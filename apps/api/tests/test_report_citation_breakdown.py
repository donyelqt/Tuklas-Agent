"""R9's four-bucket citation breakdown, and the note that publishes it.

R9 classifies every failing (marker, sentence) claim of a run into MARKER,
MODEL and REMAINDER, and the remainder SPLITS MECHANICALLY into
OFF-SUBJECT (the sentence shares NO content word with the cited node) and
ON-SUBJECT (overlap > 0). These tests pin the rule on SYNTHETIC inputs, so
they are model-free and corpus-free; the numbers the published note carries
are the measurement of the two stored runs, not of anything here.

What each test pins:

* the three definitions (content words, article extraction, overlap) -
  separately, because the buckets are decided by them;
* the ORDER of the buckets, which is what lets a claim be decided at all:
  MODEL is tested before the remainder split, so a zero-overlap claim whose
  cited node's heading names an article the sentence does not is MODEL and
  not OFF-SUBJECT, while a claim that NAMES the cited node's own article
  can never be MODEL;
* the breakdown's per-question record, which is what makes an off-subject
  count readable as a spread rather than a total;
* the published note, which must state BOTH arms' off-subject counts and
  must not renumber the three original buckets.
"""

from __future__ import annotations

from apps.api.evaluation.report import (
    BUCKET_MARKER,
    BUCKET_MODEL,
    BUCKET_OFF_SUBJECT,
    BUCKET_ON_SUBJECT,
    CITATION_METRIC_NOTE,
    breakdown_for_rows,
    classify_citation_claims,
    content_words,
    heading_article,
    sentence_articles,
    subject_overlap,
)

#: A node whose heading names an article, and whose text is about legal
#: separation - so a claim ABOUT separation can share content words with it.
NODE_ARTICLE = (
    "Article 100. Legal separation.",
    "Article 100. Legal separation may be claimed by the husband or the "
    "wife for any of the following causes.",
)

#: A CONTAINER node: its heading names no article at all, which is the case
#: the MODEL bucket structurally cannot reach.
NODE_SECTION = ("SECTION 4", "The provisions on pledges and mortgages.")

#: A node whose heading writes "ARTICLE" in capitals, as 12 titles in the
#: corpus do.
NODE_UPPER = (
    "ARTICLE 437. Ownership.",
    "ARTICLE 437. The owner of a parcel of land is the owner of its "
    "surface and of everything under it.",
)


def test_content_words_drops_short_tokens_and_the_stoplist() -> None:
    assert content_words("The owner of a parcel, and the wife") == {
        "owner",
        "parcel",
        "wife",
    }
    assert content_words("the of and to a") == set()


def test_heading_article_reads_the_heading_case_insensitively() -> None:
    assert heading_article(NODE_ARTICLE[0]) == 100
    assert heading_article(NODE_UPPER[0]) == 437
    assert heading_article(NODE_SECTION[0]) is None
    # The FIRST number in the heading is the node's own article, so a
    # heading that also names another article in its body is read for its
    # own.
    assert heading_article("Article 247. When a creditor whose claim is not mentioned in article 243") == 247


def test_sentence_articles_reads_a_list() -> None:
    assert sentence_articles("Articles 55, 61, 68 require this") == {55, 61, 68}
    assert sentence_articles("See Art. 1318 and Article 1324") == {1318, 1324}
    assert sentence_articles("nothing is named here") == set()


def test_subject_overlap_is_normalised_by_the_sentence() -> None:
    # Both of the sentence's content words are in the node: 2 / 2.
    assert subject_overlap("legal separation", *NODE_ARTICLE) == 1.0
    # One of the two: 1 / 2. The node's title and text are BOTH used, and
    # the denominator is the SENTENCE's word count, not the node's.
    assert subject_overlap("legal zeppelin", *NODE_ARTICLE) == 0.5
    assert subject_overlap("quantum zeppelin embargo", *NODE_ARTICLE) == 0.0


def test_a_supported_claim_is_not_classified_at_all() -> None:
    claims = classify_citation_claims(
        answer_markdown="Legal separation may be claimed by the husband [1].",
        citation_node_ids=["n1"],
        nodes={"n1": NODE_ARTICLE},
        question_id="q1",
        threshold=85,
    )
    assert claims == []


def test_a_marker_without_a_citation_is_marker() -> None:
    claims = classify_citation_claims(
        answer_markdown="Legal separation may be claimed by the husband [1] [9].",
        citation_node_ids=["n1"],
        nodes={"n1": NODE_ARTICLE},
        question_id="q1",
        threshold=85,
    )
    assert [(claim.bucket, claim.marker) for claim in claims] == [(BUCKET_MARKER, 9)]


def test_zero_overlap_on_an_article_node_is_model() -> None:
    claims = classify_citation_claims(
        answer_markdown="The quantum zeppelin embargo [1].",
        citation_node_ids=["n1"],
        nodes={"n1": NODE_ARTICLE},
        question_id="q1",
        threshold=85,
    )
    assert len(claims) == 1
    assert claims[0].bucket == BUCKET_MODEL
    assert claims[0].overlap == 0.0


def test_zero_overlap_on_a_container_node_is_off_subject() -> None:
    claims = classify_citation_claims(
        answer_markdown="The quantum zeppelin embargo [1].",
        citation_node_ids=["n2"],
        nodes={"n2": NODE_SECTION},
        question_id="q1",
        threshold=85,
    )
    assert len(claims) == 1
    # The bucket is decided by the heading: a container node names no
    # article, so the MODEL clause cannot fire and the claim is
    # OFF-SUBJECT.
    assert claims[0].bucket == BUCKET_OFF_SUBJECT
    assert claims[0].node_id == "n2"


def test_a_claim_naming_the_cited_nodes_own_article_is_never_model() -> None:
    # A single-digit article, so that the number itself is not a content
    # word (tokens of length 1 are dropped) and the overlap can be 0 while
    # the sentence names the node's own article.
    node = ("Article 5. Pledge.", "Article 5. A pledge is a contract by which a debtor delivers.")
    claims = classify_citation_claims(
        answer_markdown="Article 5 has nothing to do with zeppelins [1].",
        citation_node_ids=["n1"],
        nodes={"n1": node},
        question_id="q1",
        threshold=85,
    )
    assert len(claims) == 1
    # Zero overlap, article heading - and still OFF-SUBJECT, because the
    # sentence names the cited node's own article. That is the case the
    # MODEL clause is defined to exclude, and it is where the zero-overlap
    # rule takes over.
    assert claims[0].overlap == 0.0
    assert claims[0].bucket == BUCKET_OFF_SUBJECT


def test_a_paraphrase_that_fails_the_string_rule_is_on_subject() -> None:
    claims = classify_citation_claims(
        answer_markdown="Legal separation requires the wife's consent always [1].",
        citation_node_ids=["n1"],
        nodes={"n1": NODE_ARTICLE},
        question_id="q1",
        threshold=85,
    )
    assert len(claims) == 1
    assert claims[0].overlap > 0.0
    assert claims[0].bucket == BUCKET_ON_SUBJECT


def test_breakdown_counts_the_four_buckets_and_the_off_subject_spread() -> None:
    rows = [
        {
            "question_id": "q1",
            "answer_markdown": "The quantum zeppelin embargo [1]. [9]",
            "citation_node_ids": ["n2"],
            "citations_valid": False,
        },
        {
            "question_id": "q2",
            "answer_markdown": "The quantum zeppelin embargo [1].",
            "citation_node_ids": ["n2"],
            "citations_valid": False,
        },
        {
            "question_id": "q3",
            "answer_markdown": "The quantum zeppelin embargo [1].",
            "citation_node_ids": ["n2"],
            "citations_valid": True,
        },
    ]
    breakdown = breakdown_for_rows(
        rows=rows, nodes={"n2": NODE_SECTION}, threshold=85
    )
    assert breakdown.buckets == {
        BUCKET_MARKER: 1,
        BUCKET_MODEL: 0,
        BUCKET_OFF_SUBJECT: 2,
        BUCKET_ON_SUBJECT: 0,
    }
    assert breakdown.failing == 3
    assert breakdown.remainder == 2
    # The valid row contributes nothing, and the two off-subject claims are
    # one each in two questions - the SPREAD, not only the total.
    assert breakdown.off_subject_questions == {"q1": 1, "q2": 1}
    assert breakdown.n_off_subject_questions == 2
    assert breakdown.max_off_subject_in_one_question == 1


def test_a_breakdown_with_no_off_subject_claims_reports_no_spread() -> None:
    breakdown = breakdown_for_rows(
        rows=[
            {
                "question_id": "q1",
                "answer_markdown": "The quantum zeppelin embargo [1].",
                "citation_node_ids": ["n1"],
                "citations_valid": False,
            }
        ],
        nodes={"n1": NODE_ARTICLE},
        threshold=85,
    )
    assert breakdown.buckets[BUCKET_OFF_SUBJECT] == 0
    assert breakdown.n_off_subject_questions == 0
    assert breakdown.max_off_subject_in_one_question == 0


def test_the_published_note_states_both_arms_off_subject_counts() -> None:
    # A published count for ONE arm reads as a comparison: the reader has
    # the dense arm's off-subject block and no way to see whether the
    # vectorless arm has one. So the note carries both, and says so.
    assert "OFF-SUBJECT" in CITATION_METRIC_NOTE
    assert "ON-SUBJECT" in CITATION_METRIC_NOTE
    assert "BOTH ARMS' OFF-SUBJECT COUNTS ARE PUBLISHED" in CITATION_METRIC_NOTE
    assert "the test-48 VECTORLESS arm's off-subject count is 0" in CITATION_METRIC_NOTE
    assert "span 5 QUESTIONS with 5 in the largest single one" in CITATION_METRIC_NOTE
    assert "on dev-24 both arms' off-subject counts are 0" in CITATION_METRIC_NOTE
    # The three original buckets are not renumbered or dropped.
    assert "the remainder are all still published" in CITATION_METRIC_NOTE
    assert "remainder 47" in CITATION_METRIC_NOTE
