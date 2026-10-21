"""The run report (evaluation.md §7: "Reading the results honestly").

The report is the deliverable, and it has one job: to let a reader who did
not run it judge whether the numbers mean what they appear to mean. So it
states, in this order:

1. **The run's identity** — n, the split seed, the model and backend, the
   judge prompt version, the arms, the dataset, the config and its id.
2. **The arms' asymmetry** — the vectorless arm applies identifier
   precedence (ADR-005) and the dense arm has no ident component at all,
   so the arms differ in more than the retrieval mechanism, and the
   ``direct-lookup`` category is not a like-for-like result.
3. **The numbers** — per arm, per category and overall, with no comparison
   verdict. The comparison's OUTCOME is unknown until it is measured, and
   a report that names a winner is making a claim the spec forbids.
4. **The limitations that the numbers carry** — the measured ones, stated
   with their measurements.

There is NO place in this module that says one arm did better than the
other. The report prints both arms' numbers side by side and stops.
"""

from __future__ import annotations

import json
from typing import Any

#: The note that every report carries about the arms' asymmetry. It is a
#: CONSTANT, not a parameter, because it is a fact about the system and
#: not about any particular run.
ASYMMETRY_NOTE = (
    "The two arms differ in more than the retrieval MECHANISM. The vectorless "
    "arm applies identifier precedence (ADR-005): a query that names a "
    "provision puts that provision first, and the arm has an 'ident' scoring "
    "component. The dense arm has NO ident component at all - it embeds the "
    "query, so it can match a provision semantically, but it has no symbolic "
    "label channel. The 'direct-lookup' category is therefore not a "
    "like-for-like result, and the arms' per-category numbers must be read "
    "with that in mind. The outcome of the comparison is UNKNOWN; this report "
    "publishes both arms' numbers and no verdict."
)

#: The note about the 'ref' component, which is structurally zero on this
#: corpus because the references_ table is empty.
REF_COMPONENT_NOTE = (
    "The 'ref' scoring component is 0 for every node on this corpus. Its only "
    "edge source is the references_ table, and ingestion does not populate it "
    "(apps/api/ingestion/service.py: 'Day 1: no reference parsing; references_ "
    "table stays empty'), so the section 3 formula runs as lex + ident + title "
    "+ 0 + hier. A component breakdown that does not say so would leave the "
    "reader to discover it."
)

#: The note about what the abstention score measures.
ABSTENTION_NOTE = (
    "The abstention score measures the system's ability to refuse a question "
    "whose VOCABULARY is absent from the corpus. That is a weaker test than "
    "refusing a question that is out-of-domain in substance: the dataset's "
    "unanswerable rows are built out of a vocabulary the corpus does not "
    "contain at all, because the abstention gate is lexical and a single "
    "matching token gives lex = 1.0. The score is a lower bound on refusal "
    "quality, not a measurement of it."
)

#: The note about the dense arm's sufficiency gate.
#:
#: This is a STRUCTURAL asymmetry between the arms, and it is measured,
#: not theorised. The vectorless arm's ``lex`` layer is all-zero when no
#: query term matches any node, and the support rule ("lex > 0") then
#: fails for every node, so the gate fails closed and the run abstains.
#: The dense arm's score is a COSINE SIMILARITY, which is non-zero for
#: every node against every query; the min-max normalization then gives
#: the nearest neighbour a lex of 1.0, so the support rule always passes
#: and the gate never fails on lexical grounds.
#:
#: The consequence is that the dense arm's abstention score is
#: STRUCTURALLY 0 on this dataset, and that number is a property of the
#: arm's ranking, not a measurement of its refusal quality. A reader who
#: compares the two arms' abstention scores is comparing a refusal
#: measurement with a constant.
DENSE_GATE_NOTE = (
    "The dense arm's sufficiency gate CANNOT FAIL on lexical grounds, and "
    "this is structural rather than a defect in the run. The support rule "
    "is 'the node's lex component is above 0'. The vectorless arm's lex "
    "layer is ALL-ZERO when no query term matches any node, so its gate "
    "fails closed and the run abstains. The dense arm's score is a cosine "
    "similarity, which is non-zero for every node against every query; the "
    "min-max normalization then gives the nearest neighbour a lex of 1.0, "
    "so the support rule always passes. The dense arm's abstention score is "
    "therefore STRUCTURALLY 0 on this dataset, and that number is a "
    "property of the arm's ranking, not a measurement of its refusal "
    "quality. Comparing the two arms' abstention scores compares a refusal "
    "measurement with a constant."
)


def _fmt(value: float | None, places: int = 3) -> str:
    """A number for the report, or "n/a" for a missing one."""
    if value is None:
        return "n/a"
    return f"{value:.{places}f}"


def _arm_lines(name: str, aggregates: dict[str, Any]) -> list[str]:
    """The metric lines of one arm."""
    retrieval = aggregates.get("retrieval", {})
    overall = retrieval.get("overall", {})
    generation = aggregates.get("generation", {})
    system = aggregates.get("system", {})
    lines: list[str] = []
    lines.append(f"  arm {name}")
    lines.append(
        f"    retrieval (n={overall.get('n', 0)}): "
        f"recall@5={_fmt(overall.get('recall@5'))} "
        f"recall@1={_fmt(overall.get('recall@1'))} "
        f"recall@3={_fmt(overall.get('recall@3'))} "
        f"recall@10={_fmt(overall.get('recall@10'))}"
    )
    lines.append(
        f"    precision: "
        f"@1={_fmt(overall.get('precision@1'))} "
        f"@3={_fmt(overall.get('precision@3'))} "
        f"@5={_fmt(overall.get('precision@5'))} "
        f"@10={_fmt(overall.get('precision@10'))}"
    )
    lines.append(f"    mrr@10={_fmt(overall.get('mrr10'))}")
    lines.append(
        f"    generation: correctness={_fmt(generation.get('correctness'))} "
        f"faithfulness={_fmt(generation.get('faithfulness'))} "
        f"citation_accuracy={_fmt(generation.get('citation_accuracy'))} "
        f"abstention={_fmt(generation.get('abstention_score'))}"
    )
    lines.append(
        f"    system: total p50={_fmt(system.get('latency_p50'), 1)}ms "
        f"p95={_fmt(system.get('latency_p95'), 1)}ms | "
        f"retrieval p50={_fmt(system.get('retrieval_latency_p50'), 1)}ms "
        f"p95={_fmt(system.get('retrieval_latency_p95'), 1)}ms | "
        f"generation p50={_fmt(system.get('generation_latency_p50'), 1)}ms "
        f"p95={_fmt(system.get('generation_latency_p95'), 1)}ms"
    )
    lines.append(
        f"    tokens: in mean={_fmt(system.get('tokens_mean'), 1)} "
        f"out mean={_fmt(system.get('tokens_out_mean'), 1)} | "
        f"tool iterations mean={_fmt(system.get('iterations_mean'), 2)}"
    )
    return lines


def _category_lines(aggregates: dict[str, Any]) -> list[str]:
    """The per-category retrieval lines, per arm."""
    lines: list[str] = []
    arms = aggregates.get("arms", {})
    categories: list[str] = []
    for name in sorted(arms):
        for category in sorted(arms[name].get("retrieval", {}).get("by_category", {})):
            if category not in categories:
                categories.append(category)
    if not categories:
        return lines
    lines.append("  per category (recall@5 / mrr@10):")
    for category in categories:
        parts: list[str] = []
        for name in sorted(arms):
            entry = arms[name].get("retrieval", {}).get("by_category", {}).get(category, {})
            parts.append(
                f"{name} {_fmt(entry.get('recall@5'))} / {_fmt(entry.get('mrr10'))} (n={entry.get('n', 0)})"
            )
        lines.append(f"    {category}: " + " | ".join(parts))
    return lines


def render_report(
    *,
    evaluation_id: str,
    config: dict[str, Any],
    dataset_name: str,
    dataset_snapshot: str,
    split_seed: int,
    n_questions: int,
    aggregates: dict[str, Any],
    spot_check: dict[str, Any] | None = None,
    dense_arm_description: dict[str, Any] | None = None,
    vectorless_arm_description: dict[str, Any] | None = None,
) -> str:
    """The run report, as text.

    The report names the run, the arms' asymmetry, both arms' numbers, and
    the measured limitations. It states no verdict on the comparison.
    """
    config_body = config.get("config", {}) if isinstance(config, dict) else {}
    lines: list[str] = []
    lines.append("=" * 78)
    lines.append(f"Evaluation report {evaluation_id}")
    lines.append("=" * 78)
    lines.append("")
    lines.append("THE RUN")
    lines.append(f"  n questions          : {n_questions}")
    lines.append(f"  split                : {dataset_name} (snapshot {dataset_snapshot})")
    lines.append(f"  split seed           : {split_seed}")
    lines.append(f"  model / backend      : {config_body.get('model')} / {config_body.get('backend')}")
    lines.append(
        f"  temperature / seed   : {config_body.get('temperature')} / {config_body.get('seed')}"
    )
    lines.append(f"  judge prompt version : {config_body.get('judge_prompt_version')}")
    lines.append(f"  code version         : {config_body.get('code_version')}")
    lines.append(f"  config id            : {config.get('config_id')}")
    lines.append(f"  weights              : {json.dumps(config_body.get('weights'), sort_keys=True)}")
    lines.append(f"  normalization        : {config_body.get('normalization')}")
    lines.append(f"  fuzzy threshold      : {config_body.get('fuzzy_threshold')}")
    lines.append(f"  stoplist version     : {config_body.get('stoplist_version')}")
    lines.append(f"  K values             : {config.get('k')}")
    lines.append(f"  arms                 : {list(aggregates.get('arms', {}).keys())}")
    if vectorless_arm_description:
        lines.append(
            f"  vectorless arm       : {vectorless_arm_description.get('algorithm')}, "
            f"identifier_precedence="
            f"{vectorless_arm_description.get('identifier_precedence')}, "
            f"dense={vectorless_arm_description.get('dense')}"
        )
    if dense_arm_description:
        lines.append(
            f"  dense arm            : {dense_arm_description.get('model')} "
            f"({dense_arm_description.get('provider')}), "
            f"dim={dense_arm_description.get('dim')}, "
            f"revision={dense_arm_description.get('revision')}, "
            f"dense={dense_arm_description.get('dense')}, "
            f"identifier_precedence={dense_arm_description.get('identifier_precedence')}"
        )
    lines.append("")
    lines.append("THE ARMS ARE NOT LIKE-FOR-LIKE (read before the numbers)")
    for line in _wrap(ASYMMETRY_NOTE, 74):
        lines.append(f"  {line}")
    lines.append("")
    lines.append("THE NUMBERS (per arm; no comparison verdict)")
    for name in sorted(aggregates.get("arms", {})):
        lines.extend(_arm_lines(name, aggregates["arms"][name]))
    lines.append("")
    lines.extend(_category_lines(aggregates))
    lines.append("")
    if spot_check is not None:
        lines.append("THE JUDGE SPOT CHECK (evaluation.md §4)")
        if spot_check.get("performed"):
            lines.append(
                f"  sample: {spot_check.get('sample_size')} answers, "
                f"{spot_check.get('n_claims_compared')} claims re-scored"
            )
            lines.append(
                f"  disagreement: {_fmt(spot_check.get('disagreement'))} "
                f"(threshold {spot_check.get('threshold')}) - "
                f"{'WITHIN' if spot_check.get('valid') else 'ABOVE'} the threshold"
            )
        else:
            lines.append(f"  NOT PERFORMED: {spot_check.get('reason')}")
            lines.append(
                "  An unmeasured disagreement is not 0% disagreement; it is an "
                "open measurement."
            )
        lines.append("")
    lines.append("MEASURED LIMITATIONS")
    for note in (REF_COMPONENT_NOTE, ABSTENTION_NOTE, DENSE_GATE_NOTE):
        for line in _wrap(note, 74):
            lines.append(f"  {line}")
        lines.append("")
    lines.append("The outcome of the vectorless-versus-dense comparison is not")
    lines.append("claimed here. The numbers above are what was measured; what")
    lines.append("they mean is a reading, and this report does not make it.")
    lines.append("=" * 78)
    return "\n".join(lines)


def _wrap(text: str, width: int) -> list[str]:
    """Wrap a paragraph to ``width`` characters, on word boundaries."""
    words = text.split()
    lines: list[str] = []
    current: list[str] = []
    length = 0
    for word in words:
        if length + len(word) + 1 > width and current:
            lines.append(" ".join(current))
            current = []
            length = 0
        current.append(word)
        length += len(word) + 1
    if current:
        lines.append(" ".join(current))
    return lines


__all__ = [
    "ABSTENTION_NOTE",
    "ASYMMETRY_NOTE",
    "REF_COMPONENT_NOTE",
    "render_report",
]
