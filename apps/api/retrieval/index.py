"""Per-document retrieval indexes (retrieval.md §2).

Three structures, all rebuilt per document on demand:

1. FTS5 ``node_fts`` — lexical surface over titles AND body text. Scoring keeps
   the components disjoint by weighting the title column to 0 at query time
   (§3), so the table stores both columns but lex never reads titles.
2. ``ident_index`` — normalized label → node_id, from node titles. A node
   contributes its own label (e.g. "art 1318") plus every ancestor container
   label it lives under (e.g. "title iv"), because "what does Title IV say"
   must reach the articles inside Title IV. Range labels are expanded.
3. BM25 corpus over leaf-node bodies + section-intro nodes, persisted to
   ``data/indexes/{doc_id}/bm25.json`` as per-document term frequencies, lengths,
   and params, so weight tuning/eval can rebuild the same model without
   re-parsing. This artifact is the RUNTIME lex path too: scoring loads it and
   scores with rank-bm25, making lex a per-document function (FTS5's bm25()
   stats are table-global and would drift across ingests). FTS5 remains only
   the candidate MATCH prefilter.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from collections.abc import Iterator
from pathlib import Path

from apps.api.ingestion import db, errors
from apps.api.retrieval import normalizer

#: Repo-relative root for persisted per-document indexes.
INDEX_ROOT = Path("data/indexes")

#: Node types whose BODY TEXT forms the BM25 corpus (retrieval.md §2:
#: "leaf-node bodies + section-intro nodes"). A section node is included only
#: when it carries its own intro text — the tree builder stores container
#: preambles in the section node's text field, so non-empty text == intro.
_LEAF_TYPES: frozenset[str] = frozenset({"article", "subsection", "page"})
_SECTION_INTRO_TYPES: frozenset[str] = frozenset({"section"})

#: BM25 params (retrieval.md §2 defaults). Persisted into bm25.json AND loaded
#: at query time — this is the runtime-truth lex model, not just an eval record.
_BM25_PARAMS = {"k1": 1.5, "b": 0.75}


def _iter_nodes(conn: sqlite3.Connection, doc_id: str) -> Iterator[sqlite3.Row]:
    """Yield node rows for one document in tree order (depth, position)."""
    return iter(
        conn.execute(
            "SELECT id, parent_id, type, title, text, depth, position"
            " FROM nodes WHERE document_id = ? ORDER BY depth, position",
            (doc_id,),
        ).fetchall()
    )


def _label_index_rows(doc_id: str, nodes: list[sqlite3.Row]) -> list[tuple[str, str, str]]:
    """(document_id, normalized_label, node_id) rows for the ident index.

    Self labels: every label span in the node's own title, ranges expanded.
    Container labels: every label span in each ancestor's title, so the node is
    reachable via its chapter/title/book label too.
    """
    parent_of: dict[str, str | None] = {str(r["id"]): r["parent_id"] for r in nodes}
    node_by_id: dict[str, sqlite3.Row] = {str(r["id"]): r for r in nodes}
    labels_of: dict[str, list[tuple[str, float]]] = {}

    def labels_for(node_id: str) -> list[tuple[str, float]]:
        """Memoized: own-title labels plus ancestor-title labels (root-first walk)."""
        cached = labels_of.get(node_id)
        if cached is not None:
            return cached
        row = node_by_id[node_id]
        labels: list[tuple[str, float]] = list(normalizer.extract_labels(str(row["title"])))
        parent = parent_of.get(node_id)
        if parent is not None and parent in node_by_id:
            labels = labels + labels_for(parent)
        # Dedup by label, order stable.
        seen: set[str] = set()
        unique: list[tuple[str, float]] = []
        for label, level in labels:
            if label not in seen:
                seen.add(label)
                unique.append((label, level))
        labels_of[node_id] = unique
        return unique

    rows: list[tuple[str, str, str]] = []
    for row in nodes:
        for label, _level in labels_for(str(row["id"])):
            rows.append((doc_id, label, str(row["id"])))
    return rows


def build_indexes(
    db_path: str, doc_id: str, index_root: Path | str | None = None
) -> None:
    """Build/refresh all three indexes for one document (idempotent).

    ``index_root`` overrides the persisted-BM25 root (tests use a tmp dir).
    Raises ``ingestion.errors.not_found`` when the document does not exist.
    """
    with db.connect(db_path) as conn:
        doc = conn.execute(
            "SELECT id FROM documents WHERE id = ?", (doc_id,)
        ).fetchone()
        if doc is None:
            raise errors.not_found("unknown document", identifier=doc_id)

        nodes = list(_iter_nodes(conn, doc_id))

        # --- 1. FTS5: rebuild rows for this doc --------------------------
        conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS node_fts USING fts5("
            "node_id UNINDEXED, title, text, tokenize='porter ascii')"
        )
        conn.execute(
            "DELETE FROM node_fts WHERE node_id IN"
            " (SELECT id FROM nodes WHERE document_id = ?)",
            (doc_id,),
        )
        conn.executemany(
            "INSERT INTO node_fts(node_id, title, text) VALUES (?, ?, ?)",
            [(str(r["id"]), str(r["title"]), str(r["text"])) for r in nodes],
        )

        # --- 2. Identifier index ------------------------------------------
        conn.execute(
            "CREATE TABLE IF NOT EXISTS ident_index ("
            "document_id TEXT NOT NULL,"
            " normalized_label TEXT NOT NULL,"
            " node_id TEXT NOT NULL,"
            " PRIMARY KEY (document_id, normalized_label, node_id))"
        )
        conn.execute("DELETE FROM ident_index WHERE document_id = ?", (doc_id,))
        conn.executemany(
            "INSERT OR IGNORE INTO ident_index(document_id, normalized_label, node_id)"
            " VALUES (?, ?, ?)",
            _label_index_rows(doc_id, nodes),
        )

        # --- 3. BM25 corpus: leaf bodies + section-intro texts -------------
        corpus: list[tuple[str, str]] = []
        for row in nodes:
            ntype = str(row["type"])
            if ntype in _LEAF_TYPES or (ntype in _SECTION_INTRO_TYPES and str(row["text"])):
                corpus.append((str(row["id"]), str(row["text"])))
        persist_bm25(doc_id, corpus, index_root=index_root)


def persist_bm25(
    doc_id: str,
    corpus_docs: list[tuple[str, str]],
    index_root: Path | str | None = None,
) -> None:
    """Tokenize and persist a rebuildable BM25 corpus for one document.

    Payload: ``doc_id``, ``doc_ids``, ``doc_lengths``, ``term_freqs`` (aligned
    1:1 with ``doc_ids``), ``vocab``, ``avg_doc_len``, ``params``, and
    ``tokenizer``. Storing term frequencies (not just a vocab) is what makes the
    artifact a BM25 index rather than a word list; ``k1``/``b`` are persisted so
    a reload reproduces the scored model. Deterministic: sorted vocab and keys,
    no RNG.
    """
    from apps.api.retrieval.scoring import tokenize_for_bm25

    corpus_tokens = [tokenize_for_bm25(text) for _, text in corpus_docs]
    # BM25Okapi requires a non-empty vocabulary; pad an all-empty corpus with a
    # placeholder token so the persisted doc_ids/vocab stay aligned.
    if corpus_tokens and all(not toks for toks in corpus_tokens):
        corpus_tokens = [["\x00"]] * len(corpus_tokens)
    if corpus_tokens:
        # Validate the params build a real rank-bm25 model here, at index time
        # (retrieval.md §2: the persisted artifact must reproduce with
        # BM25Okapi(corpus, k1=1.5, b=0.75) without retokenizing).
        from rank_bm25 import BM25Okapi

        BM25Okapi(corpus_tokens, k1=_BM25_PARAMS["k1"], b=_BM25_PARAMS["b"])
    term_freqs = [dict(sorted(Counter(tokens).items())) for tokens in corpus_tokens]
    doc_lengths = [len(tokens) for tokens in corpus_tokens]
    payload = {
        "doc_id": doc_id,
        "doc_ids": [node_id for node_id, _ in corpus_docs],
        "doc_lengths": doc_lengths,
        "term_freqs": term_freqs,
        "vocab": sorted({token for tokens in corpus_tokens for token in tokens}),
        "avg_doc_len": (sum(doc_lengths) / len(doc_lengths)) if doc_lengths else 0.0,
        "params": dict(_BM25_PARAMS),
        "tokenizer": "fold-alnum-v2",
    }
    root = Path(index_root) if index_root is not None else INDEX_ROOT
    out_dir = root / doc_id
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "bm25.json").write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8"
    )
