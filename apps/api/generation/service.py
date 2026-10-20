"""The generation service — refusal path, blocklist enforcement, answer assembly.

Three rules decide what the service returns:

1. **The GATE decides sufficiency, not the presence of evidence.** When the
   gate failed (SPEC.md sec 6, api.md sec 3), the service returns the CANNED
   ABSTENTION string, ``citations == []`` and ``insufficient == True``. The
   string is the one pinned in api.md sec 3 and defined in
   ``apps/api/agent/graph.py`` as ``CANNED_ABSTENTION``; it is REUSED here,
   not re-spelled, so the two can never drift.
2. **The advice oracle refuses.** The generated text is checked against the
   single blocklist oracle. When it trips, the service refuses with the SAME
   uniform shape as the gate failure: the canned abstention, no citations,
   ``insufficient == True``. A partially-advice answer is never returned —
   a refusal is the whole answer.
3. **The run is recorded.** The result carries the model id, the backend, the
   temperature and the seed, because ADR-004 requires them in every ask
   trace and eval run.

A backend that is down raises ``LLM_UNAVAILABLE`` (503). It never degrades
to a fake answer (ADR-004 "Consequences").
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from apps.api.generation.blocklist import find_advice
from apps.api.generation.citations import assemble_citations
from apps.api.generation.interface import (
    DEFAULT_BACKEND,
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    DEFAULT_SEED,
    DEFAULT_TEMPERATURE,
    DEFAULT_TIMEOUT,
    GenerationResult,
    LLMInterface,
)
from apps.api.generation.prompts import DISCLAIMER, PROMPT_VERSION, render_prompt


def _parts(context: dict[str, Any]) -> list[dict[str, Any]]:
    """The PromptContext parts, in the Context Builder's order."""
    raw = context.get("parts", []) if isinstance(context, dict) else []
    if not isinstance(raw, list):
        return []
    return [part for part in raw if isinstance(part, dict)]


def _refusal() -> dict[str, Any]:
    """The uniform refusal shape (api.md sec 3).

    The abstention returns the SAME shape as a successful answer: the canned
    text, no citations, and ``insufficient`` true.
    """
    # The abstention wording is pinned in api.md sec 3 and DEFINED in
    # apps/api/agent/graph.py. It is imported here rather than re-spelled, so
    # the two can never drift. The import is function-local because the agent
    # module is the one that will call this service: a module-level import
    # here would close a cycle the moment the agent imports generation.
    from apps.api.agent.graph import CANNED_ABSTENTION

    return {
        "answer_markdown": CANNED_ABSTENTION,
        "citations": [],
        "insufficient": True,
    }


class GenerationService:
    """Turn a PromptContext into an answer, or into a refusal.

    Constructed with the LLM interface, so the run's backend, model,
    temperature and seed are the ones the interface actually used.
    """

    def __init__(self, llm: LLMInterface) -> None:
        """Bind the LLM interface that produces the text."""
        self.llm = llm
        #: The last result the backend returned, with its token counts. The
        #: answer dict carries the run parameters (ADR-004); the provider's
        #: own prompt/completion counts live here, because the answer dict's
        #: shape is fixed by the contract.
        self.last_result: GenerationResult | None = None
        #: The provider's deltas for the last answer, in the backend's order.
        self.last_chunks: list[str] = []

    def answer(
        self,
        context: dict[str, Any],
        question: str,
        sub_questions: list[str],
        evidence: dict[str, Any],
        *,
        gate_passed: bool,
    ) -> dict[str, Any]:
        """Answer one question from the rendered context.

        Returns ``{answer_markdown, citations, insufficient, tokens_source,
        model, backend, temperature, seed}``.

        - The gate failed -> the canned abstention, no citations,
          ``insufficient`` true. No model is called: a refused question costs
          no generation and fabricates nothing.
        - The generated text trips the advice oracle -> the same refusal
          shape. The generated text is discarded whole; the provider call
          did happen, so ``tokens_source`` is "provider".
        - Otherwise -> the model's answer, with the citations assembled from
          the evidence.

        ``tokens_source`` is None when no provider call happened (the gate
        failed). The transport layer omits the key in that case, which is the
        house convention for "no token count to report"
        (apps/api/agent/ask.py). "provider" means the backend reported its
        own counts.
        """
        if not gate_passed:
            refusal = _refusal()
            refusal.update(self._run_record(tokens_source=None))
            return refusal

        prompt = render_prompt(
            context,
            question,
            sub_questions,
            disclaimer=DISCLAIMER,
        )
        # The answer is produced by the BACKEND'S streaming endpoint when the
        # interface offers one, so the deltas the client will see are the ones
        # the model emitted, in its order - not a slicing of a finished string.
        # An interface that only offers the single-shot call still works: the
        # answer arrives as one delta.
        stream_result = getattr(self.llm, "stream_result", None)
        if callable(stream_result):
            result, chunks = stream_result(prompt)
            self.last_chunks = list(chunks)
        else:
            result = self.llm.generate(prompt)
            self.last_chunks = [result.text] if result.text else []
        self.last_result = result

        if find_advice(result.text):
            # The oracle refuses the WHOLE answer. A text that carries
            # advice phrasing is not edited into a legal answer - the
            # caller gets the uniform refusal, exactly as for a failed
            # gate.
            refusal = _refusal()
            refusal.update(
                self._run_record(
                    tokens_source=self._token_source(result), result=result
                )
            )
            return refusal

        parts = _parts(context)
        citations = assemble_citations(parts, evidence)
        answer: dict[str, Any] = {
            "answer_markdown": result.text,
            "citations": citations,
            "insufficient": False,
            # "provider" means the BACKEND REPORTED the counts. If it
            # reported none, the label must not say so: the ask surface
            # falls back to the character-count estimate and says THAT
            # instead (api.md sec 3: a consumer must not publish estimated
            # numbers as measured usage - and zeros labelled as measured are
            # worse, because they look real).
            "tokens_source": self._token_source(result),
            "model": result.model,
            "backend": result.backend,
            "temperature": result.temperature,
            "seed": result.seed,
            # The prompt is part of the run: an answer cannot be compared
            # across two prompt versions (ADR-004, "Prompts versioned with
            # the interface").
            "prompt_version": PROMPT_VERSION,
            # The provider's OWN token counts, not a character-count estimate:
            # a model was called, so its usage is known exactly.
            "prompt_tokens": result.prompt_tokens,
            "completion_tokens": result.completion_tokens,
            # The deltas the SSE surface streams. They concatenate to exactly
            # the answer text, so a client that joins them sees the same
            # answer it would have got in one piece.
            "stream": self.last_chunks,
        }
        return answer

    def stream(
        self,
        context: dict[str, Any],
        question: str,
        sub_questions: list[str],
        evidence: dict[str, Any],
        *,
        gate_passed: bool,
    ) -> Iterator[str]:
        """The answer's TEXT as the backend produces it (ADR-004).

        The SSE surface streams these deltas, so the client sees the answer
        arrive rather than a finished string cut into pieces.

        The refusal path yields nothing: a failed gate produces the canned
        abstention as a normal (non-streamed) answer, and a tripped advice
        oracle discards the whole generated text. So the stream carries the
        answer only when the gate passed AND the text is clean - which means
        the text is buffered to check the oracle, and only then released as
        the deltas that produced it. The deltas are the backend's real ones,
        in the backend's order.
        """
        if not gate_passed:
            return
        prompt = render_prompt(
            context, question, sub_questions, disclaimer=DISCLAIMER
        )
        chunks = list(self.llm.generate_stream(prompt))
        text = "".join(chunks)
        if find_advice(text):
            # The oracle refuses the WHOLE answer; there is nothing to
            # stream. The caller falls back to the uniform refusal.
            return
        yield from chunks

    def generate(
        self,
        context: dict[str, Any],
        question: str,
        sub_questions: list[str],
        evidence: dict[str, Any],
        *,
        gate_passed: bool,
    ) -> dict[str, Any]:
        """The agent's generation seam (apps/api/agent/graph.py ``Generator``).

        The agent calls the generator with the PromptContext, the question,
        the sub-questions, the evidence and the GATE VERDICT, and reads back
        the answer dict. This method is that seam verbatim: it delegates to
        :meth:`answer`, which owns the refusal path and the advice oracle
        (architecture.md sec 6).

        The two names are one behaviour. ``answer`` is the descriptive name
        and ``generate`` is the seam the agent calls; keeping both means the
        service satisfies the agent's Protocol without the agent knowing the
        service's internal naming.
        """
        return self.answer(
            context,
            question,
            sub_questions,
            evidence,
            gate_passed=gate_passed,
        )

    @staticmethod
    def _token_source(result: GenerationResult) -> str | None:
        """The label for the token counts, derived from what the backend said.

        "provider" means the backend REPORTED the counts. If it reported
        none, or only one side of them, the label is None: the transport
        falls back to the character-count estimate and says so. A "provider"
        label over a fabricated zero is worse than an estimate, because it
        looks measured.
        """
        if result.prompt_tokens is not None and result.completion_tokens is not None:
            return "provider"
        return None

    def _run_record(
        self, *, tokens_source: str | None, result: GenerationResult | None = None
    ) -> dict[str, Any]:
        """The recorded run parameters (ADR-004).

        Model id, backend, temperature and seed travel with every answer so
        an ask trace and an eval run can name the run that produced them.

        ``result`` is the backend's own record when there is one: the backend
        reports the model it actually ran. Without a result (the gate
        refused before any call), the interface's configured values are the
        run that was requested.
        """
        return {
            "tokens_source": tokens_source,
            "model": result.model if result is not None else self.llm.model,
            "backend": result.backend if result is not None else self.llm.backend,
            "temperature": result.temperature if result is not None else self.llm.temperature,
            "seed": result.seed if result is not None else self.llm.seed,
        }


def build_generator(
    backend: str = "ollama",
    model: str = DEFAULT_MODEL,
    *,
    base_url: str = DEFAULT_BASE_URL,
    temperature: float = DEFAULT_TEMPERATURE,
    seed: int = DEFAULT_SEED,
    timeout: float = DEFAULT_TIMEOUT,
) -> GenerationService:
    """The production generator: the real LLM behind the agent's seam.

    The returned object satisfies the agent's ``Generator`` Protocol, so the
    agent can be handed it directly. The sampling parameters default to the
    recorded ones (temperature 0, pinned seed) because ADR-004 requires the
    run's parameters to be recorded, and an unrecorded parameter records
    nothing.
    """
    return GenerationService(
        LLMInterface(
            backend=backend,
            model=model,
            base_url=base_url,
            temperature=temperature,
            seed=seed,
            timeout=timeout,
        )
    )


__all__ = [
    "DEFAULT_BACKEND",
    "DEFAULT_BASE_URL",
    "DEFAULT_MODEL",
    "DEFAULT_SEED",
    "DEFAULT_TEMPERATURE",
    "PROMPT_VERSION",
    "GenerationService",
    "build_generator",
]
