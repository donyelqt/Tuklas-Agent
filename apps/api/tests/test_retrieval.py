"""Day-2 retrieval test suite (docs/PLAN.md Day 2, retrieval.md §3–§8).

RED phase: apps/api/retrieval/* does not exist yet; every test below fails at
import until the engine lands. Programmed strictly against the contracted
signatures in docs/retrieval.md §3–§5 and PLAN.md Day 2.

State-based assertions only — no poking at engine internals.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from apps.api.api.app import create_app
from apps.api.ingestion.service import ingest_pdf
from apps.api.retrieval.api import (
    expand_context,
    follow_reference,
    get_parent,
    get_source,
    inspect_node,
    list_children,
    search_nodes,
    select_evidence,
)
from apps.api.retrieval.context import build_context
from apps.api.retrieval.index import build_indexes
from apps.api.retrieval.normalizer import (
    expand_range,
    extract_labels,
    label_tokens,
    normalize_label,
)
from apps.api.retrieval.scoring import WEIGHT_DEFAULTS, ScoredNode, score_nodes

REPO_ROOT = Path(__file__).resolve().parents[3]
CORPUS = REPO_ROOT / "data" / "documents" / "civil-code-ra386-icnl.pdf"
SLUG = "civil-code-public"

DOC_1318 = f"node_{SLUG}_article_1318"
COMPONENT_KEYS = {"lex", "ident", "title", "ref", "hier"}


def _write_pdf(path: Path, pages: list[str]) -> None:
    """Minimal synthetic-PDF writer (same shape as test_ingest._write_pdf)."""
    import fitz

    doc = fitz.open()
    for text in pages:
        page = doc.new_page()
        if text:
            page.insert_text((72, 72), text, fontsize=12)
    doc.save(path)
    doc.close()


# ---------------------------------------------------------------------------
# Fixture: one real ingest + indexes for the whole module
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def civil_code(
    tmp_path_factory: pytest.TempPathFactory, request: pytest.FixtureRequest
) -> SimpleNamespace:
    """Ingest the real Civil Code once; build per-document indexes once."""
    if not os.path.exists(CORPUS):
        pytest.skip("corpus file missing: civil-code-ra386-icnl.pdf")
    db_path = tmp_path_factory.mktemp("retrieval") / "civil-code.db"
    index_root = tmp_path_factory.mktemp("retrieval-indexes")
    doc = ingest_pdf(str(CORPUS), slug=SLUG, db_path=str(db_path))
    build_indexes(str(db_path), doc.id, index_root=index_root)
    # The runtime lex path scores from the persisted per-document model at
    # scoring.INDEX_ROOT; point it at this module's tmp index root so every
    # test exercises the same (per-document) path production uses.
    import apps.api.retrieval.scoring as _scoring

    _original_root = _scoring.INDEX_ROOT
    _scoring.INDEX_ROOT = index_root
    request.addfinalizer(lambda: setattr(_scoring, "INDEX_ROOT", _original_root))
    return SimpleNamespace(db=str(db_path), doc_id=doc.id, slug=SLUG)


def _rows(db_path: str, sql: str, params: tuple = ()) -> list[dict]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def _node(db_path: str, node_id: str) -> dict:
    rows = _rows(db_path, "SELECT * FROM nodes WHERE id = ?", (node_id,))
    assert rows, f"node {node_id} not present in tree"
    return rows[0]


def _seed_reference(db_path: str, ref_id: str, source: str, target: str | None, text: str) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO references_ (id, source_node_id, target_node_id, reference_text)"
            " VALUES (?, ?, ?, ?)",
            (ref_id, source, target, text),
        )
        conn.commit()
    finally:
        conn.close()


def _seed_node(db_path: str, node_id: str, text: str, page_start: int | None = 1) -> dict:
    """Insert a synthetic leaf directly (document membership by real doc_id)."""
    doc_id = _rows(db_path, "SELECT id FROM documents LIMIT 1")[0]["id"]
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO nodes (id, document_id, parent_id, type, title, text,"
            " page_start, page_end, depth, position) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (node_id, doc_id, None, "page", "synthetic probe", text,
             page_start, page_start, 1, 10_000),
        )
        conn.commit()
    finally:
        conn.close()
    return _node(db_path, node_id)


# ---------------------------------------------------------------------------
# 1. Normalizer golden (retrieval.md §4)
# ---------------------------------------------------------------------------


def test_retrieval_normalizer_golden() -> None:
    # 'Art. 1318' / 'Article 1318' / '§1318' all canonicalize identically.
    assert normalize_label("Art. 1318") == "art 1318"
    assert normalize_label("Article 1318") == "art 1318"
    assert normalize_label("§1318") == "art 1318"
    assert normalize_label("Chapter 2") == "ch 2"
    assert normalize_label("TITLE IV") == "title iv"
    # digits preserved
    assert "1318" in normalize_label("Art. 1318")
    assert normalize_label("Section 45") == "sec 45"
    # letter/parenthesized suffixes survive canonicalization (distinct labels)
    assert normalize_label("Art. 2000(a)") == "art 2000a"
    assert normalize_label("Article 2000a") == "art 2000a"
    # ranges expand
    assert expand_range("1318-1320") == ["1318", "1319", "1320"]
    # a bare § sign bridges to BOTH section label families (retrieval.md §4):
    # §-printed and word-printed provisions resolve to the same ident component.
    labels = dict(extract_labels("§ 1318"))
    assert labels.get("art 1318") == 1.0
    assert labels.get("sec 1318") == 1.0
    # 'subchapter' canonicalizes and ranks at the 0.5 container level like 'ch'.
    assert normalize_label("Subchapter V") == "subch v"
    sub_labels = dict(extract_labels("Subchapter V of chapter 11"))
    assert sub_labels.get("subch v") == 0.5
    assert sub_labels.get("ch 11") == 0.5


# ---------------------------------------------------------------------------
# 2. Disjoint scoring inputs + two-pass ordering (retrieval.md §3)
# ---------------------------------------------------------------------------


def test_retrieval_scoring_disjoint_inputs(civil_code: SimpleNamespace) -> None:
    db, doc_id = civil_code.db, civil_code.doc_id
    query = "Article 1318 meeting of minds between parties"

    hits, _lex_path = score_nodes(db, doc_id, query)
    assert hits, "scoring returned no candidates"

    by_id = {h.node_id: h for h in hits}
    assert DOC_1318 in by_id, "exact identifier must surface the article node"
    gold = by_id[DOC_1318]

    # all five components present on every hit
    for hit in hits:
        assert set(hit.components) == COMPONENT_KEYS

    # §6 fallback flag surfaces on every ScoredNode; the real Civil Code ingest
    # parses above threshold, so every search_nodes hit must carry fallback=False.
    assert all(h.fallback is False for h in search_nodes("contract", doc_id, db_path=db))

    # ident fires for the exact-id node and for nobody without an id match
    assert gold.components["ident"] > 0
    body_only = [h for h in hits if h.components["ident"] == 0]
    assert any(h.components["lex"] > 0 for h in body_only), \
        "body-term matches must fire lex alone (ident untouched)"

    # disjointness: the identifier token must not double-fire lex. A node whose
    # BODY carries the query's substantive terms ('meeting of minds') must
    # out-lex the gold node, whose own body lacks that phrase — proving the
    # id/title tokens were stripped from the BM25 input.
    phrase_rows = _rows(
        db,
        "SELECT id, text FROM nodes WHERE document_id = ? AND text LIKE '%meeting of minds%'",
        (doc_id,),
    )
    phrase_ids = {r["id"] for r in phrase_rows} - {DOC_1318}
    phrase_hits = [by_id[i] for i in phrase_ids if i in by_id]
    assert phrase_hits, "corpus must contain a body node with 'meeting of minds'"
    best_body_lex = max(h.components["lex"] for h in phrase_hits)
    assert best_body_lex > 0
    assert gold.components["lex"] < best_body_lex, \
        "lex of the exact-id node must not be inflated by its identifier/title tokens"

    # weight clamp: out-of-range weights rejected, defaults all 1.0
    assert WEIGHT_DEFAULTS == {"lex": 1.0, "ident": 1.0, "title": 1.0, "ref": 1.0, "hier": 1.0}
    with pytest.raises(ValueError):
        score_nodes(db, doc_id, query, weights={"lex": 3.0})
    with pytest.raises(ValueError):
        score_nodes(db, doc_id, query, weights={"hier": -0.1})
    # The identifier's own token must not reach the lex input. This is the
    # disjoint-inputs rule in its most direct form, and it is easy to break
    # silently: returning match objects instead of strings from the tokenizer
    # makes the set non-empty for the wrong reason, the token survives into
    # the BM25 query, and the identifier fires TWICE (ident and lex).
    consumed = label_tokens("What does article 1318 require?")
    assert consumed == {"article", "1318"}, (
        f"label_tokens must yield the consumed words, got {consumed!r}"
    )
    assert all(isinstance(token, str) for token in consumed), (
        "label_tokens must yield strings, not match objects"
    )
    # two-pass order: pass-1 all-zero => pass-2 ref/hier must be 0
    # (broaden-retry condition per retrieval.md §3).
    gibberish = "qqzz wumpus frobnicate"
    zero_hits, _lp = score_nodes(db, doc_id, gibberish)
    for hit in zero_hits:
        c = hit.components
        if c["lex"] == 0 and c["ident"] == 0 and c["title"] == 0:
            assert c["ref"] == 0 and c["hier"] == 0, (
                f"pass-2 fired for an all-zero pass-1 node: {hit.node_id}"
            )


# ---------------------------------------------------------------------------
# 3. Two-pass ref/hier against frozen pass-1 sets (retrieval.md §3)
# ---------------------------------------------------------------------------


def test_retrieval_two_pass_ref_hier(civil_code: SimpleNamespace) -> None:
    db, doc_id = civil_code.db, civil_code.doc_id
    query = "Article 1318 meeting of minds between parties"

    base, _lp = score_nodes(db, doc_id, query)
    assert base

    # frozen pass-1 sets, reconstructed from the reported lex components
    lex_ranked = sorted(base, key=lambda h: h.components["lex"], reverse=True)
    top3 = [h.node_id for h in lex_ranked[:3]]
    top5 = [h.node_id for h in lex_ranked[:5]]

    # hier: a sibling (same parent) of a pass-1 lex-top-3 node must score hier>0
    top3_parents = {_node(db, nid)["parent_id"] for nid in top3}
    siblings = [
        h for h in base
        if h.node_id not in top3
        and _node(db, h.node_id)["parent_id"] in top3_parents
    ]
    assert any(h.components["hier"] > 0 for h in siblings), \
        "siblings of lex-top-3 nodes must receive the hier bonus"

    # ref: seed edges from a lex-top-5 node — one direct, one 2-hop chain —
    # to targets that currently have ref == 0, then re-score.
    source = top5[0]
    targets = [h.node_id for h in base if h.components["ref"] == 0 and h.node_id != source]
    assert len(targets) >= 2, "need fresh ref-zero targets to observe the ref bonus"
    direct, second_hop = targets[0], targets[1]

    existing = _rows(db, "SELECT id FROM references_")
    base_n = len(existing)
    probe_refs = [f"probe-ref-{base_n + 1}", f"probe-ref-{base_n + 2}"]
    try:
        _seed_reference(db, probe_refs[0], source, direct,
                        "pursuant to the referenced provision")
        _seed_reference(db, probe_refs[1], direct, second_hop,
                        "see the cross-referenced article")
        # 2-hop chain: source -> direct -> second_hop

        rescored_hits, _lp = score_nodes(db, doc_id, query)
        rescored = {h.node_id: h for h in rescored_hits}
        assert rescored[direct].components["ref"] > 0, \
            "1-hop reachable node from a lex-top-5 seed must score ref>0"
        assert rescored[second_hop].components["ref"] > 0, \
            "2-hop reachable node from a lex-top-5 seed must score ref>0"
    finally:
        conn = sqlite3.connect(db)
        try:
            conn.executemany(
                "DELETE FROM references_ WHERE id = ?", [(rid,) for rid in probe_refs]
            )
            conn.commit()
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# 3b. Per-document lex reproducibility (retrieval.md §2 + PRD: re-run
# reproduces retrieval exactly)
# ---------------------------------------------------------------------------


def test_retrieval_lex_per_document(tmp_path: Path) -> None:
    """Lex components are invariant to a second document's ingest.

    FTS5's bm25() stats (N, avgdl) are table-GLOBAL, so scoring lex through
    FTS5 would shift doc A's scores when doc B lands. The runtime lex path
    scores through the persisted per-document rank-bm25 model (bm25.json)
    instead; this test pins that: capturing score_nodes('contract') for three
    nodes, ingesting a second synthetic document, and re-scoring must leave
    the lex components bit-for-bit identical.
    """
    import apps.api.retrieval.scoring as scoring_module
    from apps.api.ingestion.service import ingest_pdf

    if not os.path.exists(CORPUS):
        pytest.skip("corpus file missing: civil-code-ra386-icnl.pdf")
    db_path = tmp_path / "lex-per-doc.db"
    index_root = tmp_path / "indexes"
    doc = ingest_pdf(str(CORPUS), slug=SLUG, db_path=str(db_path))
    build_indexes(str(db_path), doc.id, index_root=index_root)

    # Score against THIS test's index root (mirror the fixture pattern).
    _original_root = scoring_module.INDEX_ROOT
    scoring_module.INDEX_ROOT = index_root
    try:
        query = "contract requisites consent object certain"
        before_hits, _lp = score_nodes(str(db_path), doc.id, query)
        before = {
            h.node_id: h.components["lex"]
            for h in before_hits
            if h.components["lex"] > 0
        }
        assert len(before) >= 3, "fixture too small: need >= 3 lex-positive nodes"
        probe_ids = list(before)[:3]
        captured = {nid: before[nid] for nid in probe_ids}

        # Ingest a second, unrelated document into the SAME db and re-score doc 1.
        other_pdf = tmp_path / "other-doc.pdf"
        _write_pdf(
            other_pdf,
            [
                "Zebra quantum fandango provisions. " * 30,
                "Zebra quantum fandango provisions. " * 30,
            ],
        )
        other = ingest_pdf(str(other_pdf), slug="other-doc-zebra", db_path=str(db_path))
        build_indexes(str(db_path), other.id, index_root=index_root)

        after_hits, _lp = score_nodes(str(db_path), doc.id, query)
        after = {
            h.node_id: h.components["lex"]
            for h in after_hits
        }
        for nid in probe_ids:
            assert after[nid] == captured[nid], (
                f"lex for {nid} drifted after another document was ingested "
                f"({captured[nid]} -> {after[nid]}): lex path is not per-document"
            )
    finally:
        scoring_module.INDEX_ROOT = _original_root


# ---------------------------------------------------------------------------
# 4. Tool caps (retrieval.md §5, architecture.md §4)
# ---------------------------------------------------------------------------


def test_retrieval_tools_caps(civil_code: SimpleNamespace) -> None:
    db, doc_id = civil_code.db, civil_code.doc_id

    # search_nodes: default 10, hard max 20, > 20 rejected
    assert len(search_nodes("contract", doc_id, db_path=db)) <= 10
    assert len(search_nodes("contract", doc_id, top_k=20, db_path=db)) <= 20
    with pytest.raises(ValueError):
        search_nodes("contract", doc_id, top_k=21, db_path=db)

    # inspect_node: single node, text capped at 8000 chars
    full = inspect_node(DOC_1318, db_path=db)
    assert isinstance(full, dict)
    assert len(full["text"]) <= 8000

    # list_children: max 50 rows, 200-char summaries; over-limit rejected
    parent_id = _node(db, DOC_1318)["parent_id"]
    with pytest.raises(ValueError):
        list_children(parent_id, limit=51, db_path=db)
    children = list_children(parent_id, limit=50, db_path=db)
    assert len(children) <= 50
    for child in children:
        summary = child.get("text", child.get("summary", ""))
        assert len(summary) <= 200

    # get_parent: single node or None
    parent = get_parent(DOC_1318, db_path=db)
    assert parent is not None and parent["node_id"] == parent_id

    # expand_context: radius <= 2, <= 5 nodes, <= 2000 chars each
    with pytest.raises(ValueError):
        expand_context(DOC_1318, radius=3, db_path=db)
    context = expand_context(DOC_1318, radius=2, db_path=db)
    assert len(context) <= 5
    for item in context:
        assert len(item["text"]) <= 2000

    # follow_reference: NULL target -> unresolved, node None (agent must not invent)
    rows = _rows(db, "SELECT id FROM references_ WHERE target_node_id IS NULL")
    if rows:
        null_ref = rows[0]["id"]
    else:
        _seed_reference(db, "probe-ref-null", DOC_1318, None,
                        "reference to a provision not in this corpus")
        null_ref = "probe-ref-null"
    result = follow_reference(null_ref, db_path=db)
    assert result["status"] == "unresolved"
    assert result["node"] is None

    # get_source: text + pages + path, text capped at 8000 (architecture.md §4)
    long_text = (
        "Obligations arising from contracts have the force of law between the "
        "contracting parties and must be complied with in good faith. " * 200
    )[:9000]
    long_id = f"node_{SLUG}_get_source_cap_probe"
    _seed_node(db, long_id, long_text, page_start=1)
    # Attach to a real parent so hierarchy_path is populated too.
    real_parent = _node(db, DOC_1318)["parent_id"]
    conn = sqlite3.connect(db)
    try:
        conn.execute("UPDATE nodes SET parent_id = ? WHERE id = ?", (real_parent, long_id))
        conn.commit()
    finally:
        conn.close()
    src = get_source(long_id, db_path=db)
    assert src["text"]
    assert len(src["text"]) <= 8000
    assert isinstance(src["page_start"], int)
    assert isinstance(src["page_end"], int)
    assert src["hierarchy_path"]


# ---------------------------------------------------------------------------
# 5. select_evidence pipeline (retrieval.md §5)
# ---------------------------------------------------------------------------


def test_retrieval_expand_context_nearest_siblings(civil_code: SimpleNamespace) -> None:
    """expand_context picks the radius NEAREST siblings by position distance,
    not the lowest-position siblings (docstring contract).

    Seeded sibling layout (direct inserts into the fixture db): positions
    0, 1, 9, 11 around focal at position 10, all under one synthetic parent.
    radius=1 must select the position-9 sibling (distance 1), never the
    position-0 sibling (distance 10).
    """
    db = civil_code.db
    parent_id = f"node_{SLUG}_expand_probe_parent"
    focal_id = f"node_{SLUG}_expand_probe_focal"
    # Clean any leftovers so re-runs stay deterministic.
    probe_ids = [parent_id, focal_id] + [
        f"node_{SLUG}_expand_probe_sib_{pos}" for pos in (0, 1, 9, 11)
    ]
    conn = sqlite3.connect(db)
    try:
        conn.executemany(
            "DELETE FROM nodes WHERE id = ?", [(nid,) for nid in probe_ids]
        )
        # Synthetic parent at depth 0; children at depth 1 straddling focal 10.
        conn.execute(
            "INSERT INTO nodes (id, document_id, parent_id, type, title, text,"
            " page_start, page_end, depth, position) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (parent_id, civil_code.doc_id, None, "section", "expand probe parent",
             "expand probe parent body", 1, 1, 0, 50_000),
        )
        rows = [
            (f"node_{SLUG}_expand_probe_sib_0", 0),
            (f"node_{SLUG}_expand_probe_sib_1", 1),
            (f"node_{SLUG}_expand_probe_sib_9", 9),
            (focal_id, 10),
            (f"node_{SLUG}_expand_probe_sib_11", 11),
        ]
        for nid, pos in rows:
            conn.execute(
                "INSERT INTO nodes (id, document_id, parent_id, type, title, text,"
                " page_start, page_end, depth, position) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (nid, civil_code.doc_id, parent_id, "page", f"sibling probe {pos}",
                 f"sibling probe body {pos}", 1, 1, 1, pos),
            )
        conn.commit()
    finally:
        conn.close()

    try:
        context = expand_context(focal_id, radius=1, db_path=db)
        returned_ids = [item["node_id"] for item in context]
        assert focal_id in returned_ids, "focal node must always be included"
        non_focal = [nid for nid in returned_ids if nid != focal_id]
        # radius=1: parent + 1 nearest sibling (descendants budget exhausted).
        assert f"node_{SLUG}_expand_probe_sib_9" in non_focal, (
            f"nearest sibling (position 9) must be selected, got {non_focal}"
        )
        assert f"node_{SLUG}_expand_probe_sib_0" not in non_focal, (
            f"lowest-position sibling (position 0, distance 10) must never win: {non_focal}"
        )
        assert len(context) <= 5

        # radius=2: parent + the two nearest siblings (9 and 11), still not 0/1.
        context2 = expand_context(focal_id, radius=2, db_path=db)
        returned2 = [item["node_id"] for item in context2]
        non_focal2 = [nid for nid in returned2 if nid != focal_id]
        assert f"node_{SLUG}_expand_probe_sib_9" in non_focal2
        assert f"node_{SLUG}_expand_probe_sib_11" in non_focal2
        assert f"node_{SLUG}_expand_probe_sib_0" not in non_focal2
        assert f"node_{SLUG}_expand_probe_sib_1" not in non_focal2
        assert len(context2) <= 5
    finally:
        conn = sqlite3.connect(db)
        try:
            conn.executemany(
                "DELETE FROM nodes WHERE id = ?", [(nid,) for nid in probe_ids]
            )
            conn.commit()
        finally:
            conn.close()


def test_retrieval_select_evidence_pipeline(civil_code: SimpleNamespace) -> None:
    db, doc_id = civil_code.db, civil_code.doc_id
    sub_questions = ["What are the requisites of contracts?"]

    # a page-span-less node (page_start NULL) must be dropped by the pipeline
    spanless = _seed_node(
        db, f"node_{SLUG}_page_probe_spanless",
        "A contract is a meeting of minds between two parties whereby one binds "
        "himself with reference to the other to give something or to render some "
        "service, with plenty of body text so it survives the length filter.",
        page_start=None,
    )
    short = _seed_node(
        db, f"node_{SLUG}_page_probe_short", "too short", page_start=1,
    )

    hits, _lp = score_nodes(db, doc_id, "contract requisites consent object certain")
    assert hits
    fake_spanless = ScoredNode(
        node_id=spanless["id"], title=spanless["title"], snippet=spanless["text"][:200],
        score=0.9, components=dict(WEIGHT_DEFAULTS), page_start=None,
    )
    fake_short = ScoredNode(
        node_id=short["id"], title=short["title"], snippet="too short",
        score=0.95, components=dict(WEIGHT_DEFAULTS), page_start=1,
    )
    evidence = select_evidence([fake_spanless, fake_short, *hits], sub_questions, db_path=db)

    assert set(evidence) == {"kept", "insufficient", "gate"}
    kept = evidence["kept"]
    assert len(kept) <= 8
    kept_ids = {k["node_id"] for k in kept}
    assert spanless["id"] not in kept_ids, "page-span-less nodes must be dropped"
    assert short["id"] not in kept_ids, "sub-50-char nodes must be dropped"
    for k in kept:
        assert k.get("page_start") is not None
        if not k.get("identified"):
            assert len(k.get("text", k.get("snippet", ""))) >= 50, (
                "only the identified node is exempt from the length floor"
            )
    scores = [k["score"] for k in kept]
    assert scores == sorted(scores, reverse=True), "kept must be ranked score-desc"

    # nothing clears the 0.15 gate -> insufficient (kept may still hold the
    # zero-score top-8 window; the gate, not a pre-filter, decides insufficiency)
    empty_hits, _lp = score_nodes(db, doc_id, "qqzz wumpus frobnicate")
    no_evidence = select_evidence(
        empty_hits, ["quantum chromodynamics entanglement flux"], db_path=db
    )
    assert no_evidence["insufficient"] is True
    assert all(n["score"] < 0.15 for n in no_evidence["kept"])
    assert set(no_evidence["gate"]) == {"threshold", "node_ids", "per_question"}
    assert no_evidence["gate"]["threshold"] == 0.15


def test_retrieval_select_evidence_top8_window(civil_code: SimpleNamespace) -> None:
    """Top-8 window is taken BEFORE the 0.15 gate: sub-threshold nodes with
    structural validity sit in kept; only >=0.15 nodes feed the support gate."""
    db = civil_code.db

    def _hit(node_id: str, score: float) -> ScoredNode:
        return ScoredNode(
            node_id=node_id,
            title=f"window probe {node_id}",
            snippet="window probe",
            score=score,
            components={"lex": 1.0 if node_id == ids[0] else 0.0,
                        "ident": 0.0, "title": 0.0, "ref": 0.0, "hier": 0.0},
            page_start=1,
            depth=1,
            position=10_000,
        )

    scores = [0.30, 0.20, 0.18, 0.16, 0.15, 0.14, 0.13, 0.12, 0.11, 0.10]
    ids = [f"node_{SLUG}_window_probe_{i:02d}" for i in range(len(scores))]
    for node_id in ids:
        _seed_node(
            db, node_id,
            "A contract is a meeting of minds between two parties whereby one binds "
            "himself with reference to the other to give something or to render some "
            "service that is not in any manner contrary to law, morals, good customs, "
            "public order, or public policy.",
            page_start=1,
        )
    hits = [_hit(node_id, score) for node_id, score in zip(ids, scores, strict=False)]

    result = select_evidence(hits, ["q"], db_path=db)

    kept = result["kept"]
    assert len(kept) == 8
    kept_scores = {k["node_id"]: k["score"] for k in kept}
    assert all(score >= 0.10 for score in kept_scores.values())
    # 0.14 node is inside the top-8 window (9th and 10th fall off).
    assert ids[5] in kept_scores
    assert ids[8] not in kept_scores and ids[9] not in kept_scores
    # Gate carries only nodes meeting BOTH conditions: score >= 0.15 and a
    # supporter signal (lex > 0 — only the first probe has lex, so only it).
    assert set(result["gate"]["node_ids"]) == {ids[0]}
    assert result["gate"]["per_question"]["q"] == [ids[0]]


# ---------------------------------------------------------------------------
# 6. Context Builder budget contract (CONSTRAINTS.md: test_context_builder_budget)
# ---------------------------------------------------------------------------


def _big_text(n: int) -> str:
    return ("Obligations arising from contracts have the force of law between the "
            "contracting parties and must be complied with in good faith. " * n)[:2400]


def _evidence(items: list[dict], gate_ids: list[str]) -> dict:
    """Evidence-set shape per retrieval.md §5 (select_evidence return).

    [INFERENCE] gate carries `node_ids` — the gate-set that must never be dropped.
    """
    return {
        "kept": items,
        "insufficient": False,
        "gate": {"node_ids": gate_ids, "threshold": 0.15, "per_question": {}},
    }


def _part(node_id: str, text: str, score: float, depth: int = 1, position: int = 0) -> dict:
    return {"node_id": node_id, "text": text, "score": score,
            "depth": depth, "position": position}


def test_context_builder_budget(civil_code: SimpleNamespace) -> None:
    # 8 nodes x 2400 chars = 19200 > 12000 budget; gate pins the top 2.
    items = [
        _part(f"probe-n{i}", _big_text(30), round(0.9 - 0.1 * i, 2))
        for i in range(8)
    ]
    gate_ids = [items[0]["node_id"], items[1]["node_id"]]
    evidence = _evidence(items, gate_ids)

    ctx = build_context(evidence, budget_chars=12000)
    assert set(ctx) == {"parts", "total_chars", "context_truncated"}
    assert ctx["total_chars"] <= 12000
    assert ctx["context_truncated"] is True

    parts = ctx["parts"]
    kept_ids = [p["node_id"] for p in parts]
    for gid in gate_ids:
        assert gid in kept_ids, "gate-set nodes must never be dropped"

    # overflow drops lowest-score first: the two 0.1-score nodes are gone
    assert "probe-n7" not in kept_ids
    assert "probe-n6" not in kept_ids

    # order: score desc
    part_scores = [p["score"] for p in parts]
    assert part_scores == sorted(part_scores, reverse=True)

    # ties: shallower depth first, then position asc
    tied = [
        _part("probe-tie-deep", _big_text(2), 0.5, depth=2, position=0),
        _part("probe-tie-shallow", _big_text(2), 0.5, depth=1, position=1),
        _part("probe-tie-pos0", _big_text(2), 0.5, depth=1, position=0),
    ]
    tie_ctx = build_context(_evidence(tied, []), budget_chars=12000)
    tie_ids = [p["node_id"] for p in tie_ctx["parts"]]
    assert tie_ids.index("probe-tie-pos0") < tie_ids.index("probe-tie-shallow") \
        < tie_ids.index("probe-tie-deep"), "tie-break: depth asc, then position asc"

    # gate set ALONE exceeds budget -> proportional truncation, flag raised
    huge_gate = [
        _part("probe-gate-a", "x" * 9000, 0.99),
        _part("probe-gate-b", "y" * 9000, 0.98),
    ]
    overflow = build_context(_evidence(huge_gate, ["probe-gate-a", "probe-gate-b"]),
                             budget_chars=12000)
    assert overflow["context_truncated"] is True
    assert overflow["total_chars"] <= 12000
    assert {p["node_id"] for p in overflow["parts"]} == {"probe-gate-a", "probe-gate-b"}
    lens = [len(p["text"]) for p in overflow["parts"]]
    assert all(length < 9000 for length in lens), "gate parts must be proportionally truncated"
    # equal-length texts -> equal shares is the correct proportional output
    assert lens[0] == lens[1], "proportional: equal-length texts get equal shares"


def test_retrieval_context_proportional_truncation(civil_code: SimpleNamespace) -> None:
    """Gate-overflow truncation is proportional to text length, not equal share."""
    texts = ["x" * 8000, "y" * 4000, "z" * 200]
    gate = [
        _part("probe-prop-a", texts[0], 0.99),
        _part("probe-prop-b", texts[1], 0.98),
        _part("probe-prop-c", texts[2], 0.97),
    ]
    ctx = build_context(
        _evidence(gate, ["probe-prop-a", "probe-prop-b", "probe-prop-c"]),
        budget_chars=12000,
    )
    assert ctx["context_truncated"] is True
    assert ctx["total_chars"] <= 12000
    parts = ctx["parts"]
    assert [p["node_id"] for p in parts] == [
        "probe-prop-a",
        "probe-prop-b",
        "probe-prop-c",
    ], "all gate parts survive truncation"
    lens = [len(p["text"]) for p in parts]
    assert lens[0] > lens[1] > lens[2], "shares must follow text-length order"

    # each length within [floor-1, floor+2] of the exact proportional share
    total_len = sum(texts_len := [len(t) for t in texts])
    for length, text_len in zip(lens, texts_len, strict=True):
        exact = 12000 * text_len / total_len
        assert exact - 1 <= length <= exact + 2, (
            f"share {length} not near proportional {exact:.1f}"
        )


# ---------------------------------------------------------------------------
# 7. Golden 10-question smoke (retrieval.md §8)
# ---------------------------------------------------------------------------

GOLDEN_10Q = [
    ("What are the requisites of contracts?", f"node_{SLUG}_article_1318"),
    ("Who are the compulsory heirs?", f"node_{SLUG}_article_887"),
    ("What causes extinguish obligations by payment or performance?", f"node_{SLUG}_article_1231"),
    ("What does Article 26 say about dignity?", f"node_{SLUG}_article_26"),
    ("Does ignorance of the law excuse compliance?", f"node_{SLUG}_article_3"),
    ("Who bears liability for damages caused by employees?", f"node_{SLUG}_article_2180"),
    ("What is succession as acquisition mode?", f"node_{SLUG}_article_774"),
    ("How must every person exercise rights?", f"node_{SLUG}_article_19"),
    ("What about juvenile courts?", f"node_{SLUG}_article_361"),
    ("When a third person pays a debt against the will of the debtor, can the debtor recover?",
     f"node_{SLUG}_article_1425"),
]


@pytest.mark.parametrize(("question", "gold_node"), GOLDEN_10Q)
def test_retrieval_golden_10q_smoke(
    civil_code: SimpleNamespace, question: str, gold_node: str
) -> None:
    # gold ids must be real nodes of the fixed tree
    _node(civil_code.db, gold_node)

    hits = search_nodes(question, civil_code.doc_id, top_k=10, db_path=civil_code.db)
    top5_ids = [h.node_id for h in hits[:5]]
    assert gold_node in top5_ids, (
        f"golden miss: {question!r} -> gold {gold_node} not in top-5 "
        f"(got {top5_ids})"
    )


# ---------------------------------------------------------------------------
# 7b. A question that NAMES a provision (the named-provision defect)
# ---------------------------------------------------------------------------

#: The defect this pins: a query that names a provision AND carries other
#: content words used to lose the named provision from the top-8 evidence
#: window, because §3's five bounded components are SUMMED and the `ident`
#: component (capped at 1.0) could not outrank a competitor that accumulated
#: `lex 1.0 + title 0.1 + hier 1.0 = 2.1`. Measured on the Civil Code before
#: the fix: "What does article 1318 require?" put article 1318 at rank 10, out
#: of the window, behind article 1395 - a DIFFERENT provision. The golden
#: 10Q never caught it because none of its questions name a provision that
#: way.
NAMED_PROVISION_QUESTIONS = [
    ("What does article 1318 require?", f"node_{SLUG}_article_1318"),
    ("What does article 2124 provide?", f"node_{SLUG}_article_2124"),
    ("What are the requirements of article 1793?", f"node_{SLUG}_article_1793"),
    ("What does Article 26 say about dignity?", f"node_{SLUG}_article_26"),
]


@pytest.mark.parametrize(("question", "named_node"), NAMED_PROVISION_QUESTIONS)
def test_retrieval_named_provision_ranks_first(
    civil_code: SimpleNamespace, question: str, named_node: str
) -> None:
    """The provision the query names is the FIRST hit, not merely a hit.

    Asserts rank 1, not membership in the top-5: the defect was not that the
    named node was absent, it was that it ranked BELOW nodes that matched
    only the query's content words. A top-5 assertion would pass against the
    broken implementation for the cases that landed at rank 5-7.
    """
    _node(civil_code.db, named_node)

    hits = search_nodes(question, civil_code.doc_id, top_k=10, db_path=civil_code.db)
    assert hits, f"no hits for {question!r}"
    assert hits[0].node_id == named_node, (
        f"{question!r} -> the named provision must rank first, got "
        f"{[(h.node_id, round(h.score, 3)) for h in hits[:3]]}"
    )
    # The node must be marked as the one the query identifies, so every
    # downstream cut (evidence window, context builder) can respect it.
    assert hits[0].identified is True, (
        f"{question!r} -> the named provision must carry the identified flag"
    )
    # And the ident component must still be the §3 value: 1.0 for the node
    # that owns the exact label.
    assert hits[0].components["ident"] == 1.0
    # The components contract is frozen at five keys.
    assert set(hits[0].components) == COMPONENT_KEYS


@pytest.mark.parametrize(("question", "named_node"), NAMED_PROVISION_QUESTIONS)
def test_retrieval_named_provision_survives_the_evidence_window(
    civil_code: SimpleNamespace, question: str, named_node: str
) -> None:
    """The named provision is IN the top-8 evidence window, and first in it.

    The window is what the model actually sees. A named provision that ranks
    first but is cut from the window is exactly the reported failure: the
    model answers about the word-match and cites a different provision.
    """
    hits = search_nodes(question, civil_code.doc_id, top_k=20, db_path=civil_code.db)
    evidence = select_evidence(hits, [question], db_path=civil_code.db)
    kept = evidence["kept"]
    assert kept, f"no evidence kept for {question!r}"
    assert kept[0]["node_id"] == named_node, (
        f"{question!r} -> the named provision must lead the evidence window, got "
        f"{[k['node_id'] for k in kept[:3]]}"
    )
    # It must be a SUPPORTER, not merely present: the gate has to be able to
    # pass on it.
    supporters = evidence["gate"]["per_question"][question]
    assert named_node in supporters, (
        f"{question!r} -> the named provision must support the sub-question"
    )
    assert evidence["insufficient"] is False


def test_retrieval_named_provision_leads_the_prompt(
    civil_code: SimpleNamespace,
) -> None:
    """The named provision is the FIRST part of the prompt context.

    The model reads the context in order, so the provision the user named
    must be the first thing it sees - not the fifth.
    """
    question = "What does article 1318 require?"
    named_node = f"node_{SLUG}_article_1318"
    hits = search_nodes(question, civil_code.doc_id, top_k=20, db_path=civil_code.db)
    evidence = select_evidence(hits, [question], db_path=civil_code.db)
    context = build_context(evidence)
    parts = context["parts"]
    assert parts, "the prompt context is empty"
    assert parts[0]["node_id"] == named_node, (
        f"the named provision must lead the prompt, got "
        f"{[p['node_id'] for p in parts[:3]]}"
    )
    # The prompt part's score is the §3 score; the order is not a score bump.
    kept_scores = {k["node_id"]: k["score"] for k in evidence["kept"]}
    assert parts[0]["score"] == kept_scores[named_node]


def test_retrieval_ident_index_resolves_one_node_per_article(
    civil_code: SimpleNamespace,
) -> None:
    """An article label resolves to the article that OWNS it, at level 1.0.

    The ident index used to carry every label span found anywhere in a node's
    title, plus every ancestor's labels. Measured on the Civil Code: "art 57"
    resolved to 404 nodes and "sec 1" to 230, because the parser's near-miss
    path turned one body-text sentence into a 'part' heading and 402 real
    articles inherited a label they never owned. A label that names hundreds
    of nodes cannot identify anything, so this is the property the identifier
    precedence depends on.
    """
    conn = sqlite3.connect(civil_code.db)
    conn.row_factory = sqlite3.Row
    try:
        for label, expected in (
            ("art 1318", f"node_{SLUG}_article_1318"),
            ("art 26", f"node_{SLUG}_article_26"),
            ("art 1793", f"node_{SLUG}_article_1793"),
            ("art 57", f"node_{SLUG}_article_57"),
        ):
            rows = conn.execute(
                "SELECT node_id, level FROM ident_index WHERE document_id = ?"
                " AND normalized_label = ? AND level >= 1.0",
                (civil_code.doc_id, label),
            ).fetchall()
            owners = [str(r["node_id"]) for r in rows]
            assert owners == [expected], (
                f"{label!r} must be owned by exactly {expected!r}, got {owners}"
            )
    finally:
        conn.close()


def test_retrieval_own_identity_ignores_cited_provisions() -> None:
    """A node's identity is its own heading, not a provision it CITES.

    "Article 244. The provisions of articles 226 to 228 and 235 to 236 ..."
    cites 226 and 228; it is not them. The own-identity rule reads the
    leading heading only, so a citation cannot make a node the owner of
    another provision.
    """
    from apps.api.retrieval.normalizer import own_identity

    assert own_identity(
        "article", "Article 1318. There is no contract unless..."
    ) == ("art 1318", 1.0)
    assert own_identity(
        "article", "Article 244. The provisions of articles 226 to 228 and 235 to 236."
    ) == ("art 244", 1.0)
    # A container label is 0.5 and belongs to its own family.
    assert own_identity("chapter", "CHAPTER 2") == ("ch 2", 0.5)
    assert own_identity("title", "TITLE IV") == ("title iv", 0.5)
    assert own_identity("book", "BOOK I") == ("book i", 0.5)
    # The node type must match the label family: a chapter cannot own an
    # article, and an article cannot own a chapter.
    assert own_identity("chapter", "Article 1318. There is no contract...") is None
    assert own_identity("article", "CHAPTER 2") is None
    # Body text owns nothing.
    assert own_identity(
        "article", "at the expense of the usufructuary. (500)"
    ) is None
    assert own_identity("part", "party wall supports a building belonging to him. (575)") is None


#: The defect this pins: a query naming SEVERAL provisions captured only
#: the FIRST one. "Compare articles 1318 and 2124" yielded the label
#: "art 1318" alone, so article 2124 was invisible to the identifier
#: channel - and the planner then split the question into "Compare articles
#: 1318" and "2124", where "2124" is a bare number and carries no label at
#: all. The second provision could never be identified.
COORDINATED_PROVISION_QUESTIONS = [
    ("Compare articles 1318 and 2124", ["art 1318", "art 2124"]),
    (
        "What do articles 1318, 2124 and 26 say?",
        ["art 1318", "art 2124", "art 26"],
    ),
    ("Section 101 and 102 of this title", ["sec 101", "sec 102"]),
]


@pytest.mark.parametrize(("question", "expected"), COORDINATED_PROVISION_QUESTIONS)
def test_retrieval_coordinated_provisions_are_all_extracted(
    question: str, expected: list[str]
) -> None:
    """A coordinated list of provisions yields a label for EACH of them.

    The list is a citation, not prose: every number in it names a provision,
    and each must reach the identifier channel. A trailing "and" is the
    normal shape of the list and must not end it early.
    """
    labels = [label for label, _level in extract_labels(question)]
    for want in expected:
        assert want in labels, (
            f"{question!r} -> {want!r} must be extracted, got {labels}"
        )


def test_retrieval_coordinated_provision_is_identified(
    civil_code: SimpleNamespace,
) -> None:
    """The SECOND provision in a coordinated list is identified too.

    Extracting the label is not enough: the provision has to reach the
    evidence window and carry the identified flag, or the model never sees
    it as the thing the user named.
    """
    question = "Compare articles 1318 and 2124"
    first = f"node_{SLUG}_article_1318"
    second = f"node_{SLUG}_article_2124"
    _node(civil_code.db, first)
    _node(civil_code.db, second)

    hits = search_nodes(question, civil_code.doc_id, top_k=20, db_path=civil_code.db)
    identified = {h.node_id for h in hits if h.identified}
    assert first in identified, (
        f"{question!r} -> the first named provision must be identified, got "
        f"{sorted(identified)}"
    )
    assert second in identified, (
        f"{question!r} -> the SECOND named provision must be identified too, "
        f"got {sorted(identified)}"
    )


def test_retrieval_context_protects_the_identified_node_from_eviction() -> None:
    """The identified node is never evicted for space.

    The identifier precedence is an ORDER, not a weight: the provision the
    query names is put first in the ranking, but its total score is whatever
    its lexical and hierarchical components happen to be. It can therefore be
    the LOWEST-scoring node in the kept set - and a lowest-score-first
    eviction then drops the named provision FIRST, silently undoing the
    precedence exactly when the prompt is too large to hold everything.

    Measured before the fix: with a 250-character budget the identified node
    was evicted and the prompt led with two other nodes.
    """
    kept = [
        {"node_id": "n_high", "text": "x" * 100, "score": 0.90, "depth": 2,
         "position": 0, "identified": False},
        {"node_id": "n_mid", "text": "x" * 100, "score": 0.60, "depth": 3,
         "position": 1, "identified": False},
        {"node_id": "n_target", "text": "x" * 100, "score": 0.10, "depth": 4,
         "position": 2, "identified": True},
        {"node_id": "n_low", "text": "x" * 100, "score": 0.05, "depth": 4,
         "position": 3, "identified": False},
    ]
    # The gate protects the strong supporters, NOT the identified node: that
    # is the real case, and it is the one that used to evict it.
    evidence = {
        "kept": kept,
        "insufficient": False,
        "gate": {"threshold": 0.15, "node_ids": ["n_high", "n_mid"],
                 "per_question": {}},
    }

    for budget in (400, 350, 300, 250, 200, 150):
        context = build_context(evidence, budget_chars=budget)
        ids = [part["node_id"] for part in context["parts"]]
        assert "n_target" in ids, (
            f"the identified node must survive a {budget}-character budget, "
            f"got {ids}"
        )
        # And it still leads, because the precedence is an order.
        assert ids[0] == "n_target", (
            f"the identified node must lead the prompt at every budget, got {ids}"
        )


def test_retrieval_short_named_provision_survives_the_length_floor(
    civil_code: SimpleNamespace,
) -> None:
    """A SHORT named provision still reaches the evidence window.

    The evidence pipeline drops nodes whose text is under 50 characters. That
    floor exists to drop page-span nodes whose text is not a provision, but
    it was also dropping provisions: article 1433 is 48 characters long
    ("Article 1433. Estoppel may in pais or by deed."), so a query naming it
    ranked it first, identified it, and then threw it away - and the model
    answered about a DIFFERENT article.

    Measured on the Civil Code: 98 article and section nodes are under the
    floor, so this is not a one-off.
    """
    db, doc_id = civil_code.db, civil_code.doc_id

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT id FROM nodes WHERE document_id = ? AND type = 'article'"
            " AND length(text) < 50",
            (doc_id,),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None, "the corpus must have a provision under 50 characters"
    short_id = str(row["id"])
    number = short_id.rsplit("_", 1)[-1]

    hits = search_nodes(
        f"What does article {number} require?", doc_id, top_k=20, db_path=db
    )
    assert hits, f"no hits for article {number}"
    assert hits[0].node_id == short_id, (
        f"article {number} must rank first, got {hits[0].node_id}"
    )
    assert hits[0].identified is True, (
        f"article {number} must be identified"
    )

    evidence = select_evidence(
        hits, [f"What does article {number} require?"], db_path=db
    )
    kept_ids = {k["node_id"] for k in evidence["kept"]}
    assert short_id in kept_ids, (
        f"the named provision is {len(str(row['id']))} chars long and must "
        f"still reach the evidence window; kept {sorted(kept_ids)}"
    )


def test_retrieval_inherited_label_is_not_the_owning_level(
    civil_code: SimpleNamespace,
) -> None:
    """Ownership is decided by the level column, not by the component value.

    Two different questions, two different answers:

    - The ident COMPONENT's value is the label's own level, exactly as
      retrieval.md §3 states ("1.0 exact normalized article id, 0.5
      chapter/title-level id, else 0"). A node that carries "sec 1" scores
      ident 1.0 whether it owns the label or inherits it. Downgrading the
      inheritor would change every published ident and score, which §3
      forbids without a recorded tuning run.
    - WHO owns the label is the level column's job, and only an owner can be
      the identified node. A node that merely lives under "SECTION 1" is
      never the provision the user named.

    The trap this pins: a ``sqlite3.Row`` is a sequence of VALUES, so
    ``"level" in row`` is ALWAYS False. The column NAMES live in
    ``row.keys()``.
    """
    db, doc_id = civil_code.db, civil_code.doc_id

    hits, _lp = score_nodes(db, doc_id, "article 1318")
    by_id = {h.node_id: h for h in hits}
    owner = by_id[DOC_1318]
    assert owner.components["ident"] == 1.0, (
        "the node that owns the label must score the full ident level"
    )

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT node_id, level FROM ident_index WHERE document_id = ?"
            " AND normalized_label = 'art 1318'",
            (doc_id,),
        ).fetchall()
    finally:
        conn.close()
    assert len(rows) == 1, f"art 1318 must be owned by one node, got {len(rows)}"
    assert float(rows[0]["level"]) == 1.0

    # A CONTAINER label: a node under "CHAPTER 2" carries "ch 2", and the
    # component value is the container level, 0.5.
    hits, _lp = score_nodes(db, doc_id, "chapter 2")
    ch2 = [h for h in hits if h.components["ident"] > 0]
    assert ch2, "a chapter label must reach the nodes under it"
    for hit in ch2:
        assert hit.components["ident"] == 0.5, (
            f"{hit.node_id} carries a container label and must score 0.5, "
            f"got {hit.components['ident']}"
        )
        assert hit.identified is False, (
            "a container label is never decisive: container numbers repeat"
        )

    # An INHERITED EXACT label: the component value is still 1.0 (the
    # label's own level), but the node is NOT identified - the level column
    # says it inherits the label rather than owning it.
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        owned = conn.execute(
            "SELECT node_id FROM ident_index WHERE document_id = ?"
            " AND normalized_label = 'sec 1' AND level = 1.0 LIMIT 1",
            (doc_id,),
        ).fetchone()
        assert owned is not None, "the corpus must own a 'sec 1' label"
        owner_id = str(owned["node_id"])
        children = conn.execute(
            "SELECT id FROM nodes WHERE parent_id = ? LIMIT 5", (owner_id,)
        ).fetchall()
        inheritor_levels = conn.execute(
            "SELECT node_id, level FROM ident_index WHERE document_id = ?"
            " AND normalized_label = 'sec 1' AND level = 0.5 LIMIT 5",
            (doc_id,),
        ).fetchall()
        owner_count = conn.execute(
            "SELECT COUNT(*) FROM ident_index WHERE document_id = ?"
            " AND normalized_label = 'sec 1' AND level = 1.0",
            (doc_id,),
        ).fetchone()[0]
    finally:
        conn.close()
    assert children, f"{owner_id} must have children to inherit the label"
    assert inheritor_levels, "the corpus must have nodes inheriting 'sec 1'"

    hits, _lp = score_nodes(db, doc_id, "section 1")
    by_id = {h.node_id: h for h in hits}
    assert by_id[owner_id].components["ident"] == 1.0, (
        "the section that owns 'sec 1' must score 1.0"
    )
    # "sec 1" is printed in EVERY chapter of the Civil Code, so it is owned by
    # many nodes and identifies NONE of them. A label that names twenty
    # nodes cannot name one provision.
    assert owner_count > 1, (
        "the corpus prints 'SECTION 1' in many chapters, so the label is "
        "ambiguous and must identify no node"
    )
    assert by_id[owner_id].identified is False, (
        "an ambiguous label identifies no node: the owner is one of many"
    )
    for row in inheritor_levels:
        inheritor = str(row["node_id"])
        if inheritor not in by_id:
            continue
        assert by_id[inheritor].components["ident"] == 1.0, (
            f"{inheritor} carries the exact label 'sec 1', so the component "
            f"value is the label's own level 1.0 (retrieval.md sec 3)"
        )
        assert by_id[inheritor].identified is False, (
            f"{inheritor} only INHERITS 'sec 1' (level 0.5), so it must not "
            f"be the identified node"
        )


# ---------------------------------------------------------------------------
# 9. Product wiring: upload -> build_indexes (architecture.md §2)
# ---------------------------------------------------------------------------


def test_retrieval_indexes_built_on_upload(tmp_path: Path) -> None:
    """POST /v1/documents must build node_fts + ident_index in the same db."""
    from fastapi.testclient import TestClient

    pdf_path = tmp_path / "upload.pdf"
    _write_pdf(
        pdf_path,
        [
            f"CHAPTER {n}\n\nArt. {100 + n}. Provision text for page {n}. " * 3
            for n in range(1, 4)
        ],
    )
    db_path = tmp_path / "upload.db"
    client = TestClient(create_app(db_path=str(db_path)))

    with pdf_path.open("rb") as fh:
        resp = client.post(
            "/v1/documents",
            files={"file": ("upload.pdf", fh, "application/pdf")},
        )
    assert resp.status_code == 201, resp.text

    conn = sqlite3.connect(str(db_path))
    try:
        fts_rows = conn.execute("SELECT COUNT(*) FROM node_fts").fetchone()[0]
        ident_rows = conn.execute("SELECT COUNT(*) FROM ident_index").fetchone()[0]
    finally:
        conn.close()
    assert fts_rows > 0, "node_fts must be populated after upload"
    assert ident_rows > 0, "ident_index must be populated after upload"
