"""The mechanical answer verification layer (evaluation.md §4, v2).

The tests are written against the module's public behaviour: what a text is
read to state, what disagrees with a cited node, and when an answer passes
the strict gate. The instrument has no model in it, so every expectation
here is exact.
"""

from __future__ import annotations

import inspect

import pytest
from apps.api.evaluation import verification

ARTICLE_2 = verification.EvidenceNode(
    node_id="node_article_2",
    title="Article 2. Laws shall take effect after fifteen days.",
    text=(
        "Article 2. Laws shall take effect after fifteen days following the "
        "completion of their publication in the Official Gazette, unless it is "
        "otherwise provided. This Code shall take effect one year after such "
        "publication. (1a)"
    ),
)

ARTICLE_3 = verification.EvidenceNode(
    node_id="node_article_3",
    title="Article 3. Ignorance of the law excuses no one from compliance.",
    text="Article 3. Ignorance of the law excuses no one from compliance therewith. (2)",
)


# ---------------------------------------------------------------------------
# Reading quantities
# ---------------------------------------------------------------------------


def test_numeric_mentions_reads_spelled_durations() -> None:
    """The Civil Code spells its numbers, and both words are read."""
    mentions = verification.numeric_mentions("after fifteen days")
    assert [(mention.value, mention.unit) for mention in mentions] == [(15.0, "day")]
    assert verification.numeric_mentions("one year after publication")[0].value == 1.0


def test_numeric_mentions_dedupes_the_parenthetical_duplicate() -> None:
    """``thirty (30) days`` is ONE quantity, not two readings of it."""
    mentions = verification.numeric_mentions("within thirty (30) days")
    assert len(mentions) == 1
    assert mentions[0].value == 30.0
    assert mentions[0].unit == "day"


def test_numeric_mentions_ignores_article_numbers_and_footnotes() -> None:
    """A number with no unit is an identifier, not a quantity."""
    assert verification.numeric_mentions("Article 1318 provides the rule. (2)") == []
    assert verification.numeric_mentions("Sections 1, 2 and 3 apply. (1a)") == []


def test_numeric_mentions_reads_scales_percent_and_currency() -> None:
    """A scale word multiplies, and percent and currency carry their own unit."""
    spelled = verification.numeric_mentions("fifty thousand pesos")[0]
    assert (spelled.value, spelled.unit, spelled.scale) == (50000.0, "peso", 1)
    money = verification.numeric_mentions("$1,257,850")[0]
    assert (money.value, money.unit) == (1257850.0, "dollar")
    ratio = verification.numeric_mentions("twenty percent")[0]
    assert (ratio.value, ratio.unit) == (20.0, "percent")
    symbol = verification.numeric_mentions("5%")[0]
    assert (symbol.value, symbol.unit) == (5.0, "percent")


# ---------------------------------------------------------------------------
# Comparing quantities
# ---------------------------------------------------------------------------


def test_numeric_mismatch_on_a_different_value() -> None:
    """The node states the same unit with a different number."""
    node = "Laws shall take effect after thirty days following publication."
    assert verification.numeric_consistency("within 15 days", node) == (1, 1)


def test_numeric_match_on_the_same_value() -> None:
    """The spelled form and the digit form are the same quantity."""
    assert verification.numeric_consistency("within fifteen (15) days", ARTICLE_2.text) == (1, 0)


def test_numeric_mismatch_on_a_different_scale() -> None:
    """The same number under a different scale is a different amount."""
    node = "A fine of two thousand pesos is imposed."
    assert verification.numeric_consistency("a fine of 2 million pesos", node) == (1, 1)


def test_numeric_cross_unit_duration_mismatch() -> None:
    """A duration is compared within the family, so a year is not fifteen days."""
    assert verification.numeric_consistency("one year", ARTICLE_2.text) == (1, 0)
    assert verification.numeric_consistency("one year", "after fifteen days") == (1, 1)


def test_numeric_check_is_undecided_without_a_node_quantity() -> None:
    """A node that states no quantity cannot contradict one."""
    assert verification.numeric_consistency("within 15 days", ARTICLE_3.text) == (0, 0)


# ---------------------------------------------------------------------------
# Article identifiers
# ---------------------------------------------------------------------------


def test_article_mismatch_when_the_cited_node_is_another_article() -> None:
    """The sentence names 1318 and the node it cites is 1319."""
    node = verification.EvidenceNode(
        node_id="n", title="Article 1319. Contracts are perfected by consent.", text=""
    )
    assert verification.article_consistency("Article 1318 provides the rule.", node) == (1, 1)


def test_article_match_for_the_node_the_sentence_names() -> None:
    """The sentence names the node's own article."""
    assert verification.article_consistency("Article 2 takes effect.", ARTICLE_2) == (1, 0)


def test_article_check_passes_for_every_article_a_cross_reference_names() -> None:
    """A cross-reference sentence names both articles it cites."""
    node = verification.EvidenceNode(
        node_id="n", title="Article 1475. The parties may fix the price.", text=""
    )
    sentence = "Article 1624 refers to article 1475 for the perfection of the sale."
    assert verification.article_consistency(sentence, node) == (1, 0)


def test_article_check_is_undecided_without_a_name_on_either_side() -> None:
    """No article named, or no article heading on the node: nothing to decide."""
    assert verification.article_consistency("It provides the rule.", ARTICLE_2) == (0, 0)
    chapter = verification.EvidenceNode(node_id="n", title="BOOK I", text="BOOK I PERSONS")
    assert verification.article_consistency("Article 2 provides the rule.", chapter) == (0, 0)


# ---------------------------------------------------------------------------
# Polarity
# ---------------------------------------------------------------------------


def test_polarity_mismatch_on_a_near_identical_opposite_sentence() -> None:
    """The negation changes the proposition, and the sentences otherwise agree."""
    node = "The debtor shall be liable for damages."
    assert verification.polarity_consistency(
        "The debtor shall not be liable for damages.", node
    ) == (1, 1)


def test_polarity_is_undecided_when_the_sentences_are_different() -> None:
    """A negation in an unrelated sentence says nothing about the node."""
    assert verification.polarity_consistency(
        "The debtor shall not be liable for damages.", ARTICLE_2.text
    ) == (0, 0)


def test_is_negated_reads_contractions_and_plain_negations() -> None:
    """The token set covers "shall not", "no one" and the contraction stems."""
    assert verification.is_negated("The debtor shall not be liable.")
    assert verification.is_negated("Ignorance of the law excuses no one.")
    assert verification.is_negated("The rule doesn't apply.")
    assert not verification.is_negated("The debtor shall be liable.")


# ---------------------------------------------------------------------------
# The strict gate
# ---------------------------------------------------------------------------


def _cited(number: int, node: verification.EvidenceNode) -> dict[str, object]:
    return {"n": number, "node_id": node.node_id}


def test_verified_answer_with_a_supported_cited_claim() -> None:
    """A claim the node contains, with its marker, passes every check."""
    outcome = verification.verify_answer(
        answerable=True,
        insufficient=False,
        answer_markdown="Laws take effect after fifteen days [1].",
        citations=[_cited(1, ARTICLE_2)],
        evidence={ARTICLE_2.node_id: ARTICLE_2},
    )
    assert outcome.verified
    assert outcome.n_cited_claim_checks == 1
    assert outcome.n_cited_claims_supported == 1
    assert outcome.n_uncited_claims == 0


def test_uncited_claim_fails_the_gate() -> None:
    """A factual sentence with no marker is an unattributed claim."""
    outcome = verification.verify_answer(
        answerable=True,
        insufficient=False,
        answer_markdown=(
            "Laws take effect after fifteen days [1]. "
            "Ignorance of the law excuses no one from compliance."
        ),
        citations=[_cited(1, ARTICLE_2)],
        evidence={ARTICLE_2.node_id: ARTICLE_2},
    )
    assert outcome.n_uncited_claims == 1
    assert not outcome.verified


def test_wrong_quantity_fails_the_gate_even_when_the_words_match() -> None:
    """The claim's wording is in the node, and its number is not."""
    node = verification.EvidenceNode(
        node_id="n",
        title="Article 2.",
        text="Laws shall take effect after thirty days.",
    )
    outcome = verification.verify_answer(
        answerable=True,
        insufficient=False,
        answer_markdown="Laws shall take effect after fifteen days [1].",
        citations=[_cited(1, node)],
        evidence={node.node_id: node},
    )
    assert outcome.n_cited_claims_supported == 1
    assert outcome.n_numeric_mismatches == 1
    assert not outcome.verified


def test_wrong_article_fails_the_gate_even_when_the_prose_matches() -> None:
    """The prose is the node's, and the article number the answer names is not."""
    node = verification.EvidenceNode(
        node_id="n",
        title="Article 1319. Contracts are perfected by consent.",
        text="Contracts are perfected by consent.",
    )
    outcome = verification.verify_answer(
        answerable=True,
        insufficient=False,
        answer_markdown="Article 1318 provides that contracts are perfected by consent [1].",
        citations=[_cited(1, node)],
        evidence={node.node_id: node},
    )
    assert outcome.n_article_mismatches == 1
    assert not outcome.verified


def test_refusal_passes_only_without_citations() -> None:
    """An unanswerable row passes as a citation-free refusal, and only that."""
    refusal = "The documents do not contain enough information to answer this question."
    clean = verification.verify_answer(
        answerable=False,
        insufficient=True,
        answer_markdown=refusal,
        citations=[],
        evidence={},
    )
    assert clean.verified
    cited = verification.verify_answer(
        answerable=False,
        insufficient=True,
        answer_markdown=refusal,
        citations=[_cited(1, ARTICLE_2)],
        evidence={ARTICLE_2.node_id: ARTICLE_2},
    )
    assert not cited.verified


def test_answerable_answer_without_citations_fails_the_gate() -> None:
    """A non-refusal with no citation is not a verified answer."""
    outcome = verification.verify_answer(
        answerable=True,
        insufficient=False,
        answer_markdown="Laws take effect after fifteen days.",
        citations=[],
        evidence={},
    )
    assert not outcome.verified
    assert outcome.n_uncited_claims == 1


def test_gold_overlap_is_reported_beside_the_gate() -> None:
    """Gold recall and precision are reported, and they do not change the gate."""
    outcome = verification.verify_answer(
        answerable=True,
        insufficient=False,
        answer_markdown="Laws take effect after fifteen days [1].",
        citations=[_cited(1, ARTICLE_2)],
        evidence={ARTICLE_2.node_id: ARTICLE_2},
        gold_nodes=[ARTICLE_2.node_id, "node_missing"],
    )
    assert outcome.verified
    assert outcome.n_gold_nodes_cited == 1
    assert outcome.gold_citation_recall == pytest.approx(0.5)
    assert outcome.gold_citation_precision == pytest.approx(1.0)


def test_repeated_sentence_is_checked_once() -> None:
    """A sentence the model repeated is one claim stated twice."""
    outcome = verification.verify_answer(
        answerable=True,
        insufficient=False,
        answer_markdown=(
            "Laws take effect after fifteen days [1]. " "Laws take effect after fifteen days [1]."
        ),
        citations=[_cited(1, ARTICLE_2)],
        evidence={ARTICLE_2.node_id: ARTICLE_2},
    )
    assert outcome.n_cited_claim_checks == 1
    assert outcome.verified


def test_verify_claim_without_a_cited_node_is_unsupported() -> None:
    """A dangling marker names no node, so nothing can support it."""
    claim = verification.verify_claim(9, "Laws take effect after fifteen days.", None)
    assert not claim.supported
    assert not claim.consistent
    assert claim.node_id == ""


def test_to_record_carries_the_version_and_the_counts() -> None:
    """The stored record is the flat shape the run columns name."""
    outcome = verification.verify_answer(
        answerable=True,
        insufficient=False,
        answer_markdown="Article 2 provides that laws take effect after fifteen days [1].",
        citations=[_cited(1, ARTICLE_2)],
        evidence={ARTICLE_2.node_id: ARTICLE_2},
        gold_nodes=[ARTICLE_2.node_id],
    )
    record = outcome.to_record()
    assert record["verification_version"] == "verification-v2"
    assert record["answer_verified"] is True
    assert record["cited_claim_checks"] == 1
    assert record["cited_claims_supported"] == 1
    assert record["gold_nodes_total"] == 1
    assert record["gold_nodes_cited"] == 1
    assert record["numeric_checks"] == 1
    assert record["answer_claim_checks"] == 1
    assert record["answer_claims_supported"] == 1
    assert record["answer_claims_unsupported"] == 0
    assert record["answer_consistency_checks"] == 0
    assert record["answer_consistency_mismatches"] == 0
    assert record["answer_citations_unreferenced"] == 0
    assert record["answer_semantic_uncertainty"] is False
    assert set(record) == {
        "verification_version",
        "answer_verified",
        "cited_claim_checks",
        "cited_claims_supported",
        "uncited_claims",
        "numeric_checks",
        "numeric_mismatches",
        "article_checks",
        "article_mismatches",
        "polarity_checks",
        "polarity_mismatches",
        "gold_nodes_total",
        "gold_nodes_cited",
        "answer_claim_checks",
        "answer_claims_supported",
        "answer_claims_unsupported",
        "answer_consistency_checks",
        "answer_consistency_mismatches",
        "answer_citations_unreferenced",
        "answer_semantic_uncertainty",
    }


def test_verification_is_arm_blind() -> None:
    """The verification cannot see which arm produced the answer."""
    parameters = inspect.signature(verification.verify_answer).parameters
    assert "arm" not in parameters


def test_cross_node_contradiction_fails_the_gate() -> None:
    """Two claims state different values in the same unit family."""
    node_a = verification.EvidenceNode(
        node_id="node_a",
        title="Article 2.",
        text="Laws shall take effect after fifteen days following publication.",
    )
    node_b = verification.EvidenceNode(
        node_id="node_b",
        title="Article 3.",
        text="Laws shall take effect after thirty days following publication.",
    )
    outcome = verification.verify_answer(
        answerable=True,
        insufficient=False,
        answer_markdown=(
            "Laws take effect after fifteen days [1]. " "Laws take effect after thirty days [2]."
        ),
        citations=[_cited(1, node_a), _cited(2, node_b)],
        evidence={node_a.node_id: node_a, node_b.node_id: node_b},
    )
    assert outcome.consistency == (1, 1)
    assert not outcome.verified


def test_cross_node_consistency_is_undecided_with_one_claim() -> None:
    """A family with a quantity in only one claim cannot contradict itself."""
    outcome = verification.verify_answer(
        answerable=True,
        insufficient=False,
        answer_markdown="Laws take effect after fifteen days [1].",
        citations=[_cited(1, ARTICLE_2)],
        evidence={ARTICLE_2.node_id: ARTICLE_2},
    )
    assert outcome.consistency == (0, 0)
    assert outcome.verified


def test_unreferenced_citation_fails_the_gate() -> None:
    """A cited node no claim references is counted as unreferenced."""
    node_a = verification.EvidenceNode(
        node_id="node_a",
        title="Article 2.",
        text="Laws shall take effect after fifteen days following publication.",
    )
    node_b = verification.EvidenceNode(
        node_id="node_b",
        title="Article 3.",
        text="Ignorance of the law excuses no one from compliance.",
    )
    outcome = verification.verify_answer(
        answerable=True,
        insufficient=False,
        answer_markdown="Laws take effect after fifteen days [1].",
        citations=[_cited(1, node_a), _cited(2, node_b)],
        evidence={node_a.node_id: node_a, node_b.node_id: node_b},
    )
    assert outcome.n_citations == 2
    assert outcome.n_citations_unreferenced == 1
    assert not outcome.verified


def test_cited_node_referenced_by_a_claim_is_not_unreferenced() -> None:
    """A node referenced by at least one claim is not unreferenced."""
    node_a = verification.EvidenceNode(
        node_id="node_a",
        title="Article 2.",
        text="Laws shall take effect after fifteen days following publication.",
    )
    node_b = verification.EvidenceNode(
        node_id="node_b",
        title="Article 3.",
        text="Ignorance of the law excuses no one from compliance.",
    )
    outcome = verification.verify_answer(
        answerable=True,
        insufficient=False,
        answer_markdown=(
            "Laws take effect after fifteen days [1]. "
            "Ignorance of the law excuses no one from compliance [2]."
        ),
        citations=[_cited(1, node_a), _cited(2, node_b)],
        evidence={node_a.node_id: node_a, node_b.node_id: node_b},
    )
    assert outcome.n_citations_unreferenced == 0


def test_semantic_uncertainty_is_true_when_a_check_is_undecided() -> None:
    """A claim the checks cannot decide sets the flag."""
    outcome = verification.verify_answer(
        answerable=True,
        insufficient=False,
        answer_markdown="Laws take effect after fifteen days [1].",
        citations=[_cited(1, ARTICLE_3)],
        evidence={ARTICLE_3.node_id: ARTICLE_3},
    )
    assert outcome.semantic_uncertainty is True
    assert not outcome.verified


def test_semantic_uncertainty_is_false_when_every_check_is_decided() -> None:
    """Every check decided, no mismatch: the flag is false."""
    outcome = verification.verify_answer(
        answerable=True,
        insufficient=False,
        answer_markdown="Article 2 provides that laws take effect after fifteen days [1].",
        citations=[_cited(1, ARTICLE_2)],
        evidence={ARTICLE_2.node_id: ARTICLE_2},
    )
    assert outcome.semantic_uncertainty is False
    assert outcome.verified


def test_dataclasses_are_frozen() -> None:
    """The verification records are immutable."""
    import dataclasses

    assert dataclasses.is_dataclass(verification.ClaimVerification)
    assert dataclasses.is_dataclass(verification.AnswerVerification)
    with pytest.raises(dataclasses.FrozenInstanceError):
        verification.ClaimVerification(
            marker=1,
            node_id="n",
            sentence="x",
            supported=True,
            numeric_checks=0,
            numeric_mismatches=0,
            article_checks=0,
            article_mismatches=0,
            polarity_checks=0,
            polarity_mismatches=0,
            values={},
        ).supported = False
