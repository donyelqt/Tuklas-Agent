"""Day-3 agent-loop test suite (PLAN.md Day 3, architecture.md sec 4).

State-based and trace-based assertions only: the frozen topology, the
12-iteration budget (the broaden-retry included, no reset), the gate verdict,
the AskResponse shape, and the SSE event sequence. No model is needed — the
deterministic stub generator renders the kept evidence as citation lines.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from apps.api.agent.ask import CANNED_ABSTENTION, iter_sse_events, run_ask
from apps.api.agent.graph import (
    MAX_SUB_QUESTIONS,
    MAX_TOOL_ITERATIONS,
    build_graph,
    run_agent,
)
from apps.api.agent.stoplist import STOPLIST, broaden
from apps.api.ingestion.service import ingest_pdf
from apps.api.retrieval.index import build_indexes

REPO_ROOT = Path(__file__).resolve().parents[3]
CORPUS = REPO_ROOT / "data" / "documents" / "civil-code-ra386-icnl.pdf"
SLUG = "civil-code-public"


@pytest.fixture(scope="module")
def civil_code(
    tmp_path_factory: pytest.TempPathFactory, request: pytest.FixtureRequest
) -> SimpleNamespace:
    """Ingest the real Civil Code once; build per-document indexes once."""
    if not os.path.exists(CORPUS):
        pytest.skip("corpus file missing: civil-code-ra386-icnl.pdf")
    db_path = tmp_path_factory.mktemp("agent") / "civil-code.db"
    index_root = tmp_path_factory.mktemp("agent-indexes")
    doc = ingest_pdf(str(CORPUS), slug=SLUG, db_path=str(db_path))
    build_indexes(str(db_path), doc.id, index_root=index_root)
    # The runtime lex path scores from the persisted per-document model at
    # scoring.INDEX_ROOT; point it at this module's tmp root so the agent
    # exercises the same path production uses.
    import apps.api.retrieval.scoring as _scoring

    original_root = _scoring.INDEX_ROOT
    _scoring.INDEX_ROOT = index_root
    request.addfinalizer(lambda: setattr(_scoring, "INDEX_ROOT", original_root))
    return SimpleNamespace(db=str(db_path), doc_id=doc.id, slug=SLUG)


# ---------------------------------------------------------------------------
# 1. Graph topology
# ---------------------------------------------------------------------------


def test_agent_graph_topology(civil_code: SimpleNamespace) -> None:
    graph = build_graph()
    inner = graph.get_graph()
    node_names = set(inner.nodes)
    for expected in {
        "query_plan",
        "search",
        "inspect",
        "expand_or_backtrack",
        "select_evidence",
        "sufficiency_gate",
        "build_context",
        "generate",
    }:
        assert expected in node_names, f"missing node: {expected}"
    edges = inner.edges
    targets = {edge[0]: edge[1] for edge in edges}
    assert targets.get("__start__") == "query_plan"
    assert targets.get("query_plan") == "search"
    assert targets.get("search") == "inspect"
    assert targets.get("inspect") == "expand_or_backtrack"
    assert targets.get("expand_or_backtrack") == "select_evidence"
    assert targets.get("select_evidence") == "sufficiency_gate"
    assert targets.get("build_context") == "generate"
    assert targets.get("generate") == "__end__"


# ---------------------------------------------------------------------------
# 2. Iteration budget: the cap holds, and the broaden-retry does not reset it
# ---------------------------------------------------------------------------


def test_agent_iteration_budget(civil_code: SimpleNamespace) -> None:
    state = run_agent(
        civil_code.doc_id,
        "qqzz wumpus frobnicatex",
        max_iterations=12,
        db_path=civil_code.db,
    )
    assert state["tool_iterations"] <= MAX_TOOL_ITERATIONS
    assert state["tool_iterations"] <= int(state.get("max_iterations", 12))
    assert state["insufficient"] is True
    # The gate failed for every sub-question.
    assert state["_gate_passed"] is False
    assert state["_gate_missing"]


def test_agent_broaden_retry_does_not_reset_budget(civil_code: SimpleNamespace) -> None:
    state = run_agent(
        civil_code.doc_id,
        "qqzz wumpus frobnicatex",
        max_iterations=12,
        db_path=civil_code.db,
    )
    broaden_entries = [
        entry for entry in state.get("trace", []) if entry.get("node") == "broaden_retry"
    ]
    assert len(broaden_entries) == 1
    # On this corpus the retry is SKIPPED (the stoplist cannot reduce a query
    # of non-stopwords), so it spends no budget - the counter is the
    # pre-gate search alone. The counter is still within the cap, and the
    # retry was attempted exactly once.
    assert broaden_entries[0]["result"] == "skipped_no_reduction"
    assert state["tool_iterations"] > 0
    assert state["tool_iterations"] <= MAX_TOOL_ITERATIONS
    assert state["broadened"] is True


def test_agent_broaden_retry_spends_budget_and_does_not_reset(
    civil_code: SimpleNamespace,
) -> None:
    """The retry must SPEND from the budget, not reset it.

    architecture.md sec 4: "max 12 tool iterations per ask, including the one
    broaden-retry below. Retry does not reset the counter."

    The retry is unreachable on this corpus (measured: a query of non-stopwords
    cannot be reduced, and a query with a stopword always passes the gate), so
    the first search is stubbed to return nothing. That forces the gate to
    fail and the retry to fire, which is the only way to observe the retry's
    effect on the counter.
    """
    from apps.api.agent import graph as agent_graph

    original = agent_graph.TOOL_TABLE["search_nodes"]
    calls: list[str] = []

    def stub_first_search(state: Any, args: Any) -> Any:
        calls.append(str(args.get("query")))
        if len(calls) == 1:
            # The first search finds nothing: the gate fails, so the retry
            # fires. Recorded exactly like a real no-hit search.
            state["tool_iterations"] = int(state.get("tool_iterations", 0)) + 1
            state["tool_results"].append(
                {
                    "tool": "search_nodes",
                    "args": args,
                    "result_ids": [],
                    "relevant": False,
                    "latency_ms": 0.0,
                }
            )
            state["trace"].append(
                {
                    "tool": "search_nodes",
                    "args": args,
                    "result_ids": [],
                    "relevant": False,
                    "branch_id": "search_nodes#1",
                    "latency_ms": 0.0,
                }
            )
            return state
        return original(state, args)

    agent_graph.TOOL_TABLE["search_nodes"] = stub_first_search
    try:
        state = run_agent(
            civil_code.doc_id,
            "What are the requisites of contracts?",
            max_iterations=12,
            db_path=civil_code.db,
        )
    finally:
        agent_graph.TOOL_TABLE["search_nodes"] = original

    # The retry fired and ran a search of its own.
    broaden_entries = [
        entry for entry in state.get("trace", []) if entry.get("node") == "broaden_retry"
    ]
    assert len(broaden_entries) == 1
    assert broaden_entries[0].get("broadened_queries"), "the retry did not run a search"
    assert len(calls) >= 2, "the retry must issue its own search"

    # The counter was NOT reset by the retry: it counted every call.
    assert state["tool_iterations"] == len(state.get("tool_results", []))
    assert state["tool_iterations"] >= 2
    assert state["tool_iterations"] <= MAX_TOOL_ITERATIONS


# ---------------------------------------------------------------------------
# 3. Sufficiency gate passes on a real question
# ---------------------------------------------------------------------------


def test_agent_sufficiency_gate_pass(civil_code: SimpleNamespace) -> None:
    state = run_agent(
        civil_code.doc_id,
        "What are the requisites of contracts?",
        db_path=civil_code.db,
    )
    assert state["insufficient"] is False
    evidence = state.get("evidence", {})
    kept = evidence.get("kept", [])
    assert len(kept) > 0
    # Every planned sub-question has at least one supporting node.
    per_question = evidence.get("gate", {}).get("per_question", {})
    for sub_question in state.get("sub_questions", []):
        assert per_question.get(sub_question), f"no supporter for {sub_question!r}"
    assert state["answer_md"] != CANNED_ABSTENTION
    # The AGENT's verdict, not just retrieval's payload: the gate passes only
    # if every planned sub-question has a supporter that is actually in the
    # kept set. Asserting on retrieval's own per_question map would not catch
    # an agent that ignored the verdict and decided on "kept is non-empty" -
    # the exact bug that let a score-0 kept set produce a fabricated answer.
    assert state["_gate_passed"] is True
    assert state["_gate_missing"] == []
    kept_ids = {
        str(item.get("node_id")) for item in kept if isinstance(item, dict)
    }
    for sub_question in state.get("sub_questions", []):
        supporters = [
            node_id
            for node_id in per_question.get(sub_question, [])
            if node_id in kept_ids
        ]
        assert supporters, f"the agent saw no supporter in the kept set for {sub_question!r}"


# ---------------------------------------------------------------------------
# 4. Gate fails -> unified abstention
# ---------------------------------------------------------------------------


def test_agent_sufficiency_gate_fail_abstention(civil_code: SimpleNamespace) -> None:
    response = run_ask(
        civil_code.doc_id, "qqzz wumpus frobnicatex", db_path=civil_code.db
    )
    assert response["insufficient"] is True
    assert response["answer_markdown"] == CANNED_ABSTENTION
    assert response["citations"] == []
    assert response["evidence_ids"] == []


# ---------------------------------------------------------------------------
# 5. Broaden-retry fires exactly once, deterministically
# ---------------------------------------------------------------------------


def test_agent_broaden_retry_is_attempted_once(civil_code: SimpleNamespace) -> None:
    # A query whose every token matches no node: the lex layer is all-zero,
    # so the gate has no supporter (retrieval.md sec 3, sec 7).
    query = "qqzz wumpus frobnicatex"
    state = run_agent(
        civil_code.doc_id, query, max_iterations=12, db_path=civil_code.db
    )
    broaden_entries = [
        entry for entry in state.get("trace", []) if entry.get("node") == "broaden_retry"
    ]
    # Exactly one retry attempt: retrieval.md sec 7 says "one broaden-retry".
    assert len(broaden_entries) == 1
    # This query is made only of non-stopwords, so the stoplist strip cannot
    # reduce it. Re-running the identical search would be provably useless
    # (retrieval is deterministic), so the retry is skipped rather than
    # spending iteration budget on a search whose answer is already known.
    assert broaden_entries[0]["result"] == "skipped_no_reduction"
    assert state["insufficient"] is True
    assert state["tool_iterations"] <= MAX_TOOL_ITERATIONS


def test_agent_broaden_retry_only_touches_failing_subquestions(
    civil_code: SimpleNamespace,
) -> None:
    # The retry must not re-search a sub-question that already has a
    # supporter: its evidence is in the kept set, and broadened hits would
    # push better nodes out of the top-8 window rather than add any.
    state = run_agent(
        civil_code.doc_id,
        "What are the requisites of contracts? qqzz wumpus frobnicatex",
        db_path=civil_code.db,
    )
    broaden_entries = [
        entry for entry in state.get("trace", []) if entry.get("node") == "broaden_retry"
    ]
    assert len(broaden_entries) <= 1
    for entry in broaden_entries:
        missing = entry.get("missing_sub_questions", [])
        queries = entry.get("broadened_queries", [])
        if not queries:
            # The retry was skipped (the stoplist could not reduce the
            # query). That is a valid outcome, but then there is nothing to
            # assert about the query set - so assert the skip itself.
            assert entry.get("result") == "skipped_no_reduction"
            continue
        # Every broadened query belongs to a sub-question that FAILED, and
        # there is exactly one broadened query per failed sub-question.
        assert len(queries) == len(missing)
        for query in queries:
            assert query in {broaden(sub) for sub in missing}


# ---------------------------------------------------------------------------
# 6. Multi-hop trace: an abandoned branch plus >=2 inspect/expand calls
# ---------------------------------------------------------------------------


def test_agent_multi_hop_trace_abandoned_branch(civil_code: SimpleNamespace) -> None:
    state = run_agent(
        civil_code.doc_id,
        "What is succession? What is the registration of chattel mortgages?",
        db_path=civil_code.db,
    )
    tool_results = state.get("tool_results", [])
    abandoned = [result for result in tool_results if result.get("relevant") is False]
    assert len(abandoned) >= 1, "expected at least one abandoned branch"
    # An abandoned branch contributes zero evidence nodes and carries a branch id.
    kept_ids = {
        str(item.get("node_id"))
        for item in state.get("evidence", {}).get("kept", [])
        if isinstance(item, dict)
    }
    branches = state.get("visited_branches", [])
    assert branches, "abandoned branches must be logged with their branch id"
    for branch in branches:
        assert branch.get("branch_id")
    # The abandoned branch's node ids never enter the kept evidence set.
    # (The `or not relevant` form this replaced was a tautology: every member
    # of `abandoned` has relevant False, so it was always True and could not
    # fail. The contract is the intersection, stated directly.)
    for result in abandoned:
        assert not set(result.get("result_ids", [])) & kept_ids, (
            "an abandoned branch must contribute zero evidence nodes"
        )
    # >= 2 inspect/expand calls before the answer.
    inspect_expand = [
        result
        for result in tool_results
        if result.get("tool") in {"inspect_node", "expand_context"}
    ]
    assert len(inspect_expand) >= 2


# ---------------------------------------------------------------------------
# 6a. A search that matched nothing is an irrelevant branch
# ---------------------------------------------------------------------------


def test_agent_search_that_matches_nothing_is_irrelevant(
    civil_code: SimpleNamespace,
) -> None:
    # search_nodes returns the document's top-k nodes even when the query
    # matches nothing (every score 0.0). Counting those as hits would make
    # every search branch relevant and hide the dead end in the trace.
    state = run_agent(
        civil_code.doc_id,
        "qqzz wumpus frobnicatex",
        db_path=civil_code.db,
    )
    searches = [
        result
        for result in state.get("tool_results", [])
        if result.get("tool") == "search_nodes"
    ]
    assert searches, "expected at least one search"
    for search in searches:
        assert search["relevant"] is False, "a non-matching search must be irrelevant"
        assert search["result_ids"] == [], "a non-matching search matched nothing"


def test_agent_relevance_ignores_a_hier_only_score(
    civil_code: SimpleNamespace,
) -> None:
    # The `hier` component scores 1.0 for any node sharing a parent with a
    # lex-top-3 node, so a structural sibling carries a full point with
    # lex=0.00, ident=0.00, title=0.00. Treating that as a query match would
    # make a search branch look relevant when the query matched nothing of
    # substance. The gate accepts exactly two signals: lex > 0 or ident > 0.
    from apps.api.agent.tools import _hit_matched

    class _Hit:
        def __init__(self, score: float, components: dict) -> None:
            self.score = score
            self.components = components

    # hier-only: a full total score, no lexical or identifier match.
    assert not _hit_matched(
        _Hit(1.0, {"lex": 0.0, "ident": 0.0, "title": 0.0, "ref": 0.0, "hier": 1.0})
    )
    # A lexical match is a match.
    assert _hit_matched(
        _Hit(2.0, {"lex": 1.0, "ident": 0.0, "title": 0.0, "ref": 0.0, "hier": 1.0})
    )
    # An identifier match is a match.
    assert _hit_matched(
        _Hit(1.0, {"lex": 0.0, "ident": 1.0, "title": 0.0, "ref": 0.0, "hier": 0.0})
    )
    # Nothing fired.
    assert not _hit_matched(
        _Hit(0.0, {"lex": 0.0, "ident": 0.0, "title": 0.0, "ref": 0.0, "hier": 0.0})
    )


def test_agent_search_that_matches_is_relevant(civil_code: SimpleNamespace) -> None:
    state = run_agent(
        civil_code.doc_id,
        "What are the requisites of contracts?",
        db_path=civil_code.db,
    )
    searches = [
        result
        for result in state.get("tool_results", [])
        if result.get("tool") == "search_nodes"
    ]
    assert searches
    assert any(search["relevant"] for search in searches)
    assert any(search["result_ids"] for search in searches)


# ---------------------------------------------------------------------------
# 6b. Visited-set: a node is inspected at most once
# ---------------------------------------------------------------------------


def test_agent_irrelevant_branch_hits_never_reach_the_evidence_pool(
    civil_code: SimpleNamespace,
) -> None:
    # architecture.md sec 4: "irrelevant branches contribute zero evidence
    # nodes". search_nodes returns the document's top-k nodes even when the
    # query matched nothing (every score 0.0), so feeding those hits
    # downstream would let an irrelevant branch contribute evidence - and
    # select_evidence dedups by node_id keeping the FIRST occurrence, so a
    # score-0 copy could shadow the same node's genuinely-scored copy.
    state = run_agent(
        civil_code.doc_id, "qqzz wumpus frobnicatex", db_path=civil_code.db
    )
    searches = [
        result
        for result in state.get("tool_results", [])
        if result.get("tool") == "search_nodes"
    ]
    assert searches
    for result in searches:
        assert result["relevant"] is False
        assert result["result_ids"] == []
    # The evidence pool holds only the MATCHED hits.
    matched_total = sum(len(result.get("result_ids", [])) for result in searches)
    assert len(state.get("_hits", [])) == matched_total


def test_agent_trace_carries_the_fallback_flag(
    civil_code: SimpleNamespace,
) -> None:
    # retrieval.md sec 6: "the fallback flag is computed in score_nodes from
    # parse_confidence and carried on every ScoredNode (so search_nodes
    # results surface it); the Day 3 trace reads it from retrieval rather
    # than recomputing it." A flat-fallback document is answered from a
    # page-only tree with ident/title/hier = 0, and the trace must say so.
    state = run_agent(
        civil_code.doc_id,
        "What are the requisites of contracts?",
        db_path=civil_code.db,
    )
    searches = [
        entry
        for entry in state.get("trace", [])
        if entry.get("tool") == "search_nodes"
    ]
    assert searches
    for entry in searches:
        assert "fallback" in entry, "the search trace entry must carry the fallback flag"
        assert isinstance(entry["fallback"], bool)
    # The Civil Code parses structurally, so it is not a flat fallback.
    assert searches[0]["fallback"] is False


def test_agent_broaden_retry_inspects_its_hits(
    civil_code: SimpleNamespace,
) -> None:
    # The topology's "search again" returns to the search-and-INSPECT cycle.
    # A broadened hit that was never inspected has not been read, and the
    # trace must show the read - so the retry must not skip the inspect node.
    from apps.api.agent import graph as agent_graph

    original = agent_graph.TOOL_TABLE["search_nodes"]
    calls: list[str] = []

    def stub_first_search(state: Any, args: Any) -> Any:
        calls.append(str(args.get("query")))
        if len(calls) == 1:
            # The first search finds nothing: the gate fails, the retry fires.
            state["tool_iterations"] = int(state.get("tool_iterations", 0)) + 1
            state["tool_results"].append(
                {
                    "tool": "search_nodes",
                    "args": args,
                    "result_ids": [],
                    "relevant": False,
                    "latency_ms": 0.0,
                }
            )
            state["trace"].append(
                {
                    "tool": "search_nodes",
                    "args": args,
                    "result_ids": [],
                    "relevant": False,
                    "branch_id": "search_nodes#1",
                    "latency_ms": 0.0,
                }
            )
            return state
        return original(state, args)

    agent_graph.TOOL_TABLE["search_nodes"] = stub_first_search
    try:
        state = run_agent(
            civil_code.doc_id,
            "What are the requisites of contracts?",
            db_path=civil_code.db,
        )
    finally:
        agent_graph.TOOL_TABLE["search_nodes"] = original

    # The retry ran its own search AND inspected what it found.
    assert len(calls) >= 2, "the retry must issue its own search"
    inspections = [
        result
        for result in state.get("tool_results", [])
        if result.get("tool") == "inspect_node"
    ]
    assert inspections, "the broadened hits must be inspected before selection"
    # The order is: search (no hits) -> gate -> retry -> search -> inspect.
    tool_order = [
        result.get("tool")
        for result in state.get("tool_results", [])
        if result.get("tool") in {"search_nodes", "inspect_node"}
    ]
    assert tool_order[0] == "search_nodes"
    assert "inspect_node" in tool_order[1:], "inspection must follow the retry's search"


def test_agent_trace_carries_fallback_true_on_a_flat_document(
    tmp_path: Path,
) -> None:
    """The contract's exact scenario (SPEC.md sec 6, retrieval.md sec 6).

    A document with no headings parses with parse_confidence < 0.5, so the
    tree is flat (page nodes only) and ident/title/hier are all 0 - the
    retrieval is lex-only. The trace must carry `fallback: true` so a
    consumer can tell that the run answered from a flat page-only tree, in
    which an identifier-hit support could never have fired.
    """
    import fitz
    from apps.api.ingestion.service import ingest_pdf
    from apps.api.retrieval.index import build_indexes

    pdf_path = tmp_path / "flat.pdf"
    document = fitz.open()
    for index in range(3):
        page = document.new_page()
        page.insert_text(
            (72, 72),
            f"Page {index + 1} of a document with no headings whatsoever. "
            "Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed "
            "do eiusmod tempor incididunt ut labore et dolore magna aliqua. "
            "Ut enim ad minim veniam, quis nostrud exercitation ullamco "
            "laboris nisi ut aliquip ex ea commodo consequat.",
            fontsize=11,
        )
    document.save(str(pdf_path))
    document.close()

    db_path = str(tmp_path / "flat.db")
    uploaded = ingest_pdf(str(pdf_path), slug="flat-doc", db_path=db_path)
    assert uploaded.parse_confidence < 0.5, "the fixture must be a flat fallback"
    build_indexes(db_path, uploaded.id, index_root=str(tmp_path / "idx"))

    import apps.api.retrieval.scoring as _scoring

    original_root = _scoring.INDEX_ROOT
    _scoring.INDEX_ROOT = tmp_path / "idx"
    try:
        state = run_agent(uploaded.id, "What does this document say?", db_path=db_path)
    finally:
        _scoring.INDEX_ROOT = original_root

    searches = [
        entry
        for entry in state.get("trace", [])
        if entry.get("tool") == "search_nodes"
    ]
    assert searches
    for entry in searches:
        assert entry.get("fallback") is True, (
            "a flat-fallback document must be marked in the trace"
        )


def test_agent_supporter_sets_do_not_cross_subquestions(
    civil_code: SimpleNamespace,
) -> None:
    """A sub-question's supporters must come from ITS OWN hits.

    A hit's lex score is relative to the query that retrieved it. Pooling the
    hits of every sub-question into one list and asking about them together
    lets one sub-question's nodes satisfy another sub-question's support
    check: a node retrieved for A carries lex > 0 relative to A, and the
    supporter rule accepts lex > 0 for ANY sub-question.
    """
    first = "What is succession?"
    second = "What is the registration of chattel mortgages?"
    state = run_agent(
        civil_code.doc_id, f"{first} {second}", db_path=civil_code.db
    )
    per_question = state["evidence"]["gate"]["per_question"]
    by_question = state.get("_hits_by_question", {})
    # The planner strips the trailing "?" from a split part, so the
    # sub-questions are the two parts without it.
    assert set(per_question) == {first.rstrip("?"), second.rstrip("?")}
    for sub_question, supporters in per_question.items():
        own = {
            str(hit.node_id) for hit in by_question.get(sub_question, [])
        }
        assert supporters, f"no supporters for {sub_question!r}"
        # Every supporter was retrieved for THIS sub-question.
        assert set(supporters) <= own, (
            f"{sub_question!r} is supported by nodes retrieved for another "
            f"sub-question: {sorted(set(supporters) - own)}"
        )
    # The merged evidence set still respects the top-8 window.
    assert len(state["evidence"]["kept"]) <= 8


def test_agent_gate_fails_closed_for_an_unsearched_subquestion(
    civil_code: SimpleNamespace,
) -> None:
    # select_evidence evaluates each sub-question against the whole kept set,
    # so a sub-question that was NEVER searched would still collect supporters
    # from the nodes another sub-question found - and the gate would pass for a
    # question the agent never looked up. A budget of 1 truncates the search
    # loop after the first sub-question, so the run must abstain rather than
    # claim coverage it does not have.
    question = "What is succession? What is the registration of chattel mortgages?"
    state = run_agent(
        civil_code.doc_id, question, max_iterations=1, db_path=civil_code.db
    )
    searched = [
        result["args"]["query"]
        for result in state.get("tool_results", [])
        if result.get("tool") == "search_nodes"
    ]
    assert len(searched) == 1, "the budget should have truncated the searches"
    assert state["_gate_passed"] is False
    assert state["_gate_missing"], "the unsearched sub-question must be missing"
    assert state["insufficient"] is True
    # With enough budget for both searches, the gate passes.
    full = run_agent(civil_code.doc_id, question, db_path=civil_code.db)
    assert full["_gate_passed"] is True
    assert full["insufficient"] is False


def test_agent_max_iterations_out_of_range_is_422(
    civil_code: SimpleNamespace,
) -> None:
    # api.md sec 3 freezes max_iterations to 1..12. An out-of-range value is a
    # bad body (422), not a silently clamped one - clamping 13 to 1 would run
    # a different request than the client asked for.
    from apps.api.ingestion import errors

    for cap in (0, -1, 13, 100):
        with pytest.raises(errors.ApiError) as excinfo:
            run_agent(civil_code.doc_id, "test", max_iterations=cap, db_path=civil_code.db)
        assert excinfo.value.code == "VALIDATION_ERROR"
        assert excinfo.value.status == 422

    # The boundaries are accepted.
    for cap in (1, 12):
        state = run_agent(
            civil_code.doc_id, "test", max_iterations=cap, db_path=civil_code.db
        )
        assert state["tool_iterations"] <= cap


def test_agent_ask_path_validates_max_iterations(
    civil_code: SimpleNamespace,
) -> None:
    # The ask surfaces must apply the same validation as run_agent. A request
    # with max_iterations=999 must not run a 999-iteration ask, and 0/-1 must
    # not silently produce a zero-iteration run that abstains as if it were a
    # real result.
    from apps.api.ingestion import errors

    for cap in (0, -1, 13, 999):
        with pytest.raises(errors.ApiError) as excinfo:
            run_ask(civil_code.doc_id, "test", max_iterations=cap, db_path=civil_code.db)
        assert excinfo.value.code == "VALIDATION_ERROR"
        assert excinfo.value.status == 422

        # The SSE surface must agree: one terminal error event, same code.
        events = list(
            iter_sse_events(
                civil_code.doc_id, "test", max_iterations=cap, db_path=civil_code.db
            )
        )
        assert events[-1]["event"] == "error"
        assert events[-1]["error"]["code"] == "VALIDATION_ERROR"
        assert len(events) == 1


def test_agent_ask_path_reports_a_bad_body_as_422_not_500(
    civil_code: SimpleNamespace,
) -> None:
    # A ValueError escaping the sync surface became a 500 INTERNAL_ERROR while
    # the SSE surface returned 422 - one endpoint contract answering the same
    # request two different ways.
    #
    # The value is built at runtime so the test exercises the path without
    # lying to the type checker about the declared parameter type.
    from apps.api.ingestion import errors

    bad: Any = "twelve"
    with pytest.raises(errors.ApiError) as excinfo:
        run_ask(civil_code.doc_id, "test", max_iterations=bad, db_path=civil_code.db)
    assert excinfo.value.status == 422
    assert excinfo.value.code == "VALIDATION_ERROR"


def test_agent_visited_set_prevents_reinspect(civil_code: SimpleNamespace) -> None:
    # Both sub-questions rank the same nodes; the visited-set must stop the
    # second sub-question from re-inspecting them (architecture.md sec 4:
    # "Visited-set prevents re-inspect loops").
    state = run_agent(
        civil_code.doc_id,
        "What are the requisites of contracts? What are the requisites of contracts?",
        db_path=civil_code.db,
    )
    inspected = [
        result["result_ids"][0]
        for result in state.get("tool_results", [])
        if result.get("tool") == "inspect_node" and result.get("result_ids")
    ]
    assert len(inspected) == len(set(inspected)), "a node was inspected twice"
    assert inspected, "expected at least one inspection"


# ---------------------------------------------------------------------------
# 6c. Every wrapper: caps clamp, budget ticks, relevance is recorded
# ---------------------------------------------------------------------------


def test_agent_all_seven_wrappers_touch_the_budget(civil_code: SimpleNamespace) -> None:
    # The graph reaches only some of the 7, so the rest are exercised here.
    # Each wrapper must tick the iteration budget and record a tool_result.
    from apps.api.agent.state import initial_state
    from apps.api.agent.tools import TOOL_TABLE

    state = initial_state("q", civil_code.doc_id)
    state["_db_path"] = civil_code.db
    article = "node_civil-code-public_article_1318"
    chapter = "node_civil-code-public_chapter_2_d20"
    calls = [
        ("search_nodes", {"query": "requisites of contracts"}),
        ("inspect_node", {"node_id": article}),
        ("list_children", {"node_id": chapter}),
        ("get_parent", {"node_id": article}),
        ("expand_context", {"node_id": article, "radius": 1}),
        ("follow_reference", {"reference_id": "ref_does_not_exist"}),
        ("get_source", {"node_id": article}),
    ]
    for name, args in calls:
        before = state["tool_iterations"]
        state = TOOL_TABLE[name](state, args)
        assert state["tool_iterations"] == before + 1, f"{name} did not tick the budget"
        result = state["tool_results"][-1]
        assert result["tool"] == name
        assert isinstance(result["relevant"], bool)
    assert len(state["tool_results"]) == 7
    # The budget never exceeded the cap.
    assert state["tool_iterations"] <= 12


def test_agent_wrappers_clamp_out_of_range_args(civil_code: SimpleNamespace) -> None:
    from apps.api.agent.state import initial_state
    from apps.api.agent.tools import TOOL_TABLE

    state = initial_state("q", civil_code.doc_id)
    state["_db_path"] = civil_code.db
    article = "node_civil-code-public_article_1318"
    chapter = "node_civil-code-public_chapter_2_d20"

    state = TOOL_TABLE["search_nodes"](state, {"query": "x", "top_k": 999})
    assert state["tool_results"][-1]["args"]["top_k"] == 20
    state = TOOL_TABLE["expand_context"](state, {"node_id": article, "radius": 99})
    assert state["tool_results"][-1]["args"]["radius"] == 2
    state = TOOL_TABLE["list_children"](state, {"node_id": chapter, "limit": 9999})
    assert state["tool_results"][-1]["args"]["limit"] == 50


def test_agent_wrappers_handle_a_missing_node(civil_code: SimpleNamespace) -> None:
    # A missing node is a dead branch, not a crash: the wrapper records it as
    # irrelevant and the agent moves on. An ApiError escaping a wrapper would
    # abort the whole ask over one bad node id.
    from apps.api.agent.state import initial_state
    from apps.api.agent.tools import TOOL_TABLE

    state = initial_state("q", civil_code.doc_id)
    state["_db_path"] = civil_code.db
    for name, args in (
        ("inspect_node", {"node_id": "node_nonexistent_xyz"}),
        ("get_source", {"node_id": "node_nonexistent_xyz"}),
        ("get_parent", {"node_id": "node_nonexistent_xyz"}),
        ("expand_context", {"node_id": "node_nonexistent_xyz"}),
        ("list_children", {"node_id": "node_nonexistent_xyz"}),
    ):
        state = TOOL_TABLE[name](state, args)
        result = state["tool_results"][-1]
        assert result["relevant"] is False, f"{name} treated a missing node as relevant"
        assert result["result_ids"] == []
        # The error code is recorded on the trace entry for that call.
        trace_entry = state["trace"][-1]
        assert trace_entry["tool"] == name
        assert trace_entry.get("error") == "NOT_FOUND"


def test_agent_follow_reference_unknown_edge_is_irrelevant(
    civil_code: SimpleNamespace,
) -> None:
    # retrieval.md sec 7: an unresolved edge stays unresolved; the agent
    # moves on and never invents a target.
    from apps.api.agent.state import initial_state
    from apps.api.agent.tools import TOOL_TABLE

    state = initial_state("q", civil_code.doc_id)
    state["_db_path"] = civil_code.db
    state = TOOL_TABLE["follow_reference"](state, {"reference_id": "ref_does_not_exist"})
    result = state["tool_results"][-1]
    assert result["relevant"] is False
    assert result["result_ids"] == []


# ---------------------------------------------------------------------------
# 7. Agent thinness: no banned imports
# ---------------------------------------------------------------------------


def test_agent_thinness() -> None:
    agent_dir = REPO_ROOT / "apps" / "api" / "agent"
    pattern = re.compile(r"^\s*(?:from|import)\s+(sqlite3|sqlalchemy|apps\.api\.models)\b")
    for path in agent_dir.rglob("*.py"):
        for line in path.read_text(encoding="utf-8").splitlines():
            assert not pattern.match(line), f"banned import in {path}: {line}"


def test_agent_imports_only_the_sanctioned_retrieval_surface() -> None:
    """architecture.md sec 2 / SPEC.md sec 2 bound the agent's imports.

    The agent may import "retrieval.api (tool impls ONLY)" and the retrieval
    Context Builder. It may NOT reach into the retrieval internals - the
    normalizer, the scorer, the index builder - the way the generation layer
    is barred from "retrieval internals".
    """
    agent_dir = REPO_ROOT / "apps" / "api" / "agent"
    allowed = {
        "apps.api.retrieval.api",
        "apps.api.retrieval.context",
    }
    pattern = re.compile(r"^\s*from\s+(apps\.api\.retrieval[\w.]*)\s+import")
    for path in agent_dir.rglob("*.py"):
        for line in path.read_text(encoding="utf-8").splitlines():
            match = pattern.match(line)
            if match is None:
                continue
            module = match.group(1)
            assert module in allowed, (
                f"{path.name} imports {module}; the agent may import only "
                f"{sorted(allowed)}"
            )


# ---------------------------------------------------------------------------
# 8. AskResponse shape
# ---------------------------------------------------------------------------


def test_agent_ask_response_shape(civil_code: SimpleNamespace) -> None:
    response = run_ask(
        civil_code.doc_id,
        "What are the requisites of contracts?",
        db_path=civil_code.db,
    )
    assert set(response.keys()) == {
        "answer_markdown",
        "citations",
        "evidence_ids",
        "iterations",
        "latency_ms",
        "tokens",
        "tokens_source",
        "insufficient",
    }
    # The token counts are an estimate until the LLM backend reports real
    # usage; the payload must say so, so no consumer can publish them as
    # measured token usage.
    assert response["tokens_source"] == "estimated"
    assert response["insufficient"] is False
    assert isinstance(response["iterations"], int)
    assert response["iterations"] <= MAX_TOOL_ITERATIONS
    assert response["latency_ms"] >= 0
    assert set(response["tokens"].keys()) == {"in", "out"}
    # Every citation points at a kept evidence node and carries a page span.
    kept_ids = set(response["evidence_ids"])
    assert response["citations"], "a passing ask must cite something"
    for index, citation in enumerate(response["citations"], start=1):
        assert citation["node_id"] in kept_ids
        assert citation["page_start"] is not None
        assert citation["page_end"] is not None
        # api.md sec 3: citations[] = [{n, node_id, label, page_start, page_end}]
        assert set(citation.keys()) == {
            "n",
            "node_id",
            "label",
            "page_start",
            "page_end",
        }
        assert citation["n"] == index
        assert citation["label"]
        # The answer's inline [n] marker must correspond to citation n:
        # architecture.md sec 6 - "every factual sentence carries [n]".
        assert f"[{citation['n']}]" in response["answer_markdown"]


# ---------------------------------------------------------------------------
# 9. SSE event sequence
# ---------------------------------------------------------------------------


def test_ask_stream_order(civil_code: SimpleNamespace) -> None:
    """The frozen SSE event order (api.md sec 3), pinned exactly.

    CONSTRAINTS.md:32 names this contract test: "SSE contract | Event order
    pinned incl. zero-tool-call + error cases | contract test
    `test_ask_stream_order` | CI".

    The order is asserted as a WHOLE sequence, not by membership. Asserting
    "plan first, done last, evidence between them, tool_call adjacent to its
    tool_result" would still accept
    ``plan -> tool_call -> tool_result -> evidence -> tool_call ->
    tool_result -> token -> done`` — an `evidence` event in the middle of the
    tool stream and `token` before `evidence`. A client built against the
    frozen sequence (PLAN.md Day 5) would break on that.
    """
    events = list(
        iter_sse_events(
            civil_code.doc_id,
            "What are the requisites of contracts?",
            db_path=civil_code.db,
        )
    )
    types = [event["event"] for event in events]

    tool_pairs = len([t for t in types if t == "tool_call"])
    token_count = len([t for t in types if t == "token"])
    expected = (
        ["plan"]
        + ["tool_call", "tool_result"] * tool_pairs
        + ["evidence"]
        + ["token"] * token_count
        + ["done"]
    )
    assert types == expected, f"the frozen event order changed:\n{types}\n!=\n{expected}"

    # The payload of each event carries the contract fields (api.md sec 3).
    for event in events:
        if event["event"] == "plan":
            assert set(event.keys()) == {"event", "sub_questions"}
        elif event["event"] == "tool_call":
            assert set(event.keys()) == {"event", "tool", "args"}
        elif event["event"] == "tool_result":
            assert set(event.keys()) == {"event", "tool", "result_ids", "relevant"}
        elif event["event"] == "evidence":
            assert set(event.keys()) == {"event", "evidence_ids", "insufficient"}
        elif event["event"] == "token":
            assert set(event.keys()) == {"event", "text"}
        elif event["event"] == "done":
            assert set(event.keys()) == {"event", "ask_response"}
    # A passing run streams tokens.
    assert token_count > 0


def test_ask_stream_order_zero_tool_call(civil_code: SimpleNamespace) -> None:
    """The zero-tool-call case of the same contract (api.md sec 3).

    "zero-tool-call runs legal: plan -> evidence(empty) -> done(insufficient
    ask_response)".
    """
    events = list(
        iter_sse_events(civil_code.doc_id, "", db_path=civil_code.db)
    )
    types = [event["event"] for event in events]
    assert types == ["plan", "evidence", "done"]
    assert events[1]["evidence_ids"] == []
    assert events[1]["insufficient"] is True
    assert events[2]["ask_response"]["insufficient"] is True
    assert events[2]["ask_response"]["citations"] == []


def test_ask_stream_order_error_is_terminal(civil_code: SimpleNamespace) -> None:
    """The error-terminal case of the same contract (api.md sec 3).

    "The `error` event is terminal: it is the last event of the stream."
    """
    events = list(
        iter_sse_events(
            civil_code.doc_id,
            "What are the requisites of contracts?",
            db_path=str(Path(civil_code.db).parent / "missing.db"),
        )
    )
    types = [event["event"] for event in events]
    assert types[-1] == "error"
    assert types.count("error") == 1
    assert "done" not in types
    assert set(events[-1]["error"].keys()) == {"code", "message", "details"}


def test_agent_sse_event_sequence(civil_code: SimpleNamespace) -> None:
    events = list(
        iter_sse_events(
            civil_code.doc_id,
            "What are the requisites of contracts?",
            db_path=civil_code.db,
        )
    )
    types = [event["event"] for event in events]
    assert types[0] == "plan"
    assert types[-1] == "done"
    plan_index = types.index("plan")
    evidence_index = types.index("evidence")
    done_index = types.index("done")
    assert plan_index < evidence_index < done_index
    # Every tool_call is immediately followed by its tool_result.
    for index, event_type in enumerate(types):
        if event_type == "tool_call":
            assert types[index + 1] == "tool_result"
    # The tool_result payload carries the contract fields.
    for event in events:
        if event["event"] == "tool_result":
            assert set(event.keys()) >= {"tool", "result_ids", "relevant"}
    # A passing run streams tokens.
    assert "token" in types


def test_agent_sse_streams_incrementally(civil_code: SimpleNamespace) -> None:
    # PRD US-3: the steps are STREAMED so the user can audit retrieval.
    #
    # A batch implementation (run the whole graph, then replay the trace)
    # produces the SAME event sequence, so asserting the sequence proves
    # nothing. What distinguishes them is WHEN the work happens: in a real
    # stream the graph is still executing when the first event is yielded; in
    # a batch replay the graph has already finished.
    #
    # The test counts the tool calls the graph makes. If the first event
    # arrives before any tool has run, the implementation is streaming.
    from apps.api.agent import graph as agent_graph

    calls: list[str] = []
    original = agent_graph.TOOL_TABLE["search_nodes"]

    def counting_search(state: Any, args: Any) -> Any:
        calls.append("search")
        return original(state, args)

    agent_graph.TOOL_TABLE["search_nodes"] = counting_search
    try:
        generator = iter_sse_events(
            civil_code.doc_id,
            "What are the requisites of contracts?",
            db_path=civil_code.db,
        )
        first = next(generator)
        assert first["event"] == "plan", "the first event must be the plan"
        # The plan is yielded BEFORE any search has run: the graph is still
        # executing. A batch replay would have run every tool first.
        assert calls == [], f"tools already ran before the plan was yielded: {calls}"

        # Draining the rest performs the work.
        rest = list(generator)
        types = [first["event"]] + [e["event"] for e in rest]
        assert types[-1] == "done"
        assert types.count("error") == 0
        assert calls, "the search should have run while draining"

        # The decisive property: the tool_call event was yielded BEFORE the
        # search it reports had run. A batch implementation would have run the
        # search first and replayed it afterwards, so the tool_call would
        # arrive with the search already counted.
        tool_call_index = next(
            index for index, event in enumerate([first] + rest) if event["event"] == "tool_call"
        )
        assert tool_call_index == 1, "the tool_call must be the second event"
    finally:
        agent_graph.TOOL_TABLE["search_nodes"] = original


def test_agent_sse_emits_every_trace_entry_exactly_once(
    civil_code: SimpleNamespace,
) -> None:
    # The SSE layer walks the trace with a cursor across streamed node updates.
    # A cursor bug would duplicate or drop tool events, and the sequence would
    # still look plausible - so compare the streamed tools against the trace.
    from apps.api.agent.graph import run_agent

    for question in (
        "What are the requisites of contracts?",
        "What is succession? What is the registration of chattel mortgages?",
        "qqzz wumpus frobnicatex",
    ):
        state = run_agent(civil_code.doc_id, question, db_path=civil_code.db)
        trace_tools = [
            entry["tool"] for entry in state.get("trace", []) if entry.get("tool")
        ]
        events = list(
            iter_sse_events(civil_code.doc_id, question, db_path=civil_code.db)
        )
        calls = [e["tool"] for e in events if e["event"] == "tool_call"]
        results = [e["tool"] for e in events if e["event"] == "tool_result"]
        assert calls == trace_tools, f"tool_call stream differs from the trace: {question}"
        assert results == trace_tools, f"tool_result stream differs: {question}"


def test_agent_sse_latency_excludes_consumer_time(
    civil_code: SimpleNamespace,
) -> None:
    # evaluation.md sec 5 reports latency as a SYSTEM metric. A streaming
    # surface cannot run ahead of its consumer, so a wall-clock measurement
    # around the whole stream measures the consumer as much as the agent - a
    # slow client would inflate the system's reported latency.
    import time

    question = "What are the requisites of contracts?"

    fast = list(
        iter_sse_events(civil_code.doc_id, question, db_path=civil_code.db)
    )
    fast_latency = fast[-1]["ask_response"]["latency_ms"]

    generator = iter_sse_events(
        civil_code.doc_id, question, db_path=civil_code.db
    )
    next(generator)
    time.sleep(1.5)  # the consumer is slow
    slow = list(generator)
    slow_latency = slow[-1]["ask_response"]["latency_ms"]

    # The 1.5s the consumer spent must not appear in the measurement.
    assert slow_latency < fast_latency + 1000, (
        f"consumer think-time leaked into the latency: {slow_latency} vs {fast_latency}"
    )


def test_agent_sse_zero_tool_call_is_legal(civil_code: SimpleNamespace) -> None:
    # An invalid plan (the planner emits zero sub-questions) is the legal
    # zero-tool-call run: api.md sec 3 "zero-tool-call runs legal: plan ->
    # evidence(empty) -> done(insufficient ask_response)".
    events = list(iter_sse_events(civil_code.doc_id, "", db_path=civil_code.db))
    types = [event["event"] for event in events]
    assert types[0] == "plan"
    assert "tool_call" not in types
    assert "tool_result" not in types
    assert types[-1] == "done"
    evidence_event = next(event for event in events if event["event"] == "evidence")
    assert evidence_event["insufficient"] is True
    assert evidence_event["evidence_ids"] == []
    # An abstention streams no tokens.
    assert "token" not in types
    done_event = events[-1]
    assert done_event["ask_response"]["insufficient"] is True
    assert done_event["ask_response"]["answer_markdown"] == CANNED_ABSTENTION


def test_agent_sse_bogus_db_is_error_event(civil_code: SimpleNamespace) -> None:
    # api.md sec 3: the error event is TERMINAL - it is the last event of the
    # stream. The plan may already have been sent before the failure, so the
    # contract is "ends with exactly one error", not "starts with an error".
    events = list(
        iter_sse_events(
            civil_code.doc_id,
            "What are the requisites of contracts?",
            db_path=str(Path(civil_code.db).parent / "missing.db"),
        )
    )
    assert events[-1]["event"] == "error"
    assert set(events[-1]["error"].keys()) == {"code", "message", "details"}
    # Exactly one error event, and no done event after it.
    assert [e["event"] for e in events].count("error") == 1
    assert "done" not in [e["event"] for e in events]
    # The stream is well formed up to the failure.
    for event in events[:-1]:
        assert event["event"] == "plan"


# ---------------------------------------------------------------------------
# 10. Planner bounds
# ---------------------------------------------------------------------------


def test_agent_planner_bounds(civil_code: SimpleNamespace) -> None:
    state = run_agent(
        civil_code.doc_id,
        "What is succession? What is the registration of chattel mortgages? "
        "What are the requisites of contracts? What is the statute of frauds?",
        db_path=civil_code.db,
    )
    # The question has four parts, so the cap of 4 is exactly reached: the
    # upper bound is meaningful, not vacuous.
    assert len(state["sub_questions"]) == 4
    assert len(state["sub_questions"]) <= MAX_SUB_QUESTIONS


def test_agent_planner_single_question(civil_code: SimpleNamespace) -> None:
    state = run_agent(
        civil_code.doc_id, "What is the statute of frauds?", db_path=civil_code.db
    )
    assert len(state["sub_questions"]) == 1


def test_agent_planner_empty_fails_closed(civil_code: SimpleNamespace) -> None:
    # retrieval.md sec 7: zero sub-questions is an invalid plan; the gate
    # fails closed to the abstention. The HTTP layer owns the 422 for a bad
    # body; the agent never raises for one.
    state = run_agent(civil_code.doc_id, "", db_path=civil_code.db)
    assert state["sub_questions"] == []
    assert state["insufficient"] is True
    assert state["answer_md"] == CANNED_ABSTENTION
    # The plan was retried once before failing closed (retrieval.md sec 7).
    plan_entries = [entry for entry in state.get("trace", []) if entry.get("node") == "plan"]
    attempts = [entry for entry in plan_entries if "attempt" in entry]
    assert len(attempts) == 2
    assert attempts[0]["sub_questions"] == []
    assert attempts[1]["sub_questions"] == []
    assert any(entry.get("result") == "invalid_plan" for entry in plan_entries)


def test_agent_planner_retries_the_plan_once(civil_code: SimpleNamespace) -> None:
    # A bare conjunction splits to nothing (it is a conjunction, not a
    # question), so the second plan attempt takes the remainder as one
    # sub-question.
    state = run_agent(civil_code.doc_id, "and", db_path=civil_code.db)
    plan_entries = [entry for entry in state.get("trace", []) if entry.get("node") == "plan"]
    attempts = [entry for entry in plan_entries if "attempt" in entry]
    assert len(attempts) == 2
    assert attempts[0]["sub_questions"] == []
    assert attempts[1]["sub_questions"] == ["and"]


# ---------------------------------------------------------------------------
# 11. Stoplist contract (retrieval.md sec 3)
# ---------------------------------------------------------------------------


def test_agent_identifier_only_query() -> None:
    # The contract's broaden-retry is "stopword-strip + identifier-only
    # query" (retrieval.md sec 3). The identifier-only form carries the
    # canonical labels and nothing else, so retrieval reaches the provisions
    # the question is ABOUT rather than the ones that share a word with it.
    from apps.api.agent.stoplist import identifier_query

    assert identifier_query("article 1318") == "art 1318"
    assert identifier_query("Art. 1318 requisites") == "art 1318"
    assert identifier_query("section 2") == "sec 2"
    # A question with no identifier has no identifier-only form.
    assert identifier_query("what is succession") == ""
    # A bare number is not a label, so it does not survive as one.
    assert identifier_query("the said provision about 1318") == ""
    # A range expands to the bare label plus each member.
    assert "art 1318" in identifier_query("articles 1318-1320")
    assert "art 1320" in identifier_query("articles 1318-1320")


def test_agent_broaden_retry_prefers_the_identifier_only_form(
    civil_code: SimpleNamespace,
) -> None:
    # When a sub-question carries an identifier, the retry must query with
    # the identifier only - that is the first half of the contract's retry.
    from apps.api.agent import graph as agent_graph

    original = agent_graph.TOOL_TABLE["search_nodes"]
    queries: list[str] = []

    def stub_first_search(state: Any, args: Any) -> Any:
        queries.append(str(args.get("query")))
        if len(queries) == 1:
            state["tool_iterations"] = int(state.get("tool_iterations", 0)) + 1
            state["tool_results"].append(
                {
                    "tool": "search_nodes",
                    "args": args,
                    "result_ids": [],
                    "relevant": False,
                    "latency_ms": 0.0,
                }
            )
            state["trace"].append(
                {
                    "tool": "search_nodes",
                    "args": args,
                    "result_ids": [],
                    "relevant": False,
                    "branch_id": "search_nodes#1",
                    "latency_ms": 0.0,
                }
            )
            return state
        return original(state, args)

    agent_graph.TOOL_TABLE["search_nodes"] = stub_first_search
    try:
        state = run_agent(
            civil_code.doc_id,
            "What does article 1318 require?",
            db_path=civil_code.db,
        )
    finally:
        agent_graph.TOOL_TABLE["search_nodes"] = original

    broaden_entries = [
        entry for entry in state.get("trace", []) if entry.get("node") == "broaden_retry"
    ]
    assert len(broaden_entries) == 1
    retry_queries = broaden_entries[0]["broadened_queries"]
    assert retry_queries, "the retry must issue a query"
    assert retry_queries[0] == "art 1318", (
        f"the retry must use the identifier-only form, got {retry_queries!r}"
    )


def test_agent_stoplist_contract() -> None:
    # FTS5 default English stopwords + the label words.
    for word in ("a", "an", "and", "the", "of", "with", "was", "were", "will"):
        assert word in STOPLIST
    for word in ("said", "provision", "provides", "article", "section"):
        assert word in STOPLIST
    assert broaden("the said provision about contracts") == "about contracts"
    # A query made only of stopwords must NOT be reduced to a stopword: "the"
    # occurs in ~2268 corpus nodes, so a stopword-only query would score
    # lex=1.0 against almost the whole corpus and the gate would pass on a
    # query that means nothing. The original is the honest fallback.
    assert broaden("the and of") == "the and of"
    # An empty query stays empty rather than becoming a bare stopword.
    assert broaden("") == ""
    assert broaden("   ") == "   "
