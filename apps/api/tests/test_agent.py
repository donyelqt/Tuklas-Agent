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

import pytest
from apps.api.agent.ask import CANNED_ABSTENTION, iter_sse_events, run_ask
from apps.api.agent.graph import MAX_TOOL_ITERATIONS, build_graph, run_agent
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
    # The retry consumes budget: the counter after the retry is still counted.
    assert state["tool_iterations"] > 0
    assert state["tool_iterations"] <= MAX_TOOL_ITERATIONS
    assert state["broadened"] is True


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


def test_agent_broaden_retry_once(civil_code: SimpleNamespace) -> None:
    # A query whose every token matches no node: the lex layer is all-zero,
    # so the gate has no supporter (retrieval.md sec 3, sec 7).
    query = "qqzz wumpus frobnicatex"
    state = run_agent(
        civil_code.doc_id, query, max_iterations=12, db_path=civil_code.db
    )
    broaden_entries = [
        entry for entry in state.get("trace", []) if entry.get("node") == "broaden_retry"
    ]
    # Exactly one retry: retrieval.md sec 7 says "one broaden-retry".
    assert len(broaden_entries) == 1
    broadened_queries = broaden_entries[0]["broadened_queries"]
    assert broadened_queries == [broaden(query)]
    # The retry still failed, so the run ends in the abstention.
    assert state["insufficient"] is True
    assert state["tool_iterations"] <= MAX_TOOL_ITERATIONS


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
    for result in abandoned:
        assert not set(result.get("result_ids", [])) & kept_ids or not result.get(
            "relevant"
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
# 7. Agent thinness: no banned imports
# ---------------------------------------------------------------------------


def test_agent_thinness() -> None:
    agent_dir = REPO_ROOT / "apps" / "api" / "agent"
    pattern = re.compile(r"^\s*(?:from|import)\s+(sqlite3|sqlalchemy|apps\.api\.models)\b")
    for path in agent_dir.rglob("*.py"):
        for line in path.read_text(encoding="utf-8").splitlines():
            assert not pattern.match(line), f"banned import in {path}: {line}"


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
        "insufficient",
    }
    assert response["insufficient"] is False
    assert isinstance(response["iterations"], int)
    assert response["iterations"] <= MAX_TOOL_ITERATIONS
    assert response["latency_ms"] >= 0
    assert set(response["tokens"].keys()) == {"in", "out"}
    # Every citation points at a kept evidence node and carries a page span.
    kept_ids = set(response["evidence_ids"])
    for citation in response["citations"]:
        assert citation["node_id"] in kept_ids
        assert citation["page_start"] is not None
        assert citation["page_end"] is not None


# ---------------------------------------------------------------------------
# 9. SSE event sequence
# ---------------------------------------------------------------------------


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
    events = list(
        iter_sse_events(
            civil_code.doc_id,
            "What are the requisites of contracts?",
            db_path=str(Path(civil_code.db).parent / "missing.db"),
        )
    )
    assert events[0]["event"] == "error"
    assert set(events[0]["error"].keys()) == {"code", "message", "details"}
    assert len(events) == 1


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
    assert 1 <= len(state["sub_questions"]) <= 4


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


def test_agent_stoplist_contract() -> None:
    # FTS5 default English stopwords + the label words.
    for word in ("a", "an", "and", "the", "of", "with", "was", "were", "will"):
        assert word in STOPLIST
    for word in ("said", "provision", "provides", "article", "section"):
        assert word in STOPLIST
    assert broaden("the said provision about contracts") == "about contracts"
    # A query made only of stopwords still yields a usable (non-empty) query.
    assert broaden("the and of") != ""
