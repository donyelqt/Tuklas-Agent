"""The split-overlap the report publishes, at the CALL SITE that computes it.

``render_report`` is not where the defect lived. The dev report's overlap
note was computed by the caller, which passed the split being RENDERED as
both sides of the comparison: rendering the dev report ran
``gold_overlap(dev, dev)``, and the note then printed that self-comparison's
numbers under the TEST split's name - 30 shared golds, 100.0% of the "test"
split's golds, 23 "test" questions fully contained. Every one of those three
numbers is false, and the true ones are 9, 17.0% and 5.

So these tests exercise ``run_eval.split_overlap`` - the one call site that
decides which pair is measured - and assert the NUMBERS it returns and the
numbers a rendered report prints, because a test that only asserts the note's
wording is present passes against the defective code as well.

A second fact is pinned here because the obvious fix breaks it: the header
line ``n distinct golds`` describes the split being RENDERED, so it must be
30 on the dev report and 53 on the test report, while the SHARE count is the
PAIR's and must be 9 in both. Reading the header from the overlap dict makes
the dev report claim the test split's 53.
"""

from __future__ import annotations

from apps.api.evaluation import dataset as dataset_module
from apps.api.evaluation import report as eval_report
from apps.api.evaluation import run_eval

#: The frozen splits' true overlap: the properties of the committed
#: ``data/datasets/v1/{dev,test}.json``, measured, not chosen.
SHARED = 9
TEST_DISTINCT = 53
DEV_DISTINCT = 30
TEST_QUESTIONS_CONTAINED = 5

#: What the dev report printed before the fix: ``gold_overlap(dev, dev)``
#: says the "shared" golds are the dev split's own 30, that they are 100% of
#: the "test" split's golds, and that 23 "test" questions are contained in
#: the dev split. None of these is a fact about the pair.
SELF_COMPARISON_SHARED = 30
SELF_COMPARISON_CONTAINED = 23


def _flat(text: str) -> str:
    """The report text with runs of whitespace collapsed.

    The report wraps its notes on word boundaries, so a phrase in a note can
    carry a newline in the middle of it. Assertions about a note's numbers
    are about the words, not about where the wrap fell.
    """
    return " ".join(text.split())


def test_the_published_overlap_is_the_dev_test_pair() -> None:
    overlap = run_eval.split_overlap()
    assert overlap is not None
    assert overlap["n_shared_gold_nodes"] == SHARED
    assert overlap["n_distinct_test_gold_provisions"] == TEST_DISTINCT
    assert overlap["n_distinct_dev_gold_provisions"] == DEV_DISTINCT
    assert len(overlap["test_questions_fully_contained_in_dev"]) == TEST_QUESTIONS_CONTAINED
    assert f"{overlap['shared_fraction_of_test_golds']:.1%}" == "17.0%"
    assert f"{overlap['shared_fraction_of_dev_golds']:.1%}" == "30.0%"
    # And it is NOT the comparison of the split against itself, which is
    # what the dev report used to publish.
    assert overlap["n_shared_gold_nodes"] != SELF_COMPARISON_SHARED
    assert len(overlap["test_questions_fully_contained_in_dev"]) != SELF_COMPARISON_CONTAINED
    assert overlap["shared_fraction_of_test_golds"] != 1.0


def test_the_same_pair_is_measured_whichever_split_is_rendered() -> None:
    dev_text = _flat(_render(dev=True))
    test_text = _flat(_render(dev=False))
    shared_note = (
        f"SHARE {SHARED} gold provision(s): 17.0% of the test split's "
        f"{TEST_DISTINCT} distinct gold provisions, and 30.0% of the dev "
        f"split's {DEV_DISTINCT}."
    )
    assert shared_note in dev_text
    assert shared_note in test_text
    assert f"{TEST_QUESTIONS_CONTAINED} test question(s) have their ENTIRE gold set" in dev_text
    assert f"{TEST_QUESTIONS_CONTAINED} test question(s) have their ENTIRE gold set" in test_text
    # The self-comparison's numbers are gone from the dev report.
    assert "100.0%" not in dev_text
    assert f"SHARE {SELF_COMPARISON_SHARED} " not in dev_text
    assert f"{SELF_COMPARISON_CONTAINED} test question(s)" not in dev_text


def test_the_header_is_the_rendered_splits_own_effective_n() -> None:
    dev_text = _flat(_render(dev=True))
    test_text = _flat(_render(dev=False))
    assert f"n distinct golds : {DEV_DISTINCT} (" in dev_text
    assert f"n distinct golds : {TEST_DISTINCT} (" in test_text
    # The trap: the dev report must NOT claim the test split's 53 in its
    # own header, which is what reading the count out of the overlap dict
    # (whose per-split fields are the PAIR's) would make it do.
    assert f"n distinct golds : {TEST_DISTINCT} (" not in dev_text


def _render(*, dev: bool) -> str:
    """The report for one split, using the call site's own overlap and n."""
    split_name = "dev" if dev else "test"
    dataset = dataset_module.load_split(run_eval.DATASET_DIR, split_name)
    return eval_report.render_report(
        evaluation_id="eval_test",
        config={"config": {}, "config_id": "cfg_x", "k": [1, 3, 5, 10]},
        dataset_name=dataset.name,
        dataset_snapshot=dataset.snapshot_id,
        split_seed=dataset.seed,
        n_questions=len(dataset.questions),
        aggregates={"arms": {}},
        dataset_overlap=run_eval.split_overlap(),
        n_distinct_golds=dataset_module.effective_n(dataset),
    )
