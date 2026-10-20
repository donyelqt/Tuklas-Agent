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
        result = self.llm.generate(prompt)
        self.last_result = result

        if find_advice(result.text):
            # The oracle refuses the WHOLE answer. A text that carries
            # advice phrasing is not edited into a legal answer - the
            # caller gets the uniform refusal, exactly as for a failed
            # gate.
            refusal = _refusal()
            refusal.update(self._run_record(tokens_source="provider", result=result))
            return refusal

        parts = _parts(context)
        citations = assemble_citations(parts, evidence)
        answer: dict[str, Any] = {
            "answer_markdown": result.text,
            "citations": citations,
            "insufficient": False,
            "tokens_source": "provider",
            "model": result.model,
            "backend": result.backend,
            "temperature": result.temperature,
            "seed": result.seed,
            # The provider's OWN token counts, not a character-count estimate:
            # a model was called, so its usage is known exactly.
            "prompt_tokens": result.prompt_tokens,
            "completion_tokens": result.completion_tokens,
        }
        return answer

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
