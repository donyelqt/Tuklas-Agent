"""LangGraph agent loop (PLAN.md Day 3, architecture.md sec 4).

Frozen topology (architecture.md sec 4)::

    START -> query_plan (1-4 sub_questions) -> search -> inspect ->
    (relevant ? expand : mark irrelevant + backtrack) ->
    select_evidence (retrieval-owned) -> sufficiency_gate ->
    (more? search again : build_context) -> generate -> END

The topology offers "expand OR follow"; this slice expands. The follow path
needs an edge in the ``references_`` table, and ingestion does not parse
reference edges yet (Day 1 scope), so no edge exists to follow. See the
``_expand_or_backtrack_node`` docstring and TODO.md Day 3.

Binding guards:
- Budget: max 12 tool iterations per ask, INCLUDING the one broaden-retry.
  The retry does not reset the counter — it spends from it.
- Gate: a node supports sub-question S iff ``score >= 0.15 AND
  (lex > 0 OR identifier-hit)``. The gate passes iff every planned
  sub-question has >= 1 supporting node in the kept set. Zero sub-questions
  is an invalid plan and fails closed (retrieval.md sec 7).
- Backtrack: an irrelevant tool_result contributes zero evidence nodes and is
  logged with its branch id.
- The sufficiency check itself is retrieval-owned: ``select_evidence`` applies
  the threshold and returns the per-question supporter lists. The agent reads
  that verdict and decides only pass/fail — it never re-scores a hit.

Two distinct failure modes, per retrieval.md sec 7, handled separately:

1. *Best kept score below the threshold* — one broaden-retry (stopword-strip
   query, spends iteration budget), then ``insufficient_evidence``.
2. *Invalid plan* (the planner emits zero sub-questions) — retry the plan
   once, then ``insufficient_evidence``. No broaden-retry: a broaden fixes a
   retrieval miss, not an invalid plan.

The generation seam is the ``Generator`` Protocol. Day 4 plugs in the real
``LLMInterface``; today a deterministic stub renders the kept evidence as
citation lines, so the whole loop is exercisable without a model.
"""

from __future__ import annotations

import re
from typing import Any, Protocol, cast, runtime_checkable

from apps.api.agent.state import AgentState, initial_state
from apps.api.agent.stoplist import broaden as _broaden
from apps.api.agent.stoplist import identifier_query as _identifier_query
from apps.api.agent.tools import TOOL_TABLE, budget_left
from apps.api.ingestion import errors
from apps.api.retrieval.api import (
    EVIDENCE_KEEP_TOP,
    SUPPORT_THRESHOLD,
    evidence_rank_key,
    select_evidence,
)
from apps.api.retrieval.context import build_context
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

#: api.md sec 3: the unified abstention wording. Pinned so it never drifts.
CANNED_ABSTENTION = (
    "The retrieved evidence is insufficient to answer this question from the "
    "documents."
)

#: architecture.md sec 4 budget.
MAX_TOOL_ITERATIONS = 12

#: The gate threshold and the evidence window are RETRIEVAL'S, not the
#: agent's. retrieval.md sec 3 makes the threshold explicitly tunable (a
#: recorded tuning run changes it) and sec 5 makes the window
#: retrieval-owned, so a second copy here would drift: a tuned threshold
#: would leave the agent reporting the old one, and
#: apps/api/retrieval/context.py reads evidence["gate"] to decide which nodes
#: are never dropped from the prompt - so the context builder's protection
#: would be derived from a stale threshold. The agent reads the numbers
#: retrieval used, never its own.

#: PLAN.md Day 3: the planner emits 1-4 sub-questions.
MAX_SUB_QUESTIONS = 4

#: How many candidates the agent inspects per sub-question.
INSPECT_TOP_N = 3


@runtime_checkable
class Generator(Protocol):
    """Generation seam (architecture.md sec 4, Generation column).

    The seam carries three things the generation layer needs and the agent
    holds:

    - the OPAQUE PromptContext (the Context Builder's product);
    - the EVIDENCE, because a citation's page span lives in the kept set and
      not in the context parts (which carry only {node_id,text,score});
    - the GATE VERDICT, because the refusal path keys on it (SPEC.md sec 6:
      insufficiency is the gate verdict, NOT "are there kept nodes").

    The result is the generation service's answer dict:
    {answer_markdown, citations, insufficient, tokens_source, model,
     backend, temperature, seed}.
    """

    def generate(
        self,
        context: dict[str, Any],
        question: str,
        sub_questions: list[str],
        evidence: dict[str, Any],
        *,
        gate_passed: bool,
    ) -> dict[str, Any]:
        """Return the generation service's answer dict."""
        ...


class StubGenerator:
    """Deterministic, model-free generator.

    Renders each context part as a numbered citation line, so the answer, the
    citations, and the evidence ids are all derived from the retrieval
    verdict — no model, no invented facts.
    """

    def generate(
        self,
        context: dict[str, Any],
        question: str,
        sub_questions: list[str],
        evidence: dict[str, Any],
        *,
        gate_passed: bool,
    ) -> dict[str, Any]:
        # The gate decides sufficiency, NOT the presence of evidence
        # (SPEC.md sec 6). The stub honours it exactly as the real service
        # does: a failed gate refuses, and no answer is produced.
        if not gate_passed:
            return {
                "answer_markdown": CANNED_ABSTENTION,
                "citations": [],
                "insufficient": True,
            }
        parts = context.get("parts", []) if isinstance(context, dict) else []
        lines: list[str] = []
        for index, part in enumerate(parts, start=1):
            node_id = str(part.get("node_id", ""))
            lines.append(f"[{index}] {node_id}: {str(part.get('text', ''))[:200]}")
        # The citation list comes from the ONE rule
        # (apps/api/generation/citations.py), not from a second copy of it:
        # two copies of the same rule are two rules that will drift, and
        # they already had.
        from apps.api.generation.citations import assemble_citations

        citations = assemble_citations(parts, evidence)
        if not lines:
            return {
                "answer_markdown": CANNED_ABSTENTION,
                "citations": [],
                "insufficient": True,
            }
        return {
            "answer_markdown": "\n".join(lines),
            "citations": citations,
            "insufficient": False,
        }


def _validate_max_iterations(max_iterations: int) -> int:
    """api.md sec 3: ``max_iterations?: int =12 (1..12)``.

    The range is part of the frozen request contract, so an out-of-range
    value is a bad body (422), not a silently clamped one. Validating here
    means the route layer gets the 422 for free from the typed error.
    """
    if isinstance(max_iterations, bool) or not isinstance(max_iterations, int):
        raise errors.validation_error(
            "max_iterations must be an integer", max_iterations=str(max_iterations)
        )
    if max_iterations < 1 or max_iterations > MAX_TOOL_ITERATIONS:
        raise errors.validation_error(
            "max_iterations must be between 1 and 12",
            max_iterations=str(max_iterations),
        )
    return max_iterations


def _split_query(question: str) -> list[str]:
    """Primary planner split (retrieval.md sec 7: zero is invalid).

    Splits on sentence terminators, commas, and coordinating conjunctions.
    A single short question stays whole. Returns 0..4 parts; zero means the
    plan is invalid.
    """
    raw = question.strip()
    if not raw:
        return []
    parts = re.split(
        r"\s*(?:\?|!)\s*|\s*,\s*|\s+\b(?:and|or|but)\b\s+", raw, flags=re.IGNORECASE
    )
    cleaned = [
        part.strip().rstrip("?!.")
        for part in parts
        if part.strip().lower() not in {"", "and", "or", "but"}
    ]
    cleaned = [part for part in cleaned if part]
    return cleaned[:MAX_SUB_QUESTIONS]


def _split_query_retry(question: str) -> list[str]:
    """Second-chance planner (retrieval.md sec 7: retry plan once).

    Drops punctuation and treats the remainder as a single sub-question. A
    question made only of punctuation still yields nothing, which is the
    genuinely invalid plan the gate must fail closed on.
    """
    stripped = "".join(ch for ch in question if ch.isalnum() or ch.isspace()).strip()
    if not stripped:
        return []
    return [stripped]


def _db_kwarg(state: AgentState) -> dict[str, Any]:
    path = state.get("_db_path")
    return {"db_path": path} if path else {}


def _query_plan_node(state: AgentState) -> AgentState:
    """Plan node: 1-4 sub-questions, else retry the plan once, else invalid."""
    question = state["question"]
    sub_questions = _split_query(question)
    state["trace"].append(
        {"node": "plan", "attempt": 1, "sub_questions": list(sub_questions)}
    )
    if not sub_questions:
        sub_questions = _split_query_retry(question)
        state["trace"].append(
            {"node": "plan", "attempt": 2, "sub_questions": list(sub_questions)}
        )
    state["sub_questions"] = sub_questions
    if not sub_questions:
        state["trace"].append({"node": "plan", "result": "invalid_plan"})
    return state


def _search_node(state: AgentState) -> AgentState:
    """Search node: one search_nodes call per sub-question.

    The wrapper owns budget accounting and trace logging; this node only
    orchestrates which queries to run and records which hits came from which
    sub-question, so the later nodes can expand one branch per sub-question.
    """
    batches = list(state.get("_hit_batches", []))
    for sub_question in state.get("sub_questions", []):
        if budget_left(state) <= 0:
            break
        state = TOOL_TABLE["search_nodes"](state, {"query": sub_question, "top_k": 10})
        results = state.get("tool_results", [])
        node_ids = list(results[-1]["result_ids"]) if results else []
        batches.append(node_ids)
    state["_hit_batches"] = batches
    return state


def _inspect_node(state: AgentState) -> AgentState:
    """Inspect node: inspect the top candidates of each sub-question.

    An inspected node that cannot contribute evidence (too short to quote, or
    no page span to cite) is a dead branch: the wrapper records it with
    relevant=false and the branch id, and it contributes zero evidence nodes.

    The visited-set prevents re-inspect loops: the same node can rank in two
    sub-questions' batches, and inspecting it twice would spend budget on a
    branch already known.
    """
    visited: set[str] = set(state.get("_visited", []))
    for batch in state.get("_hit_batches", []):
        for node_id in batch[:INSPECT_TOP_N]:
            if budget_left(state) <= 0:
                state["_visited"] = sorted(visited)
                return state
            if node_id in visited:
                continue
            visited.add(node_id)
            state = TOOL_TABLE["inspect_node"](state, {"node_id": node_id})
    state["_visited"] = sorted(visited)
    return state


def _expand_or_backtrack_node(state: AgentState) -> AgentState:
    """Expand the surviving branch of each sub-question.

    architecture.md sec 4: "(relevant ? expand/follow : mark irrelevant +
    backtrack)". The wrapper already marked the dead branches; this node
    expands one surviving branch per sub-question.

    The topology offers expand OR follow. The follow path is not reachable
    here: ``follow_reference`` resolves an edge in the ``references_`` table,
    and ingestion does not parse reference edges yet (Day 1 scope - the table
    stays empty), so there is no edge to follow. The wrapper exists and
    honours the ``resolved|unresolved|error`` contract; the graph expands
    instead of following. Recorded in TODO.md Day 3 so the branch is not
    mistaken for exercised behaviour.
    """
    results = state.get("tool_results", [])
    expanded: set[str] = set(state.get("_expanded", []))
    for batch in state.get("_hit_batches", []):
        if budget_left(state) <= 0:
            break
        for result in results:
            if result.get("tool") != "inspect_node" or not result.get("relevant"):
                continue
            node_ids = result.get("result_ids", [])
            if not node_ids:
                continue
            node_id = node_ids[0]
            if node_id not in batch or node_id in expanded:
                continue
            expanded.add(node_id)
            state = TOOL_TABLE["expand_context"](
                state, {"node_id": node_id, "radius": 1}
            )
            break
    state["_expanded"] = sorted(expanded)
    return state


def _searched_sub_questions(state: AgentState) -> list[str]:
    """The sub-questions the agent actually searched for.

    ``select_evidence`` evaluates each sub-question against the whole kept set,
    so a sub-question that was never searched would still collect supporters
    from the nodes another sub-question found — and the gate would pass for a
    question the agent never looked up. When the budget truncates the search
    loop, that turns a partial run into a confident answer.

    Only the sub-questions that were searched are therefore put to the gate.
    An unsearched sub-question has no supporters, so the gate fails closed for
    it and the run abstains rather than claiming coverage it does not have.
    """
    searched = list(state.get("_searched", []))
    if not searched:
        return []
    # A sub-question counts as searched if its OWN query was run, or if a
    # broadened query was run ON ITS BEHALF. The retry's hits are filed
    # under the sub-question they broaden, so the gate must ask about that
    # sub-question - otherwise the retry's evidence is retrieved and then
    # never judged.
    broadened_for = list(state.get("_broadened_for", []))
    planned = list(state.get("sub_questions", []))
    return [
        sub
        for sub in planned
        if sub in searched or sub in broadened_for
    ]


def _select_evidence_node(state: AgentState) -> AgentState:
    """Retrieval-owned evidence gate (retrieval.md sec 5).

    ``select_evidence`` is called PER SUB-QUESTION, with that sub-question's
    own hits, and the verdicts are merged.

    The reason is that a hit's lex score is relative to the query that
    retrieved it. Pooling the hits of every sub-question into one list and
    asking about them together lets one sub-question's nodes satisfy another
    sub-question's support check: a node retrieved for sub-question A carries
    ``lex > 0`` relative to A, and the supporter rule accepts ``lex > 0`` for
    ANY sub-question. Measured: with "What is succession?" and "What is the
    registration of chattel mortgages?" - whose top-10 hits are disjoint -
    3 of the second sub-question's supporters came from the FIRST
    sub-question's hits. Asking per sub-question keeps each verdict about the
    nodes that were actually retrieved for it.
    """
    asked = _searched_sub_questions(state)
    by_question = state.get("_hits_by_question", {})
    per_question: dict[str, list[str]] = {}
    kept: list[dict[str, Any]] = []
    seen: set[str] = set()
    threshold = SUPPORT_THRESHOLD
    for sub_question in asked:
        hits = by_question.get(sub_question)
        if hits is None:
            # No hits were retrieved for this sub-question (the budget cut the
            # search short). It has no supporters, so the gate fails for it.
            per_question[sub_question] = []
            continue
        result = select_evidence(hits, [sub_question], **_db_kwarg(state))
        per_question[sub_question] = list(
            result.get("gate", {}).get("per_question", {}).get(sub_question, [])
        )
        for item in result.get("kept", []):
            node_id = str(item.get("node_id"))
            if node_id in seen:
                continue
            seen.add(node_id)
            kept.append(item)
    kept.sort(key=evidence_rank_key)
    # The top-8 evidence window (retrieval.md sec 5) applies to the MERGED
    # set too: the agent assembles one context, and that context must not
    # carry more nodes than the window allows.
    #
    # The cut is SUPPORTER-AWARE. Each sub-question had its own top-8, so a
    # plain score-ordered cut of the merged list can drop a sub-question's
    # only supporter in favour of higher-scoring nodes another sub-question
    # contributed - and the gate would then fail for a sub-question that
    # retrieval had actually supported. The supporters of every sub-question
    # are kept first; the remaining slots go to the highest-scoring nodes.
    supporter_ids: set[str] = set()
    for supporters in per_question.values():
        supporter_ids.update(str(node_id) for node_id in supporters)
    supporters_kept = [
        item for item in kept if str(item.get("node_id")) in supporter_ids
    ]
    others = [item for item in kept if str(item.get("node_id")) not in supporter_ids]
    kept = (supporters_kept + others)[:EVIDENCE_KEEP_TOP]
    kept.sort(key=evidence_rank_key)
    # The gate's supporter lists are then filtered to the kept window. Each
    # sub-question had its OWN top-8, so before this filter the supporters
    # could name nodes the merged evidence set does not carry - the gate
    # would then pass on supporters the answer never shows. architecture.md
    # sec 4: the gate passes iff every sub-question has >= 1 supporting node
    # IN THE KEPT SET.
    kept_ids = {str(item.get("node_id")) for item in kept}
    per_question = {
        sub_question: [
            node_id for node_id in supporters if node_id in kept_ids
        ]
        for sub_question, supporters in per_question.items()
    }
    gate_ids: list[str] = []
    for node_ids in per_question.values():
        for node_id in node_ids:
            if node_id not in gate_ids:
                gate_ids.append(node_id)
    # The insufficiency verdict is over EVERY PLANNED sub-question - the
    # same set the gate uses. Computed over the searched ones only, a
    # truncated search would report insufficient=false while the gate failed
    # and the run abstained: the trace would then contradict itself.
    planned = list(state.get("sub_questions", []))
    missing = [
        sub_question
        for sub_question in planned
        if not per_question.get(sub_question)
    ]
    evidence = {
        "kept": kept,
        "insufficient": bool(missing) or not kept,
        "gate": {
            "threshold": threshold,
            "node_ids": gate_ids,
            "per_question": per_question,
        },
    }
    state["evidence"] = evidence
    state["trace"].append(
        {
            "node": "select_evidence",
            "asked_sub_questions": asked,
            "kept": len(kept),
            "insufficient": bool(evidence.get("insufficient")),
        }
    )
    return state


def _gate_verdict(state: AgentState) -> tuple[bool, list[str]]:
    """Every planned sub-question must have >= 1 supporting node in the kept set.

    The support check itself is retrieval-owned (``select_evidence`` already
    applied score >= 0.15 AND (lex > 0 OR identifier-hit)); the agent reads
    the per-question supporter lists and decides only pass/fail.

    A sub-question the agent never searched has no supporter list at all, so
    it counts as missing: the gate fails closed rather than reporting coverage
    the run never looked up.
    """
    evidence = state.get("evidence", {})
    gate = evidence.get("gate", {}) if isinstance(evidence, dict) else {}
    per_question = gate.get("per_question", {}) if isinstance(gate, dict) else {}
    kept_ids = {
        str(item.get("node_id"))
        for item in (evidence.get("kept", []) if isinstance(evidence, dict) else [])
        if isinstance(item, dict)
    }
    missing: list[str] = []
    for sub_question in state.get("sub_questions", []):
        supporters = [
            node_id
            for node_id in per_question.get(sub_question, [])
            if node_id in kept_ids
        ]
        if not supporters:
            missing.append(sub_question)
    return (not missing and bool(kept_ids)), missing


def _sufficiency_gate_node(state: AgentState) -> AgentState:
    """Gate node: decide the route, record it, and return the state.

    The route travels in ``_gate_route`` (a declared channel) because a node
    must return the state, not the destination name; the conditional edge
    reads the channel afterwards.
    """
    sub_questions = state.get("sub_questions", [])
    if not sub_questions:
        # Invalid plan (retrieval.md sec 7): fail closed. No broaden-retry —
        # a broaden fixes a retrieval miss, not an invalid plan.
        state["_gate_passed"] = False
        state["_gate_missing"] = []
        state["trace"].append({"node": "sufficiency_gate", "result": "invalid_plan"})
        state["_gate_route"] = "build_context"
        return state

    passed, missing = _gate_verdict(state)
    state["_gate_passed"] = passed
    state["_gate_missing"] = missing
    if passed:
        state["_gate_route"] = "build_context"
        return state

    if not state.get("broadened") and budget_left(state) > 0:
        # Broaden ONLY the sub-questions that failed. A sub-question that
        # already has a supporter has its evidence in the kept set; adding
        # broadened hits for it would push better nodes out of the top-8
        # window, not add any.
        broadened: list[str] = []
        # Which sub-question each broadened query serves. The retry's hits
        # must be attributed to the sub-question they broaden, not to the
        # broadened query's own text: select_evidence looks the hits up by
        # sub-question, so hits filed under the broadened query would be
        # orphaned and the retry could never change the verdict.
        broadened_for: list[str] = []
        for sub_question in missing:
            # The contract's retry is "stopword-strip + identifier-only query"
            # (retrieval.md sec 3). The identifier-only form is tried FIRST
            # when the question carries an identifier: it reaches the
            # provisions the question is ABOUT, which is a different
            # neighbourhood from the stopword-stripped text.
            identifier_only = _identifier_query(sub_question)
            if identifier_only:
                broadened.append(identifier_only)
                broadened_for.append(sub_question)
                continue
            reduced = _broaden(sub_question)
            if reduced == sub_question:
                # Neither form can change the query, so the retry would run
                # the identical search. Retrieval is deterministic, so the
                # retry cannot change the outcome: skip it rather than spend
                # iteration budget on a search we already know the answer to.
                continue
            broadened.append(reduced)
            broadened_for.append(sub_question)
        if not broadened:
            state["broadened"] = True
            state["trace"].append(
                {
                    "node": "broaden_retry",
                    "result": "skipped_no_reduction",
                    "missing_sub_questions": missing,
                }
            )
            state["trace"].append(
                {
                    "node": "sufficiency_gate",
                    "result": "insufficient_evidence",
                    "missing_sub_questions": missing,
                }
            )
            state["_gate_route"] = "build_context"
            return state
        state["broadened"] = True
        state["_broadened_queries"] = broadened
        state["_broadened_for"] = broadened_for
        state["trace"].append(
            {
                "node": "broaden_retry",
                "broadened_queries": broadened,
                "broadened_for": broadened_for,
                "missing_sub_questions": missing,
            }
        )
        state["_gate_route"] = "broaden_search"
        return state

    state["trace"].append(
        {
            "node": "sufficiency_gate",
            "result": "insufficient_evidence",
            "missing_sub_questions": missing,
        }
    )
    state["_gate_route"] = "build_context"
    return state


def _route_after_gate(state: AgentState) -> str:
    """Conditional edge: read the gate's recorded route."""
    return str(state.get("_gate_route", "build_context"))


def _broaden_search_node(state: AgentState) -> AgentState:
    """Broaden-retry: one stopword-stripped search per sub-question.

    The retry spends from the iteration budget (each search is a budgeted tool
    call) and never resets the counter.

    The topology's "search again" returns to the search-and-INSPECT cycle, not
    straight to the evidence selection: a hit that was never inspected has not
    been read, and the trace must show the read. So this node searches, and
    the graph routes the result through the inspect node before the evidence
    is selected.
    """
    queries = state.get("_broadened_queries", [])
    targets = state.get("_broadened_for", [])
    batches = list(state.get("_hit_batches", []))
    for index, query in enumerate(queries):
        if budget_left(state) <= 0:
            break
        state = TOOL_TABLE["search_nodes"](state, {"query": query, "top_k": 10})
        results = state.get("tool_results", [])
        node_ids = list(results[-1]["result_ids"]) if results else []
        batches.append(node_ids)
        # Re-file the retry's hits under the sub-question they broaden. The
        # wrapper filed them under the broadened query's own text, and
        # select_evidence looks hits up BY SUB-QUESTION - so without this the
        # retry's hits are orphaned and the retry can never change the
        # verdict, which would make it a no-op that only spends budget.
        target = targets[index] if index < len(targets) else None
        if target is not None:
            # Read, mutate and write back: the state channel hands out the
            # mapping by value, so mutating it in place would be lost when
            # the node returns.
            by_question = dict(state.get("_hits_by_question", {}))
            hits = by_question.get(query, [])
            if hits:
                by_question.setdefault(target, []).extend(hits)
            state["_hits_by_question"] = by_question
    state["_hit_batches"] = batches
    return state


def _build_context_node(state: AgentState) -> AgentState:
    """Context builder: retrieval owns it; the agent never reorders or trims."""
    state["context"] = build_context(state.get("evidence", {}))
    return state


def _default_generator() -> Generator:
    """The configured generator: the real local LLM (ADR-004).

    Imported lazily so the agent module does not depend on the generation
    package at import time, and so a caller that passes its own generator
    never pays for constructing one.
    """
    from apps.api.generation.config import generator_from_env

    return generator_from_env()


def _generate_node_factory(generator: Generator) -> Any:
    def _generate(state: AgentState) -> AgentState:
        # The generator owns the refusal path (architecture.md sec 6: the
        # refusal is the generation layer's, and the advice oracle is enforced
        # there). The agent hands it the gate verdict and the evidence, and
        # records what comes back.
        evidence = state.get("evidence", {})
        context = state.get("context", {})
        result = generator.generate(
            context,
            state["question"],
            state.get("sub_questions", []),
            evidence,
            gate_passed=bool(state.get("_gate_passed")),
        )
        state["answer_md"] = str(result.get("answer_markdown", CANNED_ABSTENTION))
        state["insufficient"] = bool(result.get("insufficient", False))
        citations = result.get("citations", [])
        state["citations"] = list(citations) if isinstance(citations, list) else []
        # The evidence ids are the cited nodes: what the answer actually rests
        # on, not every kept node.
        state["evidence_ids"] = [
            str(citation.get("node_id"))
            for citation in state["citations"]
            if isinstance(citation, dict) and citation.get("node_id")
        ]
        # ADR-004: the model id, backend, temperature and seed are recorded
        # with the ask, so a run can name the run that produced it. The keys
        # are written literally: a TypedDict cannot take a computed key, and
        # a computed key would not be a channel either.
        if "tokens_source" in result:
            state["_gen_tokens_source"] = result["tokens_source"]
        if "model" in result:
            state["_gen_model"] = result["model"]
        if "backend" in result:
            state["_gen_backend"] = result["backend"]
        if "temperature" in result:
            state["_gen_temperature"] = result["temperature"]
        if "seed" in result:
            state["_gen_seed"] = result["seed"]
        if "prompt_version" in result:
            state["_gen_prompt_version"] = result["prompt_version"]
        # The provider's own token counts, when a model was called.
        if result.get("prompt_tokens") is not None:
            state["_gen_prompt_tokens"] = result["prompt_tokens"]
        if result.get("completion_tokens") is not None:
            state["_gen_completion_tokens"] = result["completion_tokens"]
        # The provider's real deltas, when the generator produced them. The SSE
        # surface streams THESE, not a slicing of the finished answer, so the
        # client sees the answer arrive.
        stream = result.get("stream")
        if isinstance(stream, list):
            state["_gen_stream"] = [str(chunk) for chunk in stream]
        return state

    return _generate


def build_graph(generator: Generator | None = None) -> CompiledStateGraph:
    """Compile the frozen graph (architecture.md sec 4).

    Node set: query_plan, search, inspect, expand_or_backtrack,
    select_evidence, sufficiency_gate, broaden_search, build_context, generate.
    """
    graph = StateGraph(AgentState)
    # The REAL generator by default, for the same reason as the ask surface:
    # the stub applies no advice oracle and no refusal path. A caller that
    # wants the deterministic model-free answer passes StubGenerator
    # explicitly.
    gen: Generator = generator if generator is not None else _default_generator()

    graph.add_node("query_plan", _query_plan_node)
    graph.add_node("search", _search_node)
    graph.add_node("inspect", _inspect_node)
    graph.add_node("expand_or_backtrack", _expand_or_backtrack_node)
    graph.add_node("select_evidence", _select_evidence_node)
    graph.add_node("sufficiency_gate", _sufficiency_gate_node)
    graph.add_node("broaden_search", _broaden_search_node)
    graph.add_node("build_context", _build_context_node)
    graph.add_node("generate", _generate_node_factory(gen))

    graph.add_edge(START, "query_plan")
    graph.add_edge("query_plan", "search")
    graph.add_edge("search", "inspect")
    graph.add_edge("inspect", "expand_or_backtrack")
    graph.add_edge("expand_or_backtrack", "select_evidence")
    graph.add_edge("select_evidence", "sufficiency_gate")
    graph.add_conditional_edges(
        "sufficiency_gate",
        _route_after_gate,
        {"build_context": "build_context", "broaden_search": "broaden_search"},
    )
    # The "search again" branch of the topology returns to the search-and-
    # inspect cycle: a broadened hit that was never inspected has not been
    # read. inspect -> expand_or_backtrack -> select_evidence is the same
    # path the first search takes.
    graph.add_edge("broaden_search", "inspect")
    graph.add_edge("build_context", "generate")
    graph.add_edge("generate", END)

    return graph.compile()


def run_agent(
    document_id: str,
    question: str,
    *,
    max_iterations: int = 12,
    db_path: str | None = None,
    generator: Generator | None = None,
) -> AgentState:
    """Run the loop and return the full state (api.md sec 3, architecture.md §4).

    The returned state carries the trace, the iteration count, the evidence
    verdict, and the answer or the canned abstention.
    """
    # An empty question is an invalid plan (retrieval.md sec 7): the gate
    # fails closed to the abstention. The HTTP layer owns the 422 for a bad
    # body; the agent never raises for one.
    max_iterations = _validate_max_iterations(max_iterations)
    state = initial_state(question, document_id, max_iterations=max_iterations)
    state["_db_path"] = db_path
    compiled = build_graph(generator)
    # LangGraph's invoke returns a plain mapping; the state IS an AgentState.
    final = cast(AgentState, compiled.invoke(state))
    return final


__all__ = [
    "AgentState",
    "CANNED_ABSTENTION",
    "Generator",
    "MAX_SUB_QUESTIONS",
    "MAX_TOOL_ITERATIONS",
    "StubGenerator",
    "build_graph",
    "run_agent",
]
