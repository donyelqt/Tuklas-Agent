"""Ask service: agent loop -> AskResponse + SSE (api.md sec 3).

``run_ask`` is the synchronous surface behind ``POST /v1/ask``.
``iter_sse_events`` is the streaming surface behind ``POST /v1/ask/stream``.

Both drive the same graph. The streaming surface drives it through LangGraph's
``stream`` so each tool call reaches the client as it happens — PRD US-3 asks
for the steps to be streamed so the user can audit retrieval, not delivered in
one batch at the end.

Frozen event sequence (api.md sec 3)::

    plan{sub_questions}
    tool_call{tool,args}              <- one pair per tool wrapper call
    tool_result{tool,result_ids,relevant}
    (repeat, at most 12 iterations incl. the broaden-retry)
    evidence{evidence_ids, insufficient}
    token{text}*                      <- only when the gate passed
    done{ask_response} | error{error}

A zero-tool-call run is legal: plan -> evidence(empty) -> done(insufficient).
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from typing import Any, cast

from apps.api.agent.graph import (
    CANNED_ABSTENTION,
    Generator,
    StubGenerator,
    build_graph,
)
from apps.api.agent.state import AgentState, initial_state
from apps.api.ingestion import errors

#: Characters per streamed token event.
TOKEN_CHUNK_CHARS = 80

#: The AskResponse keys (api.md sec 3). Frozen.
ASK_RESPONSE_KEYS = frozenset(
    {
        "answer_markdown",
        "citations",
        "evidence_ids",
        "iterations",
        "latency_ms",
        "tokens",
        "insufficient",
    }
)


def _approx_tokens(chars: int) -> int:
    """Stub token accounting; Day 4 replaces this with real provider usage."""
    return max(0, chars // 4)


def _kept_node_ids(evidence: dict[str, Any]) -> list[str]:
    kept = evidence.get("kept", []) if isinstance(evidence, dict) else []
    return [
        str(item.get("node_id"))
        for item in kept
        if isinstance(item, dict) and item.get("node_id")
    ]


def _build_response(state: AgentState, latency_ms: int) -> dict[str, Any]:
    """Assemble the AskResponse from the final graph state."""
    evidence: dict[str, Any] = state.get("evidence", {})
    insufficient = bool(state.get("insufficient"))
    # api.md sec 3: the abstention returns the SAME shape with
    # answer_markdown=canned text, citations=[], evidence_ids=[],
    # insufficient=true.
    evidence_ids = [] if insufficient else _kept_node_ids(evidence)
    answer = state.get("answer_md", CANNED_ABSTENTION)
    if insufficient:
        answer = CANNED_ABSTENTION
    citations = [] if insufficient else list(state.get("citations", []) or [])
    context: dict[str, Any] = state.get("context", {})
    parts = context.get("parts", []) if isinstance(context, dict) else []
    in_chars = sum(len(str(part.get("text", ""))) for part in parts if isinstance(part, dict))
    return {
        "answer_markdown": answer,
        "citations": citations,
        "evidence_ids": evidence_ids,
        "iterations": int(state.get("tool_iterations", 0)),
        "latency_ms": latency_ms,
        "tokens": {"in": _approx_tokens(in_chars), "out": _approx_tokens(len(answer))},
        "insufficient": insufficient,
    }


def _error_event(err: errors.ApiError) -> dict[str, Any]:
    """The api.md sec 1 error body, as one terminal SSE event."""
    return {"event": "error", "error": err.body()["error"]}


def run_ask(
    document_id: str,
    question: str,
    *,
    max_iterations: int = 12,
    db_path: str | None = None,
    generator: Generator | None = None,
) -> dict[str, Any]:
    """Run the agent loop and return the AskResponse (api.md sec 3).

    Typed failures surface as ``errors.ApiError`` so the route layer can map
    them onto the uniform error body (api.md sec 1); an unexpected exception
    becomes a 500 rather than a traceback.
    """
    start = time.perf_counter()
    gen: Generator = generator if generator is not None else StubGenerator()
    state = initial_state(question, document_id, max_iterations=max_iterations)
    state["_db_path"] = db_path
    try:
        compiled = build_graph(gen)
        final = compiled.invoke(state)
    except errors.ApiError:
        raise
    except ValueError:
        raise
    except Exception as exc:  # pragma: no cover - defensive
        raise errors.ApiError(
            "INTERNAL_ERROR", "agent loop failed", 500, {"error": str(exc)}
        ) from exc
    latency_ms = int((time.perf_counter() - start) * 1000)
    return _build_response(cast(AgentState, final), latency_ms)


def iter_sse_events(
    document_id: str,
    question: str,
    *,
    max_iterations: int = 12,
    db_path: str | None = None,
    generator: Generator | None = None,
) -> Iterator[dict[str, Any]]:
    """Yield the frozen SSE event sequence (api.md sec 3).

    The graph is driven through LangGraph's ``stream``, so a tool_call /
    tool_result pair reaches the caller as soon as that tool has run — the
    steps are streamed, not replayed at the end (PRD US-3).

    On failure exactly one ``error`` event is yielded and iteration stops.
    """
    start = time.perf_counter()
    gen: Generator = generator if generator is not None else StubGenerator()
    state = initial_state(question, document_id, max_iterations=max_iterations)
    state["_db_path"] = db_path

    emitted_trace = 0
    plan_sent = False
    # The stream yields the accumulated state per node; the last chunk is the
    # final state. Typed as AgentState so _build_response stays strict.
    final_state: AgentState = state

    try:
        compiled = build_graph(gen)
        for chunk in compiled.stream(state, stream_mode="updates"):
            for _node, delta in chunk.items():
                if not isinstance(delta, dict):
                    continue
                final_state = AgentState(**delta)
                if not plan_sent:
                    yield {
                        "event": "plan",
                        "sub_questions": list(delta.get("sub_questions", [])),
                    }
                    plan_sent = True
                # Emit each tool trace entry exactly once, as it appears.
                trace = delta.get("trace", [])
                for entry in trace[emitted_trace:]:
                    tool = entry.get("tool")
                    if not tool:
                        continue
                    yield {"event": "tool_call", "tool": tool, "args": entry.get("args", {})}
                    yield {
                        "event": "tool_result",
                        "tool": tool,
                        "result_ids": list(entry.get("result_ids", [])),
                        "relevant": bool(entry.get("relevant", False)),
                    }
                emitted_trace = len(trace)
    except errors.ApiError as err:
        yield _error_event(err)
        return
    except ValueError as err:
        yield {
            "event": "error",
            "error": {
                "code": "VALIDATION_ERROR",
                "message": str(err),
                "details": {},
            },
        }
        return
    except Exception as exc:  # pragma: no cover - defensive
        yield {
            "event": "error",
            "error": {
                "code": "INTERNAL_ERROR",
                "message": "agent loop failed",
                "details": {"error": str(exc)},
            },
        }
        return

    if not plan_sent:
        # A plan that produced no events at all (an invalid plan) still owes
        # the client its plan event.
        yield {"event": "plan", "sub_questions": []}

    # evidence{evidence_ids, insufficient}
    evidence: dict[str, Any] = final_state.get("evidence", {})
    insufficient = bool(final_state.get("insufficient"))
    evidence_ids = [] if insufficient else _kept_node_ids(evidence)
    yield {
        "event": "evidence",
        "evidence_ids": evidence_ids,
        "insufficient": insufficient,
    }

    # token{text}* — only a real answer streams tokens.
    answer = final_state.get("answer_md", CANNED_ABSTENTION)
    if not insufficient and answer and answer != CANNED_ABSTENTION:
        for start_index in range(0, len(answer), TOKEN_CHUNK_CHARS):
            yield {
                "event": "token",
                "text": answer[start_index : start_index + TOKEN_CHUNK_CHARS],
            }

    latency_ms = int((time.perf_counter() - start) * 1000)
    yield {"event": "done", "ask_response": _build_response(final_state, latency_ms)}
