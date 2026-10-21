"""The run report (evaluation.md §7: "Reading the results honestly").

The report is the deliverable, and it has one job: to let a reader who did
not run it judge whether the numbers mean what they appear to mean. So it
states, in this order:

1. **The run's identity** — n, the split seed, the model and backend, the
   judge prompt version, the arms, the dataset, the config and its id, and
   the SCOPE of each arm: how many nodes it ranks, over what surface.
2. **The arms' asymmetry** — the vectorless arm applies identifier
   precedence (ADR-005) and the dense arm has no ident component at all;
   the ``title`` and ``hier`` components are vectorless-only for the same
   structural reason. So the arms differ in more than the retrieval
   mechanism, and the ``direct-lookup`` category is not a like-for-like
   result.
3. **What the latency numbers measure** — the two arms do the same KIND of
   question with different UNITS of work, so the two latencies are not a
   like-for-like cost comparison.
4. **The numbers** — per arm, per category and overall, with no comparison
   verdict. The comparison's OUTCOME is unknown until it is measured, and
   a report that names a winner is making a claim the spec forbids.
5. **The spot check's METHOD** — the number is the judge's self-consistency
   unless a builder re-scored the sample, and the report says which.
6. **The limitations that the numbers carry** — the measured ones, stated
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
    "The two arms differ in more than the retrieval MECHANISM. They differ "
    "in the CHANNELS they can use, and three of the vectorless arm's five "
    "scoring components are channels the dense arm structurally CANNOT "
    "have. (1) 'ident': the vectorless arm applies identifier precedence "
    "(ADR-005) - a query that names a provision puts that provision first, "
    "and the arm has an 'ident' scoring component. The dense arm has NO "
    "ident component at all - it embeds the query, so it can match a "
    "provision semantically, but it has no symbolic label channel. "
    "(2) 'title': the vectorless arm scores Jaccard overlap between the "
    "query's tokens and the node's HEADING; the dense arm has no heading "
    "column and scores one embedding per node, so it has no title channel "
    "either. (3) 'hier': the vectorless arm gives 1.0 to a node that "
    "shares a parent with a lex-top-3 node, so a provision's SIBLINGS rise "
    "with it; the dense arm's 'hier' component is 0.0 for every node, always. "
    "These are not the same three numbers the two arms would have produced "
    "with the same mechanism - they are channels one arm has and the other "
    "does not. The 'direct-lookup' category is therefore not a like-for-like "
    "result, and the arms' per-category numbers must be read with that in "
    "mind. The outcome of the comparison is UNKNOWN; this report publishes "
    "both arms' numbers and no verdict."
)

#: The note about the 'title' and 'hier' channels, which is a SEPARATE
#: disclosure from the ident one because it is about a different thing.
#:
#: The ident asymmetry is about a QUERY that names a provision. The title
#: and hier asymmetries are about EVERY query: the title channel fires on
#: almost every hit, and the hier channel fires on almost every hit, so
#: they move the ranking everywhere, not only on the direct-lookup
#: category.
#:
#: Measured on the Civil Code over the dev split's answerable questions
#: (the numbers below are what the report states, so a reader can hold
#: the disclosure to a measurement):
#:
#: * 'title' > 0 on 96.5% of the vectorless arm's hits;
#: * 'hier' > 0 on 97.8% of them;
#: * zeroing both changes the top-10 ORDER on 21 of 23 dev questions
#:   (22 of 23 when 'ident' is zeroed too), and on 40 of 41 test
#:   questions.
#:
#: The dense arm's 'title' and 'hier' components are 0.0 for every node by
#: construction (experiments/vector/index.py), so these two channels are
#: vectorless-only, exactly as 'ident' is.
TITLE_HIER_ASYMMETRY_NOTE = (
    "The 'title' and 'hier' components are VECTORLESS-ONLY channels, and "
    "they are not a corner case the way 'ident' is. The dense arm's 'title' "
    "and 'hier' components are 0.0 for every node by construction, while "
    "the vectorless arm's 'title' fires on 96.5% of its hits (Jaccard "
    "overlap between the query's tokens and the node's heading) and its "
    "'hier' fires on 97.8% of them (the node shares a parent with a "
    "lex-top-3 node, so a provision's siblings rise with it). Measured on "
    "the Civil Code, zeroing 'title' and 'hier' changes the top-10 ORDER "
    "on 21 of 23 dev questions and on 40 of 41 test questions. So the "
    "per-category numbers are not two mechanisms ranking the same nodes: "
    "they are two mechanisms with different CHANNELS, and the categories "
    "where the channels bite hardest are the ones a reader must treat as "
    "NOT like-for-like."
)

#: The note about what the two arms' latency numbers MEASURE.
#:
#: Both arms' retrieval latency is the SUM of the agent's own
#: ``search_nodes`` calls - the calls that produced the ranking the
#: metric is measured over. That is the same work on both sides, so the
#: two numbers ARE comparable as "the cost of the agent's retrieval", and
#: the difference between them is the difference between the two arms'
#: per-query work.
#:
#: That per-query work is NOT the same unit. The dense arm's is a matrix
#: product over a PRE-BUILT index: the corpus is embedded once, and a
#: query is one query-vector against a 2,579-row matrix (measured: 0.36 ms
#: for the 2,579 x 384 product). The vectorless arm's is a full-document
#: BM25 RE-SCORE per query (measured on the Civil Code: 141 ms of the
#: 153 ms is the lex layer alone, plus 72 ms for the title channel and
#: 17 ms to load the candidates).
#:
#: So the two numbers are not a like-for-like cost comparison, and
#: NEITHER includes the build of its own index: the dense arm's ~4.5-minute
#: corpus embed is outside the measured retrieval latency, and the
#: vectorless arm's BM25 corpus build is outside it too. A reader who
#: compares the two latencies as "the cost of the mechanism" is comparing
#: a query against a pre-built artifact with a query against a re-score.
LATENCY_UNITS_NOTE = (
    "The two arms' retrieval latencies are NOT the same unit of work, so "
    "they are not a like-for-like cost comparison. Both numbers are the "
    "sum of the agent's own search_nodes calls - the calls that produced "
    "the ranking the retrieval metric is measured over - so the two are "
    "comparable as the cost of the agent's retrieval, and the difference "
    "between them is the difference between the arms' per-query work. "
    "That work is not the same: the dense arm's is a matrix product over "
    "a PRE-BUILT index (the corpus is embedded once, about 4.5 minutes for "
    "the Civil Code's 2,579 nodes, and each query is one query vector "
    "against that matrix - 0.36 ms for the 2,579 x 384 product, measured), "
    "while the vectorless arm's is a full-document BM25 RE-SCORE per query "
    "(141 ms of its 153 ms is the lex layer alone, plus 72 ms for the "
    "title channel and 17 ms to load the candidates, measured on the Civil "
    "Code). Neither number includes the build of its own index, and the "
    "dense arm's build is the more expensive of the two by three orders "
    "of magnitude. Read the two latencies as 'a query against a "
    "pre-built artifact' and 'a query against a re-score', not as 'the "
    "cost of the mechanism'."
)

#: The note about the spot check's METHOD.
#:
#: evaluation.md §4 asks for a stratified 20% sample of the test answers
#: RE-SCORED BY THE BUILDER, and a disagreement above 15% invalidates the
#: judge run. What the harness actually performs, when no builder
#: re-scores are supplied, is a MECHANICAL re-judge: the same versioned
#: prompt, the same backend, the same temperature and seed, the same
#: evidence. That measures the judge's SELF-CONSISTENCY - whether it
#: gives the same verdict twice - and NOT the agreement between a careful
#: human and the judge.
#:
#: The two answer different questions, so the number must be labelled:
#:
#: * the self-consistency number answers "is the judge stable?";
#: * the builder's number answers "does the judge agree with a careful
#:   human?".
#:
#: The second is the one evaluation.md §4 asks for. A report that printed
#: "disagreement: 0.017" without the method would let a reader assume the
#: second, so the method is printed BY NAME, next to the number, and the
#: report says in plain words which measurement it is.
SPOT_CHECK_METHOD_NOTE = (
    "The spot check's METHOD matters as much as its number, and the method "
    "is printed beside the disagreement above. 'mechanical-rejudge (judge "
    "self-consistency)' means the SAME versioned prompt was re-sent to the "
    "SAME backend at the same temperature and seed over the SAME evidence, "
    "and the number is the fraction of claims on which the judge disagreed "
    "with ITSELF. It is NOT the builder's human re-score that evaluation.md "
    "section 4 asks for: a human re-score is a DIFFERENT measurement, and it "
    "answers a different question ('does the judge agree with a careful "
    "human?' rather than 'is the judge stable?'). A reader who takes the "
    "number above as a builder's validation of the judge is reading a "
    "measurement that was not taken."
)

#: The note about the reproducibility acceptance and what is actually
#: checked.
#:
#: evaluation.md §8 asks for "Re-run with same config id reproduces
#: retrieval exactly, generation within +/-5pp". The retrieval half is
#: checked by ``scripts/check_reproducibility.py``: it runs the retrieval
#: pass TWICE over two fresh ingests and two fresh index builds, and
#: compares the per-question node id lists and the macro-averaged metrics
#: element for element.
#:
#: The GENERATION half is NOT checked by anything in this repository, and
#: the report says so rather than letting the reader assume the whole
#: acceptance was met. The reason is structural, not an oversight: the
#: generation half is a property of a local model call, so checking it
#: needs a live model backend and a run that costs minutes per question.
#: A script that cannot run without a model must not be the only witness
#: for a claim that is published next to a number, so the claim is
#: published as UNCHECKED.
GENERATION_REPRODUCIBILITY_NOTE = (
    "The reproducibility acceptance has TWO halves, and only one of them "
    "is checked here. evaluation.md section 8 asks for 'Re-run with same "
    "config id reproduces retrieval exactly, generation within +/-5pp'. "
    "The RETRIEVAL half is checked by scripts/check_reproducibility.py: it "
    "runs the retrieval pass twice over two fresh ingests and two fresh "
    "index builds, through BOTH arms, and compares the per-question node "
    "id lists and the macro-averaged metrics element for element. The "
    "GENERATION half is NOT checked by anything in this repository: it is "
    "a property of a local model call, so measuring it needs a live model "
    "backend and a run that costs minutes per question, and no script "
    "here can carry that. The +/-5pp generation tolerance is therefore an "
    "UNCHECKED acceptance, and a reader must not treat this run's "
    "generation numbers as a reproduced measurement."
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
#: the nearest neighbour a lex of 1.0, so the dense arm's gate never
#: fails on LEXICAL grounds.
#:
#: It is not unconditional, though, and an earlier version of this note
#: said it was: it claimed the dense arm's abstention score was
#: STRUCTURALLY 0, and the measured run contradicts that. The evidence
#: gate keeps a node only when its text is at least 50 characters, and
#: the dense arm's cosine ranks a cluster of short SECTION nodes (11 to
#: 40 characters of text) at the very top for an out-of-vocabulary
#: question. Every one of them is dropped by the length floor, the kept
#: set is empty, and the gate fails on that non-lexical ground.
#:
#: The note therefore states what is true and does not carry a
#: split-specific number: the two arms' abstention scores are not
#: comparable, because the dense arm's number is a property of its
#: ranking and of the length floor rather than a measurement of its
#: refusal quality. A reader who compares the two arms' abstention
#: scores compares a refusal measurement with a property of the ranking.
DENSE_GATE_NOTE = (
    "The two arms' abstention scores are NOT comparable, and the reason is "
    "structural rather than a defect in the run. The support rule is 'the "
    "node's lex component is above 0'. The vectorless arm's lex layer is "
    "ALL-ZERO when no query term matches any node, so its gate fails "
    "CLOSED on lexical grounds and the run abstains. The dense arm's score "
    "is a cosine similarity, which is non-zero for every node against every "
    "query; the min-max normalization then gives the nearest neighbour a "
    "lex of 1.0, so the dense arm's gate CANNOT FAIL ON LEXICAL GROUNDS. "
    "A refusal by the dense arm is therefore not a refusal to answer, and "
    "its abstention score is far below the vectorless arm's for that reason "
    "alone. It is not UNCONDITIONAL, though, and an earlier version of "
    "this note said it was: the dense arm's gate can still fail on a "
    "NON-LEXICAL ground. The evidence gate keeps a node only when its text "
    "is at least 50 characters, and the dense arm's cosine ranks a cluster "
    "of short SECTION nodes (11 to 40 characters of text) at the very top "
    "for an out-of-vocabulary question. Every one of them is dropped by the "
    "length floor, the kept set is empty, and the gate fails. Whether that "
    "happens on a given question is a property of the arm's RANKING and of "
    "the length floor, not of the system's refusal quality, so the dense "
    "arm's abstention score is a number about the ranking and the two "
    "arms' abstention scores are not comparable. The number above is the "
    "measurement; this note says what it is a measurement OF."
)


#: The note about the INSTRUMENT that produced the numbers, for a reader
#: who holds an earlier report of the same split.
#:
#: Two of the quantities in this report were REDEFINED, not merely
#: re-measured, and the earlier report of this split measured the old
#: definitions. A number whose instrument changed is not comparable with
#: the number it replaced, so the report says so where a reader will see
#: it, rather than letting the two reports be compared as if the
#: instrument had been constant.
#:
#: 1. **The retrieval metric.** It is now computed over the node ids the
#:    ANSWER was produced from - the union of the agent's per-sub-question
#:    ``search_nodes`` results. The earlier report computed it over a
#:    separate, one-off search over the whole question, a ranking the model
#:    never saw. The two measure different retrievals, so the earlier
#:    report's Recall@K and MRR@10 are NOT comparable with this report's.
#: 2. **``latency_ms``.** It is now the sum of the agent's own
#:    ``search_nodes`` calls for the question. The earlier report's figure
#:    was a one-off whole-question probe. Same for both arms, so the two
#:    arms are comparable with each other here, and neither is comparable
#:    with the earlier report's figure.
#:
#: The other quantities were not redefined: the judge's scores, the
#: abstention score and the token counts are measured the same way as
#: before.
INSTRUMENT_CHANGE_NOTE = (
    "The INSTRUMENT changed for two of the numbers in this report, so they "
    "are NOT comparable with an earlier report of the same split. (1) The "
    "RETRIEVAL METRIC is now computed over the node ids the ANSWER was "
    "produced from - the union of the agent's per-sub-question "
    "search_nodes results - because the earlier report computed it over a "
    "separate one-off search over the whole question, a ranking the model "
    "never saw. The earlier report's Recall@K and MRR@10 therefore "
    "measure a different retrieval and are not comparable with this "
    "report's. (2) latency_ms is now the SUM of the agent's own "
    "search_nodes calls for the question, where the earlier report's "
    "figure was a one-off whole-question probe; the two arms are "
    "comparable with each other on this measure, and neither is "
    "comparable with the earlier report's figure. The judge's scores, "
    "the abstention score and the token counts were not redefined."
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
    # WHAT the retrieval metric is measured over. The agent splits a
    # question into sub-questions and calls search_nodes once per
    # sub-question, so the retrieval an answer was produced from is the
    # union of those calls' result ids. A metric computed over a separate
    # whole-question search would describe a retrieval that never
    # happened, so the report says which one it is.
    lines.append(
        "    retrieval measured over: the node ids the ANSWER was produced "
        "from (the union of the agent's per-sub-question search_nodes "
        "results), not a separate whole-question search"
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
    # The number of claims the judge actually scored, next to the
    # faithfulness it is a mean over. Faithfulness without n_judged is a
    # mean over an unstated number of observations, and the two differ
    # here: the judge does not score a refusal (it has no factual claim to
    # check), so n_judged is smaller than the row count and a reader
    # cannot recover it from the numbers above.
    lines.append(
        f"    judge: n_judged={generation.get('n_judged', 0)} claims "
        f"(the faithfulness above is the mean over these; a refusal carries "
        f"no claim and is not judged)"
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


#: The surface each arm scores, used when the arm's own description does
#: not name one. The arm's ``describe()`` payload is the FIRST source,
#: because a fact about an arm belongs to the arm; this is the fallback
#: so the report is never silent about the surface.
#:
#: The strings are deliberately about WHAT is scored, not about how well:
#: "per-document BM25 over the leaf bodies plus the section intros, with
#: the query's identifier tokens stripped" and "one dense embedding per
#: node over title plus body, cosine similarity over the whole corpus" are
#: descriptions of the two surfaces, and the reader can see from them that
#: the surfaces are not the same.
DEFAULT_SURFACE: dict[str, str] = {
    "vectorless": (
        "per-document BM25 over the leaf bodies plus the section intros, with "
        "the query's identifier tokens stripped"
    ),
    "dense": (
        "one dense embedding per node over title plus body text, cosine "
        "similarity over the whole corpus"
    ),
}


def _surface_for(description: dict[str, Any]) -> str:
    """The surface an arm scores, from its own description or the fallback.

    The arm's own ``surface`` key wins. Without it, the arm is identified
    by its own ``dense`` flag, which is the one field the dense arm's
    description cannot get wrong: it is the field that says a baseline
    stopped being dense.
    """
    surface = description.get("surface")
    if isinstance(surface, str) and surface:
        return surface
    if description.get("dense"):
        return DEFAULT_SURFACE["dense"]
    return DEFAULT_SURFACE["vectorless"]


def _arm_scope_lines(
    vectorless_arm_description: dict[str, Any] | None,
    dense_arm_description: dict[str, Any] | None,
) -> list[str]:
    """The per-arm SCOPE: the corpus size and the surface each arm scores.

    Three facts a reader needs before the numbers, and none of them is
    visible from the numbers:

    * **How many nodes the arm ranks.** The VECTORLESS arm ranks the
      document's whole node set, because its ``ident``, ``title`` and
      ``hier`` channels reach the containers the lexical surface does not.
      The DENSE arm does not: it embeds the lexical node set, so its
      ``n_nodes`` equals the vectorless arm's ``n_lex_nodes``.
    * **How large the vectorless arm's LEXICAL corpus is.** Its BM25
      corpus is the leaf bodies plus the section intros, so it is smaller
      than the node count, and a node outside that corpus carries lex = 0
      by construction rather than by scoring low. The dense arm embeds
      exactly that same lexical surface, so the two arms' lexical
      material is the same set.
    * **What the arm scores.** The vectorless arm scores a per-document
      BM25 over that corpus; the dense arm scores a cosine similarity over
      one embedding per node. These are different surfaces, and a
      difference between the arms' numbers is a difference between
      surfaces and channels as well as between mechanisms.

    The keys come from the arms' own ``describe()`` payload, so the report
    prints what the arm said about itself and not a second, separate
    description that could drift from it. An arm that does not describe
    itself still gets a surface (see :func:`_surface_for`), because the
    report is never silent about what an arm scores.
    """
    lines: list[str] = []
    lex_corpus: int | None = None
    vectorless_ranks: int | None = None
    for label, description in (
        ("vectorless arm", vectorless_arm_description),
        ("dense arm", dense_arm_description),
    ):
        if not description:
            continue
        n_nodes = description.get("n_nodes")
        n_lex = description.get("n_lex_nodes")
        if isinstance(n_lex, int):
            lex_corpus = n_lex
        if label == "vectorless arm" and isinstance(n_nodes, int):
            vectorless_ranks = n_nodes
        parts: list[str] = []
        if n_nodes is not None:
            parts.append(f"ranks {n_nodes} nodes")
        if n_lex is not None:
            parts.append(f"lex corpus {n_lex} nodes")
        parts.append(f"surface: {_surface_for(description)}")
        lines.append(f"  {label} scope         : " + ", ".join(parts))
    if len(lines) == 2:
        if lex_corpus is not None and vectorless_ranks is not None:
            lines.append(
                "  the two arms score DIFFERENT surfaces over the corpus, and "
                "the vectorless arm's lexical corpus is a strict subset of "
                f"the {vectorless_ranks} nodes it ranks "
                f"({lex_corpus} of them), so a per-category difference "
                "between them is a difference of surface and channel as well "
                "as of mechanism."
            )
        else:
            lines.append(
                "  the two arms score DIFFERENT surfaces over the corpus, so a "
                "per-category difference between them is a difference of "
                "surface and channel as well as of mechanism."
            )
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
    dataset_overlap: dict[str, Any] | None = None,
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
    if dataset_overlap is not None:
        distinct = dataset_overlap.get("n_distinct_test_gold_provisions")
        if distinct:
            lines.append(
                f"  n distinct golds     : {distinct} (the effective sample "
                f"size; {n_questions} questions share some provisions)"
            )
    lines.append(f"  split                : {dataset_name} (snapshot {dataset_snapshot})")
    lines.append(f"  split seed           : {split_seed}")
    lines.append(f"  model / backend      : {config_body.get('model')} / {config_body.get('backend')}")
    lines.append(
        f"  temperature / seed   : {config_body.get('temperature')} / {config_body.get('seed')}"
    )
    lines.append(f"  judge prompt version : {config_body.get('judge_prompt_version')}")
    lines.append(f"  code version         : {config_body.get('code_version')}")
    # The numbers below describe the harness AS IT STOOD at that code
    # version, and a report that does not say so is a report a reader
    # could mistake for a measurement of the current tree. The run's
    # metrics were computed by the code the run names, and a later change
    # to the harness does not retroactively change them.
    lines.append(
        f"  harness as of        : the code version above ({config_body.get('code_version')}); "
        f"the numbers below are what THAT harness measured, and a later "
        f"change to the harness does not change them"
    )
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
    # The SCOPE of each arm: how many nodes it ranks, and over what
    # surface. Without these two facts a reader cannot tell whether the
    # two arms ranked the same candidate set, and on this corpus they do
    # NOT: the vectorless arm's BM25 corpus is the leaf bodies plus the
    # section intros (2,392 of the Civil Code's 2,579 nodes), while the
    # dense arm embeds every node (2,579). The two arms also score
    # DIFFERENT surfaces over those nodes, so a per-category difference
    # is not a per-category difference in ranking method alone.
    lines.extend(_arm_scope_lines(vectorless_arm_description, dense_arm_description))
    lines.append("")
    lines.append("THE ARMS ARE NOT LIKE-FOR-LIKE (read before the numbers)")
    for line in _wrap(ASYMMETRY_NOTE, 74):
        lines.append(f"  {line}")
    lines.append("")
    lines.append("AND THE 'title' AND 'hier' CHANNELS ARE VECTORLESS-ONLY")
    for line in _wrap(TITLE_HIER_ASYMMETRY_NOTE, 74):
        lines.append(f"  {line}")
    lines.append("")
    lines.append("THE TWO LATENCIES ARE NOT THE SAME UNIT OF WORK")
    for line in _wrap(LATENCY_UNITS_NOTE, 74):
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
            # The METHOD is printed BY NAME, on the line above the number
            # it qualifies. A reader who sees only "disagreement: 0.017"
            # would reasonably assume a human re-scored the sample and
            # validated the judge; the harness's default is a mechanical
            # re-judge, and the report must not let the reader assume
            # otherwise.
            method = str(spot_check.get("method") or "unnamed")
            lines.append(
                f"  method: {method}"
            )
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
        for line in _wrap(SPOT_CHECK_METHOD_NOTE, 74):
            lines.append(f"  {line}")
        lines.append("")
    lines.append("MEASURED LIMITATIONS")
    notes = [
        INSTRUMENT_CHANGE_NOTE,
        REF_COMPONENT_NOTE,
        ABSTENTION_NOTE,
        DENSE_GATE_NOTE,
        GENERATION_REPRODUCIBILITY_NOTE,
    ]
    if dataset_overlap is not None:
        shared = dataset_overlap.get("n_shared_gold_nodes", 0)
        if shared:
            notes.append(
                "The dev and test splits SHARE "
                f"{shared} gold provision(s) "
                f"({dataset_overlap.get('shared_fraction_of_test_golds', 0.0):.1%} "
                "of the test split's distinct gold provisions), and "
                f"{len(dataset_overlap.get('test_questions_fully_contained_in_dev', []))} "
                "test question(s) have their ENTIRE gold set inside the dev "
                "split's. evaluation.md sec 1 makes the dev split the tuning "
                "split and the test split the one touched once per frozen "
                "config, so for those provisions the tuning has already seen "
                "the test split's answer key. The per-category numbers below "
                "move in steps of 1/n, and one question can move several rows "
                "at once where a provision is reused."
            )
    for note in notes:
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
    "DENSE_GATE_NOTE",
    "GENERATION_REPRODUCIBILITY_NOTE",
    "INSTRUMENT_CHANGE_NOTE",
    "LATENCY_UNITS_NOTE",
    "REF_COMPONENT_NOTE",
    "SPOT_CHECK_METHOD_NOTE",
    "TITLE_HIER_ASYMMETRY_NOTE",
    "render_report",
]
