"""The retrieval, generation and system metrics (evaluation.md §3-§5).

Everything here is a pure function of the rows the runner produced. No
metric reads the corpus, the model or the clock: the runner measures, this
module counts, and a metric that could drift with the environment would be
a metric nobody could compare across two runs.

The three families:

* **Retrieval** (§3): Recall@K, Precision@K, MRR@10, macro-averaged over
  the questions of a split, reported per category and overall.
* **Generation** (§4): answer correctness, faithfulness, citation accuracy,
  abstention score. All arm-blinded — nothing in this module can see the
  arm label, because nothing in this module receives it.
* **System** (§5): retrieval latency, generation latency, total latency,
  tokens in/out, tool iterations, with p50 and p95.

The macro-average is over QUESTIONS, not over categories. A category with
three rows and a category with nine rows contribute in proportion to their
row count, which is what "macro-averaged over the test set" says. The
per-category numbers are reported alongside, so a reader can see the
distribution that the average hides.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

#: The K values evaluation.md §3 reports. Recall is reported at all four,
#: Precision at every K (the contract in api.md §4 names precision@{1,5},
#: and the extra values cost nothing and make the curve readable).
DEFAULT_K_VALUES: tuple[int, ...] = (1, 3, 5, 10)

#: The primary K: Recall@5 (evaluation.md §3, "Primary metric").
PRIMARY_K = 5

#: MRR's cut-off (evaluation.md §3: MRR@10).
MRR_CUTOFF = 10


@dataclass(frozen=True)
class RetrievalRow:
    """One question, one arm: what was retrieved, and the gold set.

    This is the row the runner writes to ``retrieval_runs`` and the row the
    retrieval metrics are computed over.
    """

    question_id: str
    arm: str
    category: str
    answerable: bool
    retrieved_node_ids: list[str]
    gold_node_ids: list[str]

    def rank_of_first_gold(self, cutoff: int = MRR_CUTOFF) -> int | None:
        """The 1-based rank of the first gold hit, or None within ``cutoff``."""
        gold = set(self.gold_node_ids)
        for position, node_id in enumerate(self.retrieved_node_ids[:cutoff], start=1):
            if node_id in gold:
                return position
        return None

    def recall_at(self, k: int) -> float:
        """|retrieved[:k] ∩ gold| / |gold| (evaluation.md §3).

        A question with NO gold set has no recall to compute: the
        unanswerable rows are scored by the abstention metric, not by
        recall. Scoring them 0.0 would drag the aggregate down for a
        question the recall metric does not apply to, so they are EXCLUDED
        from the retrieval aggregate, not scored as zero.
        """
        if not self.gold_node_ids:
            return 0.0
        gold = set(self.gold_node_ids)
        hits = sum(1 for node_id in self.retrieved_node_ids[:k] if node_id in gold)
        return hits / len(gold)

    def precision_at(self, k: int) -> float:
        """|retrieved[:k] ∩ gold| / min(k, |retrieved|).

        The denominator is the number of slots actually filled, so a
        question that retrieved three nodes and got one of them right
        scores 1/3, not 1/10. A system that returns three good hits and a
        system that returns ten good hits are not equally precise, and
        neither is a system that returns three hits and a system that
        returns nothing.
        """
        retrieved = self.retrieved_node_ids[:k]
        if not retrieved:
            return 0.0
        gold = set(self.gold_node_ids)
        hits = sum(1 for node_id in retrieved if node_id in gold)
        return hits / len(retrieved)

    def reciprocal_rank(self, cutoff: int = MRR_CUTOFF) -> float:
        """1 / rank(first gold hit within ``cutoff``), else 0."""
        rank = self.rank_of_first_gold(cutoff)
        return 0.0 if rank is None else 1.0 / rank

    def gold_hit(self, k: int = PRIMARY_K) -> bool:
        """True when at least one gold node is in the top-``k``."""
        gold = set(self.gold_node_ids)
        return any(node_id in gold for node_id in self.retrieved_node_ids[:k])


@dataclass(frozen=True)
class GenerationRow:
    """One question, one arm: the answer and how it was scored.

    The arm label is carried as a field for JOINING, and for nothing
    else: the generation metrics are computed from these rows WITHOUT it,
    and the judge never receives it. A row that knew its arm could still
    not leak it into a score, because no metric function reads the field
    — the aggregation is per-arm by CONSTRUCTION (the runner filters the
    rows before it aggregates), not by the metric looking at the label.
    """

    question_id: str
    arm: str
    category: str
    answerable: bool
    answer_markdown: str
    claims_matched: int
    claims_total: int
    faithfulness: float | None
    citations_valid: bool
    insufficient: bool
    citation_node_ids: list[str] = field(default_factory=list)
    judged_claims: int = 0


@dataclass(frozen=True)
class SystemRow:
    """One question, one arm: the measured cost of the ask."""

    question_id: str
    arm: str
    retrieval_latency_ms: int
    generation_latency_ms: int
    total_latency_ms: int
    tokens_in: int
    tokens_out: int
    tool_iterations: int


def _mean(values: Sequence[float]) -> float:
    """The arithmetic mean, 0.0 for an empty sequence."""
    if not values:
        return 0.0
    return sum(values) / len(values)


def percentile(values: Sequence[float], fraction: float) -> float:
    """The ``fraction``-quantile of ``values``, by linear interpolation.

    The interpolation is the same rule ``numpy.percentile`` uses by
    default, and it is spelled out here so the number does not depend on a
    numpy version. With a single value it returns that value; with none it
    returns 0.0.
    """
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return ordered[0]
    position = fraction * (len(ordered) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[int(position)]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def retrieval_aggregates(
    rows: Iterable[RetrievalRow],
    *,
    k_values: Sequence[int] = DEFAULT_K_VALUES,
) -> dict[str, Any]:
    """The retrieval aggregate: overall and per category.

    The overall numbers are macro-averaged over the rows that have a gold
    set. The per-category numbers are computed over each category's own
    rows, so a category's average is the average of ITS questions and not
    a share of the whole.
    """
    materialised = list(rows)
    scored = [row for row in materialised if row.gold_node_ids]

    def aggregate(subset: Sequence[RetrievalRow]) -> dict[str, Any]:
        payload: dict[str, Any] = {"n": len(subset)}
        for k in k_values:
            payload[f"recall@{k}"] = _mean([row.recall_at(k) for row in subset])
        for k in k_values:
            payload[f"precision@{k}"] = _mean([row.precision_at(k) for row in subset])
        payload["mrr10"] = _mean([row.reciprocal_rank(MRR_CUTOFF) for row in subset])
        return payload

    by_category: dict[str, list[RetrievalRow]] = defaultdict(list)
    for row in materialised:
        by_category[row.category].append(row)

    return {
        "overall": aggregate(scored),
        "by_category": {
            category: aggregate([row for row in rows_ if row.gold_node_ids])
            for category, rows_ in sorted(by_category.items())
        },
        "n_rows": len(materialised),
        "n_scored": len(scored),
        "n_unanswerable_excluded": len(materialised) - len(scored),
    }


def generation_aggregates(rows: Iterable[GenerationRow]) -> dict[str, Any]:
    """The generation aggregate (evaluation.md §4).

    * ``correctness`` — the mean over answerable questions of the fraction
      of expected claims the answer matched. An unanswerable question has
      no expected claims, so it is EXCLUDED from this aggregate rather
      than scored as zero.
    * ``faithfulness`` — the mean over the rows the judge scored. A row
      the judge did not score (an abstention, which has no factual claim
      to check) is excluded, not scored as zero: a refusal is not an
      unfaithful answer, and counting it as one would measure the
      abstention rate twice.
    * ``citation_accuracy`` — the fraction of rows whose citations all
      exist and support their claim.
    * ``abstention_score`` — over the UNANSWERABLE rows only: a refusal
      scores 1, any cited answer scores 0.
    """
    materialised = list(rows)
    answerable = [row for row in materialised if row.answerable]
    unanswerable = [row for row in materialised if not row.answerable]
    judged = [row for row in materialised if row.faithfulness is not None]

    correctness = _mean(
        [
            (row.claims_matched / row.claims_total) if row.claims_total else 0.0
            for row in answerable
        ]
    )
    faithfulness = _mean(
        [float(row.faithfulness) for row in judged if row.faithfulness is not None]
    )
    citation_accuracy = (
        _mean([1.0 if row.citations_valid else 0.0 for row in materialised])
        if materialised
        else 0.0
    )
    abstention = _mean(
        [
            1.0 if (row.insufficient and not row.citation_node_ids) else 0.0
            for row in unanswerable
        ]
    )
    return {
        "correctness": correctness,
        "faithfulness": faithfulness,
        "citation_accuracy": citation_accuracy,
        "abstention_score": abstention,
        "n_answerable": len(answerable),
        "n_unanswerable": len(unanswerable),
        "n_judged": len(judged),
    }


def system_aggregates(rows: Iterable[SystemRow]) -> dict[str, Any]:
    """The system aggregate (evaluation.md §5): p50 and p95 per measure.

    The latency split is reported for retrieval, generation and total, and
    the token counts and the tool-iteration count are reported as means.
    A more accurate but 10x slower arm is a tradeoff, and the report
    publishes both sides of it — evaluation.md §5.
    """
    materialised = list(rows)
    retrieval = [float(row.retrieval_latency_ms) for row in materialised]
    generation = [float(row.generation_latency_ms) for row in materialised]
    total = [float(row.total_latency_ms) for row in materialised]
    return {
        "latency_p50": percentile(total, 0.50),
        "latency_p95": percentile(total, 0.95),
        "retrieval_latency_p50": percentile(retrieval, 0.50),
        "retrieval_latency_p95": percentile(retrieval, 0.95),
        "generation_latency_p50": percentile(generation, 0.50),
        "generation_latency_p95": percentile(generation, 0.95),
        "tokens_mean": _mean([float(row.tokens_in) for row in materialised]),
        "tokens_out_mean": _mean([float(row.tokens_out) for row in materialised]),
        "iterations_mean": _mean([float(row.tool_iterations) for row in materialised]),
        "n": len(materialised),
    }


__all__ = [
    "DEFAULT_K_VALUES",
    "MRR_CUTOFF",
    "PRIMARY_K",
    "GenerationRow",
    "RetrievalRow",
    "SystemRow",
    "generation_aggregates",
    "percentile",
    "retrieval_aggregates",
    "system_aggregates",
]
