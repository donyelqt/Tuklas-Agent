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
from apps.api.retrieval.context import build_context
from apps.api.retrieval.index import build_indexes
from apps.api.retrieval.normalizer import expand_range, extract_labels, normalize_label
from apps.api.retrieval.scoring import WEIGHT_DEFAULTS, ScoredNode, score_nodes
from apps.api.retrieval.tools import (
    expand_context,
    follow_reference,
    get_parent,
    get_source,
    inspect_node,
    list_children,
    search_nodes,
    select_evidence,
)

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

    hits = score_nodes(db, doc_id, query)
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

    # two-pass order: pass-1 all-zero => pass-2 ref/hier must be 0
    # (broaden-retry condition per retrieval.md §3).
    gibberish = "qqzz wumpus frobnicate"
    zero_hits = score_nodes(db, doc_id, gibberish)
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

    base = score_nodes(db, doc_id, query)
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

        rescored = {h.node_id: h for h in score_nodes(db, doc_id, query)}
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
        before = {
            h.node_id: h.components["lex"]
            for h in score_nodes(str(db_path), doc.id, query)
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

        after = {
            h.node_id: h.components["lex"]
            for h in score_nodes(str(db_path), doc.id, query)
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

    hits = score_nodes(db, doc_id, "contract requisites consent object certain")
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
        assert len(k.get("text", k.get("snippet", ""))) >= 50
        assert k.get("page_start") is not None
    scores = [k["score"] for k in kept]
    assert scores == sorted(scores, reverse=True), "kept must be ranked score-desc"

    # nothing clears the 0.15 gate -> insufficient (kept may still hold the
    # zero-score top-8 window; the gate, not a pre-filter, decides insufficiency)
    empty_hits = score_nodes(db, doc_id, "qqzz wumpus frobnicate")
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
