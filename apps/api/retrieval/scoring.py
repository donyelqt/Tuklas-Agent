"""Two-pass, five-component node scoring (retrieval.md §3).

WHY two passes with a freeze: ``ref`` and ``hier`` are defined against the
top-scoring lex neighbors of the SAME query. Computing them per-candidate
inside pass 1 would let late-processed candidates influence early ones (order
dependence) or require a full O(n²) pairwise pass over references. Freezing
the lex-top-5 and lex-top-3 sets after pass 1 makes pass 2 a single cheap
graph lookup per candidate and keeps the total score a deterministic pure
function of (db, query, weights).

Disjoint inputs (BINDING, §3): identifier tokens are stripped from the lex
input and from the title tokens, so a heading term fires in exactly one
component. Lex scores come from the per-document persisted rank-bm25 model
(``bm25.json``, §2) so they are invariant to other documents' ingests; SQLite
FTS5 (title column weighted 0 at query time) remains only the candidate MATCH
prefilter, never the score source.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from typing import Any, cast

from apps.api.ingestion import db, errors
from apps.api.retrieval import normalizer
from apps.api.retrieval.index import INDEX_ROOT
from rank_bm25 import BM25Okapi

#: Tunable weight defaults (retrieval.md §3: "all weights 1.0").
WEIGHT_DEFAULTS: dict[str, float] = {
    "lex": 1.0,
    "ident": 1.0,
    "title": 1.0,
    "ref": 1.0,
    "hier": 1.0,
}

#: Component names, canonical order for the components dict.
COMPONENTS: tuple[str, ...] = ("lex", "ident", "title", "ref", "hier")

#: Legal weight range, inclusive (retrieval.md §3: "weights ∈ [0,2]").
_WEIGHT_MIN = 0.0
_WEIGHT_MAX = 2.0

#: FTS5 column weights: (node_id UNINDEXED, title, text) — text only. This is
#: the disjoint-inputs enforcement at the FTS5 surface, which serves ONLY as
#: the candidate MATCH prefilter; the lex score itself comes from the persisted
#: per-document rank-bm25 model (see _lex_scores).
_FTS_WEIGHTS = "0.0, 0.0, 1.0"

#: Cache of loaded per-document BM25 models, keyed by (bm25.json abs path,
#: mtime). Bounded by the number of distinct indexed documents touched this
#: process; no eviction needed for v1 because each entry is small (term-freq
#: dicts for one document's corpus) and document indexes are rebuilt rarely,
#: with the mtime key invalidating stale entries on rebuild.
_BM25_CACHE: dict[tuple[str, float], tuple[BM25Okapi, list[str]]] = {}

#: How many top-lex nodes seed the pass-2 ref walk / hier sibling set.
_LEX_TOP_FOR_REF = 5
_LEX_TOP_FOR_HIER = 3

#: Unicode word token for title/heading Jaccard and BM25 query tokenization.
_TOKEN_RE = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True)
class ScoredNode:
    """One ranked node: identity, rendered snippet, total + per-component score."""

    node_id: str
    title: str
    snippet: str
    score: float
    components: dict[str, float]
    page_start: int | None
    depth: int = 0
    position: int = 0
    #: True when the document's parse_confidence < 0.5 (retrieval.md §6). Day 3
    #: trace needs this flag from retrieval; it is computed in score_nodes and
    #: carried here so search_nodes results surface it for free.
    fallback: bool = False
    #: True when this node is the one the query IDENTIFIES: it uniquely owns an
    #: exact label (article/section) the query names. Not a component and not
    #: part of the score — it is a fact about the query, and it decides the
    #: ORDER (see ``_rank_key``). The components dict stays frozen at the five
    #: §3 components; a sixth would change the published component contract.
    identified: bool = False


#: Version tag of the BM25 tokenizer; persisted in bm25.json so stale
#: pre-fold artifacts are detectable at load time.
_TOKENIZER_VERSION = "fold-alnum-v2"


def _fold(token: str) -> str:
    """Deterministic suffix folding (plural -> singular-ish) for BM25 tokens."""
    if len(token) <= 3:
        return token
    if token.endswith("ies") and len(token) > 4:
        return token[:-3] + "y"
    for suf in ("sses", "shes", "ches", "xes", "zes"):
        if token.endswith(suf):
            return token[:-2]
    if token.endswith("s") and not token.endswith(("ss", "us", "is")):
        return token[:-1]
    return token


def tokenize_for_bm25(text: str) -> list[str]:
    """Lowercase alphanumeric tokens — the BM25 corpus/query tokenizer.

    Tokens are suffix-folded so the persisted BM25 corpus and the query path
    tokenize identically, restoring the parity FTS5's former porter stemmer
    provided; folding runs at index and query time so BM25Okapi matches.
    """
    return [_fold(t) for t in _TOKEN_RE.findall(text.lower())]


def _clamp_weights(weights: dict[str, float]) -> dict[str, float]:
    """Merge caller weights onto defaults; ValueError outside [0, 2]."""
    merged = dict(WEIGHT_DEFAULTS)
    for key, value in weights.items():
        if key not in merged:
            raise ValueError(f"unknown weight key: {key!r}")
        if value < _WEIGHT_MIN or value > _WEIGHT_MAX:
            raise ValueError(f"weight {key}={value} outside [{_WEIGHT_MIN}, {_WEIGHT_MAX}]")
        merged[key] = value
    return merged


def _rank_key(node: ScoredNode) -> tuple[int, float, int, int, str]:
    """The ONE ordering the pipeline uses: identified first, then score desc.

    retrieval.md §3 sums five bounded components, so no single one of them
    can outrank the sum of the others. Measured consequence: the `ident`
    component (capped at 1.0) lost to a competitor that accumulated
    `lex 1.0 + title 0.1 + hier 1.0 = 2.1`, so a query that NAMES a
    provision lost that provision from the top-8 evidence window whenever the
    provision's own body did not contain the query's content word. On the
    Civil Code, "What does article 1318 require?" put article 1318 at rank 10
    (out of the window) behind article 1395, a different provision.

    The fix is an ORDER, not a new weight: the node the query identifies (the
    unique owner of an exact label the query names) comes first, and
    everything else keeps the §3 score order. The score value itself is
    unchanged, so the published formula and the measured component values are
    untouched.

    This key is the single source of the order, so the retrieval ranking, the
    evidence window cut, and the context builder cannot disagree about what
    comes first.
    """
    return (
        0 if node.identified else 1,
        -node.score,
        node.depth,
        node.position,
        node.node_id,
    )


def evidence_rank_key(item: Any) -> tuple[int, float, int, int, str]:
    """The same ordering, applied to an evidence-set item (a plain dict).

    The evidence set is the only thing that crosses the retrieval boundary
    (architecture.md §2: `select_evidence` owns the ScoredNode -> dict
    boundary), so the order has to be expressible on the dict as well. The
    agent merges several sub-questions' evidence sets and cuts the merged
    list; if that cut used a different order than the one retrieval used, the
    window could again drop the provision the user named.
    """
    return (
        0 if bool(item.get("identified")) else 1,
        -float(item.get("score", 0.0)),
        int(item.get("depth", 0) or 0),
        int(item.get("position", 0) or 0),
        str(item.get("node_id", "")),
    )


def _resolve_document(conn: sqlite3.Connection, doc_id_or_slug: str) -> sqlite3.Row:
    """Row for a document id or slug; errors.not_found if absent."""
    row = conn.execute(
        "SELECT * FROM documents WHERE id = ? OR slug = ?", (doc_id_or_slug, doc_id_or_slug)
    ).fetchone()
    if row is None:
        raise errors.not_found("unknown document", identifier=doc_id_or_slug)
    return cast(sqlite3.Row, row)


def _min_max(values: dict[str, float], candidates: set[str] | None = None) -> dict[str, float]:
    """Min-max normalize per query to [0,1]; all-equal input -> all zeros.

    When ``candidates`` is given, ids missing from ``values`` enter at 0.0 so
    every candidate carries a defined lex value.
    """
    full = dict(values)
    if candidates:
        for cid in candidates:
            full.setdefault(cid, 0.0)
    if not full:
        return {}
    lo, hi = min(full.values()), max(full.values())
    if hi <= lo:
        return {key: 0.0 for key in full}
    span = hi - lo
    return {key: (value - lo) / span for key, value in full.items()}


def _load_candidates(conn: sqlite3.Connection, doc_id: str) -> list[sqlite3.Row]:
    """All nodes for one document, tree order (deterministic tiebreak base)."""
    return list(
        conn.execute(
            "SELECT id, parent_id, type, title, text, page_start, depth, position"
            " FROM nodes WHERE document_id = ? ORDER BY depth, position",
            (doc_id,),
        ).fetchall()
    )


def _load_doc_bm25(doc_id: str) -> tuple[BM25Okapi, list[str]] | None:
    """Load the per-document persisted BM25 model (bm25.json), cached.

    Returns the model plus its aligned ``doc_ids`` (1:1 with the model's
    corpus rows). Cache key is (absolute path, mtime) so a rebuild invalidates
    the stale model automatically; no eviction is needed because one entry
    holds a single document's corpus and the count of touched documents is
    small.
    """
    path = INDEX_ROOT / doc_id / "bm25.json"
    if not path.exists():
        return None
    abs_path = str(path.resolve())
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    key = (abs_path, mtime)
    cached = _BM25_CACHE.get(key)
    if cached is not None:
        return cached
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("tokenizer") != _TOKENIZER_VERSION:
        # Stale artifact (pre-fold term_freqs); fall back to the FTS5 path.
        return None
    corpus_tokens: list[list[str]] = []
    for term_freqs in payload["term_freqs"]:
        tokens: list[str] = []
        for term, freq in term_freqs.items():
            tokens.extend([term] * int(freq))
        corpus_tokens.append(tokens)
    params = payload["params"]
    model = BM25Okapi(corpus_tokens, k1=float(params["k1"]), b=float(params["b"]))
    doc_ids = [str(nid) for nid in payload["doc_ids"]]
    _BM25_CACHE[key] = (model, doc_ids)
    return model, doc_ids


def _lex_scores(
    conn: sqlite3.Connection,
    tokens: list[str],
    candidate_ids: list[str],
    doc_id: str,
) -> dict[str, float]:
    """Lex component: BM25 over the document's own corpus, min-maxed to [0,1].

    The score source is the persisted per-document rank-bm25 model
    (``data/indexes/{doc_id}/bm25.json``), so a lex score is a pure function of
    (this document's corpus, query) — ingesting another document cannot shift
    it. FTS5's internal N/avgdl are table-GLOBAL, so bm25() ranks would drift
    on any later ingest; FTS5 is therefore used only for the candidate MATCH
    prefilter, never as the score source.

    ``tokens`` are already stripped of identifier tokens (§3 disjoint inputs).
    All candidates participate in the normalization: nodes with zero matched
    terms take lex 0.0, so a query matching a single node yields 1.0 for that
    node (its raw score is the max, every other candidate is the min).
    """
    if not tokens:
        return {}
    candidates = set(candidate_ids)
    loaded = _load_doc_bm25(doc_id)
    if loaded is not None:
        # Per-document runtime-truth model: deterministic per (doc, query).
        model, doc_ids = loaded
        raw_scores = model.get_scores(tokens)
        raw = {
            nid: float(score)
            for nid, score in zip(doc_ids, raw_scores, strict=True)
            if nid in candidates
        }
        return _min_max(raw, candidates)
    # Fallback for legacy DBs with no persisted bm25.json: score via FTS5 bm25
    # with the MATCH restricted to this document. CAVEAT: FTS5's N/avgdl are
    # table-global, so these scores can still drift when another document is
    # ingested; reproducibility holds only until the next ingest.
    safe = [t.replace('"', '""') for t in tokens]
    match_expr = " OR ".join(f'"{t}"' for t in safe)
    fts_raw: dict[str, float] = {}
    for row in conn.execute(
        f"SELECT node_id, bm25(node_fts, {_FTS_WEIGHTS}) AS rank"
        f" FROM node_fts WHERE node_fts MATCH ?"
        f" AND node_id IN (SELECT id FROM nodes WHERE document_id = ?)"
        f" ORDER BY rank",
        (match_expr, doc_id),
    ):
        node_id = str(row["node_id"])
        if node_id in candidates:
            # bm25() is negative-better; invert so larger == better.
            fts_raw[node_id] = -float(row["rank"])
    return _min_max(fts_raw, candidates)


def _title_scores(
    conn: sqlite3.Connection,
    doc_id: str,
    query: str,
    query_labels: set[str],
) -> dict[str, float]:
    """Jaccard heading-token overlap, [0,1]; label tokens stripped (disjoint)."""
    qwords = set(tokenize_for_bm25(query)) - query_labels
    if not qwords:
        return {}
    rows = conn.execute(
        "SELECT id, title FROM nodes WHERE document_id = ?", (doc_id,)
    ).fetchall()
    raw: dict[str, float] = {}
    for row in rows:
        title_words = set(tokenize_for_bm25(str(row["title"])))
        title_words -= query_labels
        if not title_words:
            continue
        union = qwords | title_words
        raw[str(row["id"])] = len(qwords & title_words) / len(union) if union else 0.0
    return raw


def _row_owns(row: Any) -> bool:
    """True when this ident_index row says the node OWNS the label.

    The ``level`` column records HOW the node carries the label: 1.0 when it
    owns it (the node's own heading), 0.5 when it inherits it from an
    ancestor. Only an owner can be the identified node.

    The column is read by NAME, not by membership: a ``sqlite3.Row`` is a
    sequence of VALUES, so ``"level" in row`` tests the values and is always
    False. Getting this wrong makes every node look like an owner. A
    database built before the column existed is read as "owns it", which is
    the behaviour before ADR-005.
    """
    keys = row.keys() if hasattr(row, "keys") else ()
    if "level" not in keys:
        return True
    return float(row["level"]) >= 1.0


def _ident_lookup(
    conn: sqlite3.Connection,
    doc_id: str,
    query_labels: list[tuple[str, float]],
) -> tuple[dict[str, float], set[str]]:
    """ident component from ident_index, and the nodes the query IDENTIFIES.

    The ident component keeps retrieval.md §3's levels: 1.0 for a node that
    OWNS the exact label the query names, 0.5 for a node that carries a
    container label (its own or inherited), 0.0 otherwise. The level now
    comes from the ident_index row, which records whether the node owns the
    label or inherits it, so a node that merely lives under "SECTION 1" is
    0.5 on "sec 1" while the section node itself is 1.0.

    The second return value is the set of nodes the query IDENTIFIES: the
    node that uniquely owns an EXACT label the query names. A container label
    never identifies, because container numbers repeat ("CHAPTER 2" occurs
    34 times in the Civil Code), and an exact label owned by more than one
    node identifies none of them. See ``_identified_nodes``.
    """
    if not query_labels:
        return {}, set()
    label_levels = {label: level for label, level in query_labels}
    placeholders = ",".join("?" * len(label_levels))
    rows = conn.execute(
        f"SELECT normalized_label, node_id, level FROM ident_index"
        f" WHERE document_id = ? AND normalized_label IN ({placeholders})",
        (doc_id, *label_levels.keys()),
    ).fetchall()
    raw: dict[str, float] = {}
    owners: dict[str, list[str]] = {}
    for row in rows:
        label = str(row["normalized_label"])
        node_id = str(row["node_id"])
        label_level = label_levels.get(label, 0.0)
        if label_level not in (1.0, 0.5):
            continue
        # The ident component's VALUE is the label's own level, exactly as
        # retrieval.md §3 states: "1.0 exact normalized article id, 0.5
        # chapter/title-level id, else 0". It is NOT downgraded for a node
        # that inherits the label, because the component does not say "this
        # node IS the provision" - it says "this label is in play". Changing
        # the value would change every published ident and score, which
        # §3 forbids without a recorded tuning run.
        value = label_level
        if value > raw.get(node_id, 0.0):
            raw[node_id] = value
        # The ident_index `level` column answers a DIFFERENT question: who
        # OWNS the label. Only an owner can be the identified node, so the
        # precedence predicate reads the level and the component does not.
        # The column is read by NAME: a sqlite3.Row supports `row["level"]`
        # but NOT `"level" in row`, which tests the row's VALUES and is
        # always False.
        if value >= 1.0 and _row_owns(row):
            owners.setdefault(label, []).append(node_id)
    identified = {
        node_id
        for label, node_ids in owners.items()
        if len(node_ids) == 1
        for node_id in node_ids
    }
    return raw, identified


def _reachable_within_2(
    conn: sqlite3.Connection, doc_id: str, seeds: set[str]
) -> set[str]:
    """Node ids reachable within 2 references_ hops from any seed.

    Edges are resolved (target_node_id NOT NULL) and scoped to this document.
    Seeds are NOT included unless a walk returns to them.
    """
    found: set[str] = set()
    frontier: set[str] = seeds
    for _hop in range(2):
        if not frontier:
            break
        placeholders = ",".join("?" * len(frontier))
        rows = conn.execute(
            "SELECT r.target_node_id FROM references_ r"
            " JOIN nodes n ON n.id = r.target_node_id"
            f" WHERE r.source_node_id IN ({placeholders})"
            " AND r.target_node_id IS NOT NULL AND n.document_id = ?",
            (*frontier, doc_id),
        ).fetchall()
        frontier = {str(r["target_node_id"]) for r in rows} - found
        found |= frontier
    return found


def _sibling_parents(conn: sqlite3.Connection, node_ids: set[str]) -> set[str | None]:
    """parent_id values of the given nodes (None means document root level)."""
    if not node_ids:
        return set()
    placeholders = ",".join("?" * len(node_ids))
    rows = conn.execute(
        f"SELECT DISTINCT parent_id FROM nodes WHERE id IN ({placeholders})",
        tuple(node_ids),
    ).fetchall()
    return {row["parent_id"] for row in rows}


def score_nodes(
    db_path: str,
    doc_id: str,
    query: str,
    weights: dict[str, float] = WEIGHT_DEFAULTS,
) -> list[ScoredNode]:
    """Rank all nodes of one document; retrieval.md §3 exactly.

    Pass 1 computes lex/ident/title and freezes the lex-top-5 and lex-top-3
    sets; pass 2 computes ref/hier against those frozen sets. Lex scores come
    from the persisted per-document rank-bm25 model (bm25.json), so they are
    invariant to other documents' ingests. With an all-zero lex layer, ref/hier
    are forced to 0 so the broaden-retry precondition holds. Deterministic: no
    RNG, stable tiebreak on (depth, position, id).
    """
    w = _clamp_weights(weights)
    with db.connect(db_path) as conn:
        doc = _resolve_document(conn, doc_id)
        real_doc_id = str(doc["id"])
        parse_confidence = float(doc["parse_confidence"])
        fallback = parse_confidence < 0.5
        candidates = _load_candidates(conn, real_doc_id)
        if not candidates:
            return []
        by_id = {str(r["id"]): r for r in candidates}

        # --- Disjoint-inputs token surgery --------------------------------
        query_labels = normalizer.extract_labels(query)
        query_label_tokens = normalizer.label_tokens(query)
        lex_tokens = [
            token for token in tokenize_for_bm25(query) if token not in query_label_tokens
        ]

        # --- PASS 1 --------------------------------------------------------
        identified: set[str] = set()
        if fallback:
            # retrieval.md §6: flat fallback — ident/title/hier are 0; lex runs
            # over page-node texts (node_fts holds every node; page rows ARE
            # the lex surface, so FTS5 over text covers the page corpus).
            lex = _lex_scores(conn, lex_tokens, [str(r["id"]) for r in candidates], real_doc_id)
            ident: dict[str, float] = {}
            title: dict[str, float] = {}
        else:
            lex = _lex_scores(conn, lex_tokens, [str(r["id"]) for r in candidates], real_doc_id)
            ident, identified = _ident_lookup(conn, real_doc_id, query_labels)
            title = _title_scores(conn, real_doc_id, query, query_label_tokens)

        lex_top_sorted = sorted(
            lex.items(), key=lambda kv: (-kv[1], by_id[kv[0]]["depth"], by_id[kv[0]]["position"], kv[0])
        )
        lex_top5 = {node_id for node_id, _ in lex_top_sorted[:_LEX_TOP_FOR_REF]}
        lex_top3 = {node_id for node_id, _ in lex_top_sorted[:_LEX_TOP_FOR_HIER]}

        # --- PASS 2 (frozen sets) -----------------------------------------
        if not lex_top5 or all(v == 0.0 for v in lex.values()):
            # All-zero lex: ref/hier forced 0 (broaden-retry precondition §3).
            ref: dict[str, float] = {}
            hier: dict[str, float] = {}
        elif fallback:
            # §6: ref runs only on resolved edges — seeds from lex, same rule.
            reachable = _reachable_within_2(conn, real_doc_id, lex_top5)
            ref = {node_id: 1.0 for node_id in reachable}
            hier = {}
        else:
            reachable = _reachable_within_2(conn, real_doc_id, lex_top5)
            ref = {node_id: 1.0 for node_id in reachable}
            parent_ids = _sibling_parents(conn, lex_top3)
            hier = {}
            for node_id, node in by_id.items():
                if node["parent_id"] in parent_ids:
                    hier[node_id] = 1.0

        # --- Combine --------------------------------------------------------
        scored: list[ScoredNode] = []
        for node in candidates:
            node_id = str(node["id"])
            comps = {
                "lex": lex.get(node_id, 0.0),
                "ident": ident.get(node_id, 0.0),
                "title": title.get(node_id, 0.0),
                "ref": ref.get(node_id, 0.0),
                "hier": hier.get(node_id, 0.0),
            }
            total = sum(w[name] * comps[name] for name in COMPONENTS)
            scored.append(
                ScoredNode(
                    node_id=node_id,
                    title=str(node["title"]),
                    snippet=str(node["text"])[:400],
                    score=total,
                    components=comps,
                    page_start=node["page_start"],
                    depth=int(node["depth"]),
                    position=int(node["position"]),
                    fallback=fallback,
                    identified=node_id in identified,
                )
            )
        # Deterministic order: identified first, then score desc, then tree
        # position (depth, position, id).
        scored.sort(key=_rank_key)
        return scored
