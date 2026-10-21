"""The judge spot check (evaluation.md §4).

    "Spot check: stratified 20% of test answers (min 10) re-scored by the
    builder; disagreement > 15% invalidates the judge run (remedy: fix
    prompt, new run id, re-score all)."

**What this module measures, precisely.** It re-scores a stratified
sample of the run's judged claims and reports the fraction on which the
re-score disagrees with the original. That number is the DISAGREEMENT,
and it is the number the 15% threshold applies to.

**What the re-score is, and what it is not.** The re-score here is a
MECHANICAL re-judging: the same versioned prompt, sent to the same
backend, at the same temperature and seed, over the same evidence. It
therefore measures the judge's SELF-CONSISTENCY — whether the judge gives
the same verdict twice — and NOT the agreement between a human builder
and the judge. A human builder's re-score is a different measurement, and
the report says which one was performed, so a reader knows what the
number is.

The distinction matters because the two numbers answer different
questions, and reporting one as the other would be a mislabelled metric:
* the self-consistency number answers "is the judge stable?", and
* the builder's number answers "does the judge agree with a careful
  human?".
The second is the one evaluation.md §4 asks for. This module computes the
first, and the report names it as such.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from apps.api.evaluation.judge import (
    Judge,
    judge_answer,
    spot_check_disagreement,
    spot_check_sample,
)


@dataclass(frozen=True)
class SpotCheckResult:
    """The outcome of one spot check."""

    performed: bool
    method: str
    sample_size: int
    n_claims_compared: int
    disagreement: float | None
    threshold: float
    valid: bool | None
    reason: str = ""
    sample: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        """The result as the run record and the report carry it."""
        return {
            "performed": self.performed,
            "method": self.method,
            "sample_size": self.sample_size,
            "n_claims_compared": self.n_claims_compared,
            "disagreement": self.disagreement,
            "threshold": self.threshold,
            "valid": self.valid,
            "reason": self.reason,
            "sample": self.sample or [],
        }


#: The threshold evaluation.md §4 fixes: above 15% the judge run is
#: invalid.
DISAGREEMENT_THRESHOLD = 0.15

#: The method label. It names WHAT was done, so a reader is never left
#: guessing whether a number is a human judgement or a re-judging.
METHOD_MECHANICAL = "mechanical-rejudge (judge self-consistency)"
METHOD_BUILDER = "builder-rescore (human re-scoring)"


def select_sample(question_ids: list[str]) -> list[str]:
    """The stratified 20% sample of question ids (min 10)."""
    return spot_check_sample(question_ids)


def rescore_sample(
    judge: Judge,
    rows: list[dict[str, Any]],
    *,
    evidence_by_row: dict[str, dict[str, str]],
    threshold: float = DISAGREEMENT_THRESHOLD,
) -> SpotCheckResult:
    """Re-score a sample of the run's rows and compute the disagreement.

    ``rows`` are the run's per-question rows, each with a
    ``question_id``, an ``arm``, an ``answer_markdown``, a
    ``citation_node_ids`` and a ``judge_verdicts`` JSON string.

    ``evidence_by_row`` maps ``"<question_id>:<arm>"`` to the evidence
    text of that row's cited nodes, so the re-score sees the SAME
    evidence the original saw.
    """
    question_ids = sorted({str(row["question_id"]) for row in rows})
    sample = select_sample(question_ids)
    sample_set = set(sample)

    original: list[int] = []
    rejudged: list[int] = []
    compared_rows = 0
    for row in rows:
        question_id = str(row["question_id"])
        if question_id not in sample_set:
            continue
        arm = str(row["arm"])
        key = f"{question_id}:{arm}"
        evidence = evidence_by_row.get(key, {})
        citation_ids = [str(node) for node in (row.get("citation_node_ids") or [])]
        answer = str(row.get("answer_markdown") or "")
        stored = json.loads(str(row.get("judge_verdicts") or "[]"))
        stored_scores = [int(verdict["score"]) for verdict in stored]
        if not stored_scores:
            continue
        _score, _judged, verdicts = judge_answer(judge, answer, evidence, citation_ids)
        if not verdicts:
            continue
        compared_rows += 1
        original.extend(stored_scores)
        rejudged.extend(verdict.score for verdict in verdicts)

    if not original:
        return SpotCheckResult(
            performed=False,
            method=METHOD_MECHANICAL,
            sample_size=len(sample),
            n_claims_compared=0,
            disagreement=None,
            threshold=threshold,
            valid=None,
            reason="the sample covered no judged claims",
            sample=sample,
        )

    disagreement = spot_check_disagreement(original, rejudged)
    return SpotCheckResult(
        performed=True,
        method=METHOD_MECHANICAL,
        sample_size=len(sample),
        n_claims_compared=len(original),
        disagreement=disagreement,
        threshold=threshold,
        valid=disagreement <= threshold,
        sample=sample,
    )


__all__ = [
    "DISAGREEMENT_THRESHOLD",
    "METHOD_BUILDER",
    "METHOD_MECHANICAL",
    "SpotCheckResult",
    "rescore_sample",
    "select_sample",
]
