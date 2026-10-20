"""Ask service: agent loop -> AskResponse + SSE (api.md sec 3).

``run_ask`` is the synchronous surface behind ``POST /v1/ask``.
``iter_sse_events`` is the streaming surface behind ``POST /v1/ask/stream``.
Both drive the same graph and read the same trace, so the streamed events and
the final response describe the identical run.

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
from typing import Any

from apps.api.agent.graph import (
    CANNED_ABSTENTION,
    Generator,
    StubGenerator,
    run_agent,
)
from apps.api.agent.state import AgentState
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
    try:
        state = run_agent(
            document_id,
            question,
            max_iterations=max_iterations,
            db_path=db_path,
            generator=gen,
        )
    except errors.ApiError:
        raise
    except ValueError:
        raise
    except Exception as exc:  # pragma: no cover - defensive
        raise errors.ApiError(
            "INTERNAL_ERROR", "agent loop failed", 500, {"error": str(exc)}
        ) from exc
    latency_ms = int((time.perf_counter() - start) * 1000)
    return _build_response(state, latency_ms)


def iter_sse_events(
    document_id: str,
    question: str,
    *,
    max_iterations: int = 12,
    db_path: str | None = None,
    generator: Generator | None = None,
) -> Iterator[dict[str, Any]]:
    """Yield the frozen SSE event sequence (api.md sec 3).

    On failure exactly one ``error`` event is yielded and iteration stops.
    """
    start = time.perf_counter()
    gen: Generator = generator if generator is not None else StubGenerator()
    try:
        state = run_agent(
            document_id,
            question,
            max_iterations=max_iterations,
            db_path=db_path,
            generator=gen,
        )
    except errors.ApiError as err:
        yield {"event": "error", "error": err.body()["error"]}
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

    # plan{sub_questions}
    yield {"event": "plan", "sub_questions": list(state.get("sub_questions", []))}

    # One tool_call/tool_result pair per tool wrapper call, in trace order.
    for entry in state.get("trace", []):
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

    # evidence{evidence_ids, insufficient}
    evidence = state.get("evidence", {})
    insufficient = bool(state.get("insufficient"))
    evidence_ids = [] if insufficient else _kept_node_ids(evidence)
    yield {
        "event": "evidence",
        "evidence_ids": evidence_ids,
        "insufficient": insufficient,
    }

    # token{text}* — only a real answer streams tokens.
    answer = state.get("answer_md", CANNED_ABSTENTION)
    if not insufficient and answer and answer != CANNED_ABSTENTION:
        for start_index in range(0, len(answer), TOKEN_CHUNK_CHARS):
            yield {
                "event": "token",
                "text": answer[start_index : start_index + TOKEN_CHUNK_CHARS],
            }

    latency_ms = int((time.perf_counter() - start) * 1000)
    yield {"event": "done", "ask_response": _build_response(state, latency_ms)}
