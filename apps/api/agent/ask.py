"""Ask service: agent loop -> AskResponse + SSE (api.md sec 3).

``run_ask`` is the synchronous surface behind ``POST /v1/ask``.
``iter_sse_events`` is the streaming surface behind ``POST /v1/ask/stream``.

Both drive the same graph. The streaming surface drives it through LangGraph's
``stream`` so each tool call reaches the client as it happens — PRD US-3 asks
for the steps to be streamed so the user can audit retrieval, not delivered in
one batch at the end.

The ``token`` events are a REPLAY, not a live feed, and the difference is
deliberate. The advice oracle (architecture.md sec 6) refuses the WHOLE
answer, so the text cannot be released before the whole of it has been
checked — a stream would have to hand over advice phrasing and then retract
it. The generation layer therefore buffers the model's deltas, checks them
against the oracle, and the transport emits them as ``token`` events
afterwards. The deltas are the model's own, in its order, so a client that
joins them gets exactly the answer; but they arrive in a burst once the
model has finished, not as it writes.

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

import logging
import time
from collections.abc import Iterator
from typing import Any, cast

from apps.api.agent.graph import (
    CANNED_ABSTENTION,
    Generator,
    _validate_max_iterations,
    build_graph,
    run_agent,
)
from apps.api.agent.state import AgentState, initial_state
from apps.api.agent.trace import record_ask
from apps.api.ingestion import errors
from apps.api.ingestion.service import DB_PATH as DEFAULT_DB_PATH

_LOG = logging.getLogger(__name__)

#: The AskResponse keys (api.md sec 3).
#:
#: ``tokens_source`` is OPTIONAL and is only present when the token counts
#: are not provider-reported. api.md sec 5 allows additive changes under
#: /v1 only for OPTIONAL fields - a required new key is a strict-schema
#: break, and a client generated from the 7-key shape would fail
#: validation on the extra key under additionalProperties: false.
#:
#: So the key is absent when there is nothing to disambiguate (the provider
#: reported the counts, or the shape is consumed by a caller that predates
#: the label) and present - carrying "estimated" - exactly when a consumer
#: could otherwise mistake a character-count heuristic for measured usage.
ASK_RESPONSE_KEYS = frozenset(
    {
        "answer_markdown",
        "citations",
        "evidence_ids",
        "iterations",
        "latency_ms",
        "tokens",
        "tokens_source",
        "insufficient",
    }
)

#: The keys every AskResponse carries, whatever the token source.
ASK_RESPONSE_REQUIRED_KEYS = frozenset(
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


#: How the token counts in an AskResponse were produced.
#:
#: "estimated" — a character-count heuristic (chars // 4) over the evidence
#: payload and the rendered answer. It is NOT a provider token count: no model
#: was called, and the "input" side is the size of the evidence, not the size
#: of a prompt. Day 4 replaces it with the provider's own usage numbers.
#: "provider" — the LLM backend reported the counts.
TOKENS_SOURCE_ESTIMATED = "estimated"
TOKENS_SOURCE_PROVIDER = "provider"


def _approx_tokens(chars: int) -> int:
    """A character-count estimate, NOT a provider token count.

    Labelled as such in the response (``tokens_source``) so no downstream
    consumer can mistake it for measured usage. Day 4 replaces it with the
    provider's own numbers.
    """
    return max(0, chars // 4)


def _kept_node_ids(evidence: dict[str, Any]) -> list[str]:
    kept = evidence.get("kept", []) if isinstance(evidence, dict) else []
    return [
        str(item.get("node_id"))
        for item in kept
        if isinstance(item, dict) and item.get("node_id")
    ]


def _build_response(
    state: AgentState,
    latency_ms: int,
    tokens_source: str | None = None,
) -> dict[str, Any]:
    """Assemble the AskResponse from the final graph state.

    The token counts come from the BACKEND when a model was called (the
    provider's own prompt/completion counts, ADR-004) and fall back to the
    character-count estimate when no model ran - a refusal, or the model-free
    stub. The ``tokens_source`` label says which, so a consumer can never
    mistake the estimate for measured usage.
    """
    insufficient = bool(state.get("insufficient"))
    # api.md sec 3: the abstention returns the SAME shape with
    # answer_markdown=canned text, citations=[], evidence_ids=[],
    # insufficient=true.
    #
    # The evidence ids are the nodes the answer's CITATIONS point at - what
    # the answer actually rests on, not the whole retrieval window. A client
    # rendering "8 sources" for an answer that cites 2 would be reporting
    # the window, not the grounding. The SSE `evidence` event still carries
    # the full kept set, because that event IS the retrieval report.
    evidence_ids = [] if insufficient else [
        str(citation.get("node_id"))
        for citation in (state.get("citations", []) or [])
        if isinstance(citation, dict) and citation.get("node_id")
    ]
    answer = state.get("answer_md", CANNED_ABSTENTION)
    if insufficient:
        answer = CANNED_ABSTENTION
    citations = [] if insufficient else list(state.get("citations", []) or [])
    context: dict[str, Any] = state.get("context", {})
    parts = context.get("parts", []) if isinstance(context, dict) else []
    in_chars = sum(len(str(part.get("text", ""))) for part in parts if isinstance(part, dict))
    out_chars = len(answer)

    source = tokens_source or state.get("_gen_tokens_source")
    tokens_in = state.get("_gen_prompt_tokens")
    tokens_out = state.get("_gen_completion_tokens")
    if source == TOKENS_SOURCE_PROVIDER and (
        isinstance(tokens_in, int) and isinstance(tokens_out, int)
    ):
        # The provider's own counts, recorded by the generation layer. BOTH
        # must be present: a provider that reported only one of them left the
        # other side unknown, and publishing a hard 0 for it under a
        # "provider" label would present a fabricated zero as measured usage.
        counts = {"in": int(tokens_in), "out": int(tokens_out)}
    else:
        # The provider reported NO counts (a truncated stream, a backend that
        # does not send them). Publishing zeros with tokens_source="provider"
        # would present LOST counts as MEASURED usage, which is worse than an
        # estimate because it looks real. Fall back to the estimate and say
        # so.
        source = TOKENS_SOURCE_ESTIMATED
        counts = {"in": _approx_tokens(in_chars), "out": _approx_tokens(out_chars)}

    response: dict[str, Any] = {
        "answer_markdown": answer,
        "citations": citations,
        "evidence_ids": evidence_ids,
        "iterations": int(state.get("tool_iterations", 0)),
        "latency_ms": latency_ms,
        "tokens": counts,
        "insufficient": insufficient,
    }
    # The label is present exactly when it is needed, and it is an OPTIONAL
    # field (api.md sec 5), so a client built against the 7-key shape still
    # validates.
    if source is not None:
        response["tokens_source"] = source
    return response


def _default_generator() -> Generator:
    """The configured generator: the real local LLM (ADR-004).

    Built lazily, so importing the ask surface does not construct an HTTP
    client, and a test that passes its own generator never pays for it.
    """
    from apps.api.generation.config import generator_from_env

    return generator_from_env()


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
    # The REAL generator by default. The model-free stub applies no advice
    # oracle and no refusal path, so a request answered through it would
    # bypass the safety machinery the generation layer exists to provide.
    # The stub is explicit opt-in, for the tests that want a deterministic
    # answer.
    try:
        gen: Generator = (
            generator if generator is not None else _default_generator()
        )
    except ValueError as err:
        # A MISCONFIGURED backend is a server fault, not a bad body: the
        # caller sent a fine request and the server cannot serve it. It must
        # not escape as a raw ValueError (the app's catch-all would turn it
        # into a 500 with a traceback-free body, but the typed error is
        # clearer about what went wrong).
        _LOG.error("the configured LLM backend is unusable: %s", err)
        raise errors.ApiError(
            "INTERNAL_ERROR", "internal server error", 500, {}
        ) from err
    try:
        final = run_agent(
            document_id,
            question,
            max_iterations=max_iterations,
            db_path=db_path,
            generator=gen,
        )
    except errors.ApiError:
        raise
    except ValueError as err:
        # A bad body is a 422, not a 500. The SSE surface already mapped it
        # that way; the sync surface must agree, or one endpoint contract
        # would answer the same request two different ways.
        raise errors.validation_error(str(err)) from err
    except Exception as exc:  # pragma: no cover - defensive
        # The app-level handler's convention (apps/api/api/app.py): a 500
        # carries an EMPTY details object. str(exc) would leak the internal
        # message - a filesystem path, a SQLite error, a JSON parse position -
        # to the client, and api.md sec 1 says "no traceback leaks". The
        # exception is logged, not returned.
        _LOG.exception("agent loop failed")
        raise errors.ApiError(
            "INTERNAL_ERROR", "internal server error", 500, {}
        ) from exc
    latency_ms = int((time.perf_counter() - start) * 1000)
    response = _build_response(cast(AgentState, final), latency_ms)
    # ADR-004: the run is recorded, so a run can be named and compared.
    # The trace is best-effort: a trace that cannot be written must not cost
    # the caller its answer.
    record_ask(
        document_id,
        question,
        db_path=str(db_path or DEFAULT_DB_PATH),
        state=dict(final),
        response=response,
    )
    return response


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
    # The REAL generator by default. The model-free stub applies no advice
    # oracle and no refusal path, so a request answered through it would
    # bypass the safety machinery the generation layer exists to provide.
    # The stub is explicit opt-in, for the tests that want a deterministic
    # answer.
    try:
        gen: Generator = (
            generator if generator is not None else _default_generator()
        )
    except ValueError as err:
        # A misconfigured backend is a server fault, not a bad body.
        _LOG.error("the configured LLM backend is unusable: %s", err)
        yield {
            "event": "error",
            "error": {
                "code": "INTERNAL_ERROR",
                "message": "internal server error",
                "details": {},
            },
        }
        return
    try:
        max_iterations = _validate_max_iterations(max_iterations)
    except errors.ApiError as err:
        yield _error_event(err)
        return
    state = initial_state(question, document_id, max_iterations=max_iterations)
    state["_db_path"] = db_path

    emitted_trace = 0
    plan_sent = False
    # The agent's own elapsed time, EXCLUDING the time the consumer spends
    # between events. A streaming surface cannot run ahead of its consumer:
    # the generator is suspended at each yield, so wall-clock around the
    # stream measures the consumer as much as the agent.
    #
    # The clock is therefore re-anchored every time the generator RESUMES
    # (i.e. right after a yield returns). The time from a resume to the next
    # chunk arriving is the graph's own work, with the consumer's think-time
    # already excluded - the consumer was not running during it.
    agent_elapsed_ms = 0
    last_resume = time.perf_counter()
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
                # The graph produced this node's output: that is agent work,
                # and the interval since the last resume is exactly it.
                now = time.perf_counter()
                agent_elapsed_ms += int((now - last_resume) * 1000)
                if not plan_sent:
                    yield {
                        "event": "plan",
                        "sub_questions": list(delta.get("sub_questions", [])),
                    }
                    plan_sent = True
                    # The generator is suspended here; the consumer's time
                    # starts now and must not be counted.
                    last_resume = time.perf_counter()
                # Emit each tool trace entry exactly once, as it appears.
                trace = delta.get("trace", [])
                for entry in trace[emitted_trace:]:
                    tool = entry.get("tool")
                    if not tool:
                        continue
                    yield {"event": "tool_call", "tool": tool, "args": entry.get("args", {})}
                    last_resume = time.perf_counter()
                    yield {
                        "event": "tool_result",
                        "tool": tool,
                        "result_ids": list(entry.get("result_ids", [])),
                        "relevant": bool(entry.get("relevant", False)),
                    }
                    last_resume = time.perf_counter()
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
    except Exception:  # pragma: no cover - defensive
        # Same rule as the sync path: the details stay empty and the
        # exception is logged rather than returned.
        _LOG.exception("agent loop failed")
        yield {
            "event": "error",
            "error": {
                "code": "INTERNAL_ERROR",
                "message": "internal server error",
                "details": {},
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

    # token{text}* — only a real answer carries tokens.
    #
    # The deltas are the PROVIDER'S, recorded by the generation layer in the
    # order the backend emitted them - not a slicing of a finished string.
    # (The Day-3 implementation cut the finished answer into 80-character
    # pieces, which looked streamed but was not.)
    #
    # They arrive in a BURST, not live: the advice oracle has to see the whole
    # text before any of it can be released, so the generation layer buffers
    # the deltas. See the module docstring.
    answer = final_state.get("answer_md", CANNED_ABSTENTION)
    if not insufficient and answer and answer != CANNED_ABSTENTION:
        chunks = list(final_state.get("_gen_stream", []) or [])
        if not chunks:
            # No provider deltas were recorded (a model-free generator): fall
            # back to the answer as one delta, so the client still receives the
            # text and the concatenation is still the answer.
            chunks = [answer]
        for chunk in chunks:
            if chunk:
                yield {"event": "token", "text": str(chunk)}

    # The measured latency is the time the AGENT spent, not the time the
    # consumer took to read the stream: a slow client must not inflate the
    # system's reported latency (evaluation.md sec 5 reports latency as a
    # system metric). The clock stops when the graph does.
    latency_ms = agent_elapsed_ms
    response = _build_response(final_state, latency_ms)
    # ADR-004: the run is recorded in EVERY ask trace, and the SSE surface
    # is the path the product uses (PLAN.md Day 5 builds the web client on
    # /v1/ask/stream). The trace is best-effort: it cannot fail the stream.
    record_ask(
        document_id,
        question,
        db_path=str(db_path or DEFAULT_DB_PATH),
        state=dict(final_state),
        response=response,
    )
    yield {"event": "done", "ask_response": response}
