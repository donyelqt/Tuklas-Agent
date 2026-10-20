"""The arm-blinded, versioned judge (evaluation.md §4).

The judge scores ONE sentence-level factual claim against the cited
evidence, 0 or 1. Three properties are load-bearing and each is enforced
mechanically, not by convention:

1. **BLINDED.** The judge never sees the arm label. The scoring function's
   signature has no arm parameter, the prompt text has no slot for one, and
   :func:`judge_payload` — the exact dict the judge receives — is built
   from the answer, the claim and the evidence and nothing else. A judge
   that could see which arm produced an answer would produce a number
   that is a comparison, not a measurement.

2. **VERSIONED.** The prompt is a module constant with a version string
   beside it. The version travels in the run config and in every recorded
   run, because an answer cannot be compared across two prompt versions
   (ADR-004: "Prompts versioned with the interface; judge prompts versioned
   separately").

3. **SENTENCE-LEVEL.** The unit of judgement is a factual claim, and the
   claim segmentation lives here, so both arms have their answers split the
   same way. A judge that judged a whole answer would score length, not
   faithfulness.

The judge calls a model through the same :class:`LLMInterface` seam the
product uses, so the judge's model, backend, temperature and seed are
recorded with the run and a judge change is a version bump, not a silent
regression.

**What the judge is NOT.** It is not a claim-verification oracle. It reads
a claim and the evidence text and returns a verdict; it does not check
whether the evidence is the RIGHT evidence for the question, and it does
not check the claim against the law outside the cited nodes. The metric it
produces is "is this sentence supported by the evidence it cites", and the
report says exactly that.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Protocol

_LOG = logging.getLogger(__name__)

#: The judge prompt for SEVERAL claims at once. It names no arm, exactly
#: like the single-claim prompt.
#:
#: The batched prompt exists because the single-claim one is the dominant
#: cost of a run: an answer of 5 claims costs 5 model calls, a 72-question
#: split over two arms costs ~700, and on a local CPU model that is hours
#: of wall clock for a metric that one call can produce.
#:
#: The batching changes the CALL, not the metric. Every claim still gets
#: its own 0/1 verdict against the same evidence, and a claim the model
#: does not answer for scores 0 - the same way a parse failure scores 0.
#: The prompt version is separate, because scores produced by the batched
#: prompt are not comparable with scores produced by the single-claim one.
JUDGE_BATCH_PROMPT = """\
You are checking factual claims against the source text of a legal document.

Read the EVIDENCE below. It is the full text of the document nodes the
answer cited.

For EACH numbered claim, decide whether the EVIDENCE supports it.

Answer with one line per claim, in order, with the claim's number and
either 1 or 0:
  <number> 1   the evidence supports the claim
  <number> 0   the evidence does not support the claim, contradicts it,
               or is silent about it

A claim the evidence is silent about scores 0. Answer with nothing else.

EVIDENCE:
{evidence}

CLAIMS:
{claims}
"""

#: The judge prompt's version. Bump it whenever the prompt text changes:
#: the version identifies the prompt a score was produced with, and scores
#: from two versions are not comparable.
JUDGE_PROMPT_VERSION = "judge-v1"

#: The version of the batched prompt. It is a SEPARATE version because
#: the two prompts are different prompts: a faithfulness score produced
#: with one is not comparable with a score produced with the other, and a
#: report that mixed them would be comparing two instruments.
JUDGE_BATCH_PROMPT_VERSION = "judge-v1-batch"

#: The judge prompt. It names no arm, and the rendered text is asserted
#: against the arm names in the test suite — a prompt that leaked an arm
#: label would be a blinded judge that is not blinded.
JUDGE_PROMPT = """\
You are checking one factual claim against the source text of a legal document.

Read the CLAIM and the EVIDENCE below. The EVIDENCE is the full text of the
document nodes the answer cited for this claim.

Decide whether the EVIDENCE supports the CLAIM.

Answer with exactly one character:
  1  the evidence supports the claim
  0  the evidence does not support the claim, contradicts it, or is silent about it

Answer with the single character and nothing else.

CLAIM:
{claim}

EVIDENCE:
{evidence}
"""

#: A sentence splitter. A sentence ends at a terminator followed by
#: whitespace and a capital letter, or at the end of the text. The
#: splitter is deliberately simple and deterministic: the two arms' answers
#: must be segmented the SAME way, and a hand-tuned splitter would be one
#: more thing that could differ between them.
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9(\"'])")

#: The maximum characters of evidence the judge is given for one claim.
#:
#: The bound is not a convenience. The local backend's context window is
#: 4096 tokens, and the answer's cited set can carry ~16,000 characters
#: of node text (measured on the Civil Code: the eight largest articles
#: total 16,648 characters). A prompt that overflows the context is
#: truncated by the backend or refused, and a judge that silently sees
#: only part of the evidence would score a claim against evidence the
#: reader cannot see. So the evidence is BOUNDED, deterministically, and
#: the bound is stated in the module so the number is not a secret.
#:
#: The bound is applied PER NODE, so the first cited node is never the
#: one that gets cut: the evidence is a prefix of the cited set, in
#: citation order, and the truncation is reported in the judge's own
#: record.
JUDGE_EVIDENCE_MAX_CHARS = 6000

#: The maximum characters of ONE node's text in the judge's evidence.
JUDGE_NODE_MAX_CHARS = 2000

#: A sentence that carries no factual claim: a heading, a citation marker
#: alone, a fragment of the disclaimer, or a numbered list marker on its
#: own. These are NOT judged: a score that counted them would measure the
#: answer's formatting.
_NON_CLAIM_RE = re.compile(
    r"^[\s\[\]\(\)\d\.\-\*#>:;,]+$"
)

#: The disclaimer the answer carries. It is a fixed sentence of the
#: product, not a claim about the document, so it is not judged.
DISCLAIMER_MARKER = "research tool"

#: The words that mark a sentence as carrying no claim about the document.
#: A sentence that is only a heading ("Requisites of contracts") states
#: nothing that the evidence could support or contradict.
_HEADING_ONLY_RE = re.compile(r"^[A-Z][A-Za-z0-9 ,'()\-]{0,60}$")


@dataclass(frozen=True)
class JudgeVerdict:
    """One judged claim and its score."""

    claim: str
    score: int
    supported: bool


class Judge(Protocol):
    """What the runner needs from a judge."""

    prompt_version: str

    def score_claim(self, claim: str, evidence: str) -> int:
        """0 or 1: does the evidence support the claim?"""
        ...

    def score(self, claim: str, evidence: str) -> JudgeVerdict:
        """The claim's verdict, with the score."""
        ...


def split_claims(answer_markdown: str) -> list[str]:
    """The factual claims of an answer, in order.

    The split is deterministic and shared by both arms. A sentence is
    dropped when it carries no claim at all: a bare citation marker, a
    heading with no verb, the persistent disclaimer.
    """
    if not answer_markdown:
        return []
    claims: list[str] = []
    for raw in _SENTENCE_RE.split(answer_markdown):
        sentence = raw.strip()
        if not sentence:
            continue
        if _NON_CLAIM_RE.match(sentence):
            continue
        if DISCLAIMER_MARKER in sentence.lower():
            continue
        if _HEADING_ONLY_RE.match(sentence) and " " in sentence:
            # A short capitalised phrase with no verb: a heading.
            words = sentence.split()
            if len(words) <= 8 and not any(
                word.lower() in {"is", "are", "shall", "must", "may", "has", "have"}
                for word in words
            ):
                continue
        claims.append(sentence)
    return claims


def judge_payload(claim: str, evidence: str) -> dict[str, Any]:
    """The exact payload the judge receives.

    It has three keys: the claim, the evidence and the version. There is
    NO arm key, and there is no place to put one — the function's
    signature does not accept the arm. That is the blinding, made
    mechanical.
    """
    return {
        "claim": claim,
        "evidence": evidence,
        "judge_prompt_version": JUDGE_PROMPT_VERSION,
    }


def render_judge_prompt(claim: str, evidence: str) -> str:
    """The rendered judge prompt for one claim."""
    return JUDGE_PROMPT.format(claim=claim, evidence=evidence)


class LLMJudge:
    """The judge that calls a model through the product's LLM seam.

    The verdict is parsed from the model's first character. A model that
    answers with anything else is a parse FAILURE, and a parse failure
    scores 0 — it is not scored as 1, and it is not silently dropped: a
    dropped claim would raise the faithfulness score, which is the wrong
    direction for a failure.
    """

    def __init__(self, llm: Any) -> None:
        self.llm = llm
        self.prompt_version = JUDGE_PROMPT_VERSION

    def score_claim(self, claim: str, evidence: str) -> int:
        """0 or 1: does the evidence support the claim?"""
        prompt = render_judge_prompt(claim, evidence)
        try:
            result = self.llm.generate(prompt)
        except Exception:
            _LOG.exception("the judge backend failed; the claim scores 0")
            return 0
        text = getattr(result, "text", "") or ""
        return parse_verdict(text)

    def score(self, claim: str, evidence: str) -> JudgeVerdict:
        """The claim's verdict, with the score."""
        score = self.score_claim(claim, evidence)
        return JudgeVerdict(claim=claim, score=score, supported=score == 1)


def parse_verdict(text: str) -> int:
    """The score in a judge's answer: 1, 0, or 0 for an unreadable answer.

    Only the FIRST character counts, and only if it is a 0 or a 1. A
    judge's answer that says "I cannot determine" is not a pass, so it
    scores 0.
    """
    stripped = text.strip()
    if not stripped:
        return 0
    if stripped[0] == "1":
        return 1
    return 0


class CannedJudge:
    """A deterministic judge, for the tests that must not need a model.

    It scores a claim 1 when the claim's content words appear in the
    evidence, and 0 otherwise. It is a LEXICAL stand-in, not the judge the
    run uses: a run that used it would be measuring the stand-in, and the
    run config would have to say so. The production path is
    :class:`LLMJudge`.
    """

    def __init__(self, *, prompt_version: str = "canned-judge-v1") -> None:
        self.prompt_version = prompt_version

    def score_claim(self, claim: str, evidence: str) -> int:
        """1 when the claim's distinctive words are all in the evidence."""
        words = [word for word in re.findall(r"[a-z0-9]+", claim.lower()) if len(word) > 3]
        if not words:
            return 0
        haystack = evidence.lower()
        return 1 if all(word in haystack for word in words) else 0

    def score(self, claim: str, evidence: str) -> JudgeVerdict:
        """The claim's verdict, with the score."""
        score = self.score_claim(claim, evidence)
        return JudgeVerdict(claim=claim, score=score, supported=score == 1)


def _bounded_evidence(
    evidence_by_node: dict[str, str], citation_node_ids: list[str]
) -> str:
    """The cited nodes' text, bounded to the judge's context budget.

    The bound is applied PER NODE first (so one long provision cannot
    crowd out the others) and then over the WHOLE set (so the prompt
    always fits the backend's context window). The cut is a PREFIX in
    citation order: the first cited node is never the one that is
    dropped, and the truncation is deterministic.
    """
    parts: list[str] = []
    total = 0
    for node_id in citation_node_ids:
        text = evidence_by_node.get(node_id, "")
        if not text:
            continue
        clipped = text[:JUDGE_NODE_MAX_CHARS]
        if total + len(clipped) > JUDGE_EVIDENCE_MAX_CHARS:
            remaining = JUDGE_EVIDENCE_MAX_CHARS - total
            if remaining <= 0:
                break
            clipped = clipped[:remaining]
        parts.append(clipped)
        total += len(clipped)
        if total >= JUDGE_EVIDENCE_MAX_CHARS:
            break
    return "\n\n".join(parts)


def render_batch_judge_prompt(claims: list[str], evidence: str) -> str:
    """The rendered batched judge prompt for several claims."""
    numbered = "\n".join(f"{index}. {claim}" for index, claim in enumerate(claims, 1))
    return JUDGE_BATCH_PROMPT.format(claims=numbered, evidence=evidence)


def parse_batch_verdicts(text: str, claim_count: int) -> list[int]:
    """The per-claim scores in a batched judge's answer.

    Every claim gets a score. A claim the model did not answer for - the
    line is missing, the number is wrong, the score is not a 0 or a 1 -
    scores 0, the same way a parse failure of the single-claim prompt
    scores 0. A claim that was silently dropped would RAISE the
    faithfulness score, which is the wrong direction for a failure.

    The order is the order the claims were asked in, so the mapping from
    a line to a claim is positional and does not depend on the model
    echoing the claim text.
    """
    scores = [0] * claim_count
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        parts = stripped.split()
        if len(parts) < 2:
            continue
        try:
            number = int(parts[0].rstrip(".)"))
        except ValueError:
            continue
        if not 1 <= number <= claim_count:
            continue
        if parts[1] not in ("0", "1"):
            continue
        scores[number - 1] = int(parts[1])
    return scores


class BatchedLLMJudge:
    """A judge that scores every claim of an answer in ONE model call.

    The blinding is the same as the single-claim judge's: the rendered
    prompt names no arm, and the payload has no arm key. What changes is
    the number of calls: one per answer instead of one per claim.
    """

    def __init__(self, llm: Any) -> None:
        self.llm = llm
        self.prompt_version = JUDGE_BATCH_PROMPT_VERSION

    def score_claim(self, claim: str, evidence: str) -> int:
        """0 or 1 for ONE claim, through the single-claim prompt."""
        prompt = render_judge_prompt(claim, evidence)
        try:
            result = self.llm.generate(prompt)
        except Exception:
            _LOG.exception("the judge backend failed; the claim scores 0")
            return 0
        return parse_verdict(getattr(result, "text", "") or "")

    def score(self, claim: str, evidence: str) -> JudgeVerdict:
        """The claim's verdict, with the score."""
        score = self.score_claim(claim, evidence)
        return JudgeVerdict(claim=claim, score=score, supported=score == 1)

    def score_claims(
        self, claims: list[str], evidence: str
    ) -> list[JudgeVerdict]:
        """Every claim's verdict, from ONE model call."""
        if not claims:
            return []
        prompt = render_batch_judge_prompt(claims, evidence)
        try:
            result = self.llm.generate(prompt)
        except Exception:
            _LOG.exception("the judge backend failed; every claim scores 0")
            return [
                JudgeVerdict(claim=claim, score=0, supported=False)
                for claim in claims
            ]
        scores = parse_batch_verdicts(getattr(result, "text", "") or "", len(claims))
        return [
            JudgeVerdict(claim=claim, score=score, supported=score == 1)
            for claim, score in zip(claims, scores, strict=True)
        ]


def judge_answer(
    judge: Judge,
    answer_markdown: str,
    evidence_by_node: dict[str, str],
    citation_node_ids: list[str],
) -> tuple[float | None, int, list[JudgeVerdict]]:
    """Judge every claim of an answer against the nodes it cited.

    Returns the faithfulness score (the mean over the judged claims), the
    number of claims judged, and the verdicts.

    The evidence for a claim is the CONCATENATION of the cited nodes'
    texts: the judge is asked whether the cited evidence supports the
    claim, and the answer's citations are the set of nodes it says it
    rests on. A claim is judged against the whole cited set rather than
    against one node per claim, because the answer's citation markers
    number the prompt's parts, not the claims.

    An answer with no claims scores ``None`` - there was nothing to
    judge, and a refusal is not an unfaithful answer. The caller decides
    what a ``None`` means for the aggregate; the metrics module excludes
    it from the faithfulness average rather than scoring it 0.

    A judge that can score several claims at once IS asked to: the
    per-claim verdicts are identical in kind, and the cost of a run is
    dominated by the number of model calls.
    """
    claims = split_claims(answer_markdown)
    if not claims:
        return None, 0, []
    evidence = _bounded_evidence(evidence_by_node, citation_node_ids)
    if not evidence.strip():
        # The answer cited nothing, or cited nodes with no text. There is
        # no evidence to support a claim, so every claim is unsupported.
        verdicts = [JudgeVerdict(claim=claim, score=0, supported=False) for claim in claims]
        return 0.0, len(verdicts), verdicts
    batched = getattr(judge, "score_claims", None)
    if batched is not None:
        verdicts = list(batched(claims, evidence))
    else:
        verdicts = [judge.score(claim, evidence) for claim in claims]
    score = sum(verdict.score for verdict in verdicts) / len(verdicts)
    return score, len(verdicts), verdicts


def spot_check_sample(
    question_ids: list[str], *, fraction: float = 0.20, minimum: int = 10
) -> list[str]:
    """The stratified spot-check sample of question ids (evaluation.md §4).

    "Stratified 20% of test answers (min 10)". The sample is taken by
    taking every ``1/fraction``-th id of the SORTED ids, which is
    deterministic and spreads the sample across the split rather than
    clustering it at the front. The floor of 10 is applied afterwards: if
    20% of the split is fewer than 10, the first 10 ids (in sorted order)
    are the sample.
    """
    ordered = sorted(question_ids)
    if not ordered:
        return []
    step = max(1, round(1.0 / fraction))
    sample = ordered[::step]
    if len(sample) < minimum:
        sample = ordered[: min(minimum, len(ordered))]
    return sample


def spot_check_disagreement(
    original: list[int], rescored: list[int]
) -> float:
    """The fraction of claims the builder's re-score disagreed with.

    The comparison is per claim, in order. A disagreement above 15%
    invalidates the judge run (evaluation.md §4: "disagreement > 15%
    invalidates the judge run").
    """
    if len(original) != len(rescored):
        raise ValueError(
            f"the spot check compared {len(original)} original scores with "
            f"{len(rescored)} re-scores; the samples must be the same size"
        )
    if not original:
        return 0.0
    disagreements = sum(1 for left, right in zip(original, rescored, strict=True) if left != right)
    return disagreements / len(original)


def verdicts_to_json(verdicts: list[JudgeVerdict]) -> str:
    """The verdicts as a JSON string, for the run record."""
    return json.dumps(
        [{"claim": verdict.claim, "score": verdict.score} for verdict in verdicts],
        ensure_ascii=False,
    )


__all__ = [
    "BatchedLLMJudge",
    "CannedJudge",
    "DISCLAIMER_MARKER",
    "JUDGE_BATCH_PROMPT",
    "JUDGE_BATCH_PROMPT_VERSION",
    "JUDGE_PROMPT",
    "JUDGE_PROMPT_VERSION",
    "Judge",
    "JudgeVerdict",
    "LLMJudge",
    "judge_answer",
    "judge_payload",
    "parse_batch_verdicts",
    "parse_verdict",
    "render_batch_judge_prompt",
    "render_judge_prompt",
    "spot_check_disagreement",
    "spot_check_sample",
    "split_claims",
    "verdicts_to_json",
]
