"""Agent loop state (PLAN.md Day 3, architecture.md sec 4).

The graph mutates one typed dict. Every node reads and writes the same
``AgentState``, so the trace is the single source of truth for what happened
during an ask.

``tool_iterations`` is the budget counter: it increments on every tool wrapper
call (architecture.md sec 4 "max 12 tool iterations per ask"). The broaden-retry
does NOT reset it — the 12 includes the retry.

Keys prefixed with an underscore are internal to the loop (scored hits, the
resolved db path, the gate's routing decision). They are declared here because
LangGraph builds one channel per declared key: an undeclared key written by a
node is silently dropped, which would break the hand-off of scored hits from
the search node to the evidence selector.
"""

from __future__ import annotations

from typing import Any, TypedDict


class AgentState(TypedDict, total=False):
    """Mutable graph state. ``total=False``: a node may write any subset."""

    # --- request -----------------------------------------------------------
    question: str
    document_id: str
    max_iterations: int

    # --- plan --------------------------------------------------------------
    sub_questions: list[str]

    # --- budget and trace --------------------------------------------------
    tool_iterations: int
    visited_branches: list[dict[str, Any]]
    tool_results: list[dict[str, Any]]
    trace: list[dict[str, Any]]

    # --- retrieval ---------------------------------------------------------
    evidence: dict[str, Any]
    context: dict[str, Any]

    # --- answer ------------------------------------------------------------
    answer_md: str
    citations: list[dict[str, Any]]
    evidence_ids: list[str]
    insufficient: bool
    broadened: bool
    error: str

    # --- internal (underscore-prefixed) ------------------------------------
    _hits: list[Any]
    _hits_by_question: dict[str, list[Any]]
    _hit_batches: list[list[str]]
    _inspected: list[str]
    _broadened_query: str
    _gate_route: str
    _gate_passed: bool
    _gate_missing: list[str]
    _broadened_queries: list[str]
    _broadened_for: list[str]
    _gen_tokens_source: str | None
    _gen_model: str
    _gen_backend: str
    _gen_temperature: float
    _gen_seed: int
    _gen_prompt_version: str
    _gen_prompt_tokens: int
    _gen_completion_tokens: int
    _gen_stream: list[str]
    _db_path: str | None
    _searched: list[str]
    _visited: list[str]
    _expanded: list[str]


def initial_state(
    question: str,
    document_id: str,
    *,
    max_iterations: int = 12,
) -> AgentState:
    """The entry state for ``run_agent`` — deterministic, no RNG."""
    return AgentState(
        question=question,
        document_id=document_id,
        sub_questions=[],
        tool_iterations=0,
        visited_branches=[],
        tool_results=[],
        trace=[],
        evidence={},
        context={},
        answer_md="",
        citations=[],
        evidence_ids=[],
        insufficient=False,
        max_iterations=max_iterations,
        broadened=False,
        error="",
        _hits=[],
        _inspected=[],
    )
