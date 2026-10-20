"""The versioned prompt renderer (architecture.md sec 6, ADR-004).

The renderer takes the OPAQUE ``PromptContext`` the Context Builder produced
(``{'parts': [{'node_id','text','score'}...], 'total_chars', 'context_truncated'}``)
and renders it. It NEVER reorders or truncates the evidence: the order and
the budget belong to the Context Builder (retrieval.md sec 5), and a
reordering here would silently invalidate the gate the agent already passed
and would break the truncation contract test
(``test_context_builder_budget``, CONSTRAINTS.md).

The prompt says three things to the model, and only those:

1. answer only from the evidence given,
2. every factual sentence carries a ``[n]`` citation,
3. refuse when the evidence does not answer the question.

The persistent disclaimer is included verbatim from
``apps/web/src/lib/copy.ts`` (the answer-pane banner) so the model states the
same framing the interface states.

CONSTRAINTS.md forbids advice phrasing in prompts, so the prompt states the
advice rule in the research-tool framing and does NOT spell the banned
phrases out. The phrase list lives in one place only - the blocklist oracle
- and this module asks that oracle whether its own text is clean.

The prompt text is VERSIONED (:data:`PROMPT_VERSION`): a prompt is part of the
recorded run, and an answer cannot be compared across two prompt versions.
"""

from __future__ import annotations

from typing import Any

from apps.api.generation.blocklist import contains_advice

#: The prompt version. Bump it whenever the rendered prompt changes: the
#: version identifies the prompt a recorded answer was produced with.
PROMPT_VERSION = "prompt-v1"

#: The persistent disclaimer, word for word as
#: ``apps/web/src/lib/copy.ts`` line 221 states it (SPEC.md sec 7 pins the
#: wording). The renderer states it so the answer carries the framing the
#: banner carries.
DISCLAIMER = (
    "Tuklas is a research discovery tool, not a lawyer. Verify provisions "
    "before relying on them."
)

#: The system constraints (architecture.md sec 6). Kept as a module constant
#: so a test can assert the three rules are present without re-spelling them.
SYSTEM_CONSTRAINTS = (
    "You are a research assistant for legal documents. Answer only from the "
    "evidence given below. Do not use prior knowledge, and do not add facts "
    "the evidence does not state. Every factual sentence must carry a "
    "citation in square brackets naming the evidence it came from. The "
    "evidence entries below are numbered; cite an entry by its number alone, "
    "written as [1] or [2], and never by its node id. Cite only the entries "
    "that actually support the sentence, and do not cite an entry you did "
    "not use. If the evidence does not answer the question, say that the "
    "evidence is insufficient and stop."
)

#: The advice rule, in the research-tool framing. CONSTRAINTS.md bans advice
#: phrasing IN prompts, so the rule is stated as a framing instruction and
#: the banned phrases are never written out.
ADVICE_RULE = (
    "Describe the documents; do not advise. Do not address the reader with a "
    "recommendation, an instruction, or a direction about what to do, and do "
    "not speak about anyone's personal situation."
)

#: The static prompt text, checked against the blocklist oracle below.
_STATIC_PROMPT_TEXT = "\n".join([SYSTEM_CONSTRAINTS, ADVICE_RULE, DISCLAIMER])


def _assert_prompt_is_clean() -> None:
    """Fail loudly if the prompt text itself trips the advice oracle.

    The oracle is the one place that knows the banned phrases, so this is
    where the prompt asks it. CONSTRAINTS.md forbids advice phrasing in
    prompts: this guard is what keeps a future edit to the prompt text from
    introducing it.
    """
    if contains_advice(_STATIC_PROMPT_TEXT):  # pragma: no cover - invariant guard
        raise RuntimeError("the prompt text must not contain advice phrasing (CONSTRAINTS.md)")


_assert_prompt_is_clean()


def _parts(context: dict[str, Any]) -> list[dict[str, Any]]:
    """The PromptContext parts, in the order the Context Builder emitted them.

    The order is preserved exactly. Nothing here sorts, filters or trims the
    list: the Context Builder owns the order and the budget (retrieval.md
    sec 5), and generation renders the payload opaquely.
    """
    raw = context.get("parts", []) if isinstance(context, dict) else []
    if not isinstance(raw, list):
        return []
    return [part for part in raw if isinstance(part, dict)]


def _render_evidence(context: dict[str, Any]) -> str:
    """The evidence block, one numbered entry per part, in order.

    Each entry is labelled with its citation number ``[n]`` and carries the
    part's text verbatim. The number is the citation the answer will use.
    """
    blocks: list[str] = []
    for index, part in enumerate(_parts(context), start=1):
        node_id = str(part.get("node_id", ""))
        text = str(part.get("text", ""))
        blocks.append(f"[{index}] node_id: {node_id}\n{text}")
    return "\n\n".join(blocks)


def _render_sub_questions(sub_questions: list[str]) -> str:
    """The sub-question list, one per line, in the planner's order."""
    return "\n".join(f"- {question}" for question in sub_questions)


def render_prompt(
    context: dict[str, Any],
    question: str,
    sub_questions: list[str],
    *,
    disclaimer: str,
) -> str:
    """Render the full prompt for one ask.

    ``context`` is the opaque PromptContext; ``question`` is the user's
    question; ``sub_questions`` are the planner's decomposition; ``disclaimer``
    is the persistent disclaimer wording, passed in so the caller owns the
    string (the web copy is its source of truth).

    The rendered prompt carries the evidence in the Context Builder's order,
    uncut.
    """
    evidence = _render_evidence(context)
    sub_block = _render_sub_questions(sub_questions)
    truncated = bool(context.get("context_truncated")) if isinstance(context, dict) else False
    truncation_note = (
        "The evidence below was shortened to fit the context budget; do not "
        "claim more than the text shows."
        if truncated
        else ""
    )
    sections = [
        f"System constraints:\n{SYSTEM_CONSTRAINTS}",
        f"Advice rule:\n{ADVICE_RULE}",
        f"Disclaimer:\n{disclaimer}",
        f"Question:\n{question}",
    ]
    if sub_block:
        sections.append(f"Sub-questions:\n{sub_block}")
    if truncation_note:
        sections.append(truncation_note)
    sections.append(f"Evidence:\n{evidence}")
    sections.append("Answer (cite every factual sentence with [n]):")
    return "\n\n".join(sections)


__all__ = [
    "ADVICE_RULE",
    "DISCLAIMER",
    "PROMPT_VERSION",
    "SYSTEM_CONSTRAINTS",
    "render_prompt",
]
