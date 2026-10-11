# ADR-001: LangGraph for orchestration; custom engine for retrieval

## Status
Accepted

## Date
2026-10-07

## Context
Tuklas needs agent control flow (plan → search → inspect → expand → sufficiency loop,
backtrack, checkpointing, streamed progress) plus a retrieval algorithm (BM25 +
structural + reference scoring over a document tree). One purchase-or-build decision
covers both, and conflating them would destroy the project's research credibility:
if LangGraph did the retrieval, there is no independent contribution to measure.

## Decision
LangGraph owns state, graph execution, conditional routing, loops, checkpointing,
and step streaming. Everything retrieval-shaped — tree schema, scoring, ranking,
evidence selection, reference resolution, citations — is custom code in
`apps/api/retrieval/`, called through 7 thin tools. See `architecture.md §4`.

## Alternatives Considered

### Raw LangChain / hand-rolled loop
- Pros: fewer dependencies.
- Cons: re-implements checkpointing, routing, and streaming badly; audit trail weaker.
- Rejected: orchestration is solved plumbing, not the research question.

### LangGraph with built-in retrievers
- Pros: faster to first demo.
- Cons: retrieval becomes framework behavior; the Tuklas-vs-vector comparison measures nothing of ours.
- Rejected: would invalidate the core research question.

## Consequences
- `apps/api/agent/` must stay thin; any scoring logic found there is a review failure.
- LangGraph version pinned; graph definition changes don't alter retrieval semantics.
- Checkpointing via SQLite checkpointer gives free audit/replay for evaluation traces.
