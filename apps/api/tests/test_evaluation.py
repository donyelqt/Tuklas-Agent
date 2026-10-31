"""The evaluation framework's tests (evaluation.md §8's checklist).

The suite is MODEL-FREE and CORPUS-FREE where it can be. The generation
is driven through a deterministic stub, the judge through the canned
judge, and the corpus is a SYNTHETIC PDF built with PyMuPDF — the same
convention ``apps/api/tests/test_api_routes.py`` uses, so the suite runs
on a fresh clone with neither the corpus files nor a live model.

The ONE test that needs the real corpus is the dataset validation test,
and it SKIPS when the corpus PDF is absent rather than failing: the
corpus is not committed (ADR-002: it never leaves the machine), and a
suite that depended on it would skip itself on a fresh clone.

What the suite pins:

* the dataset schema, the stratification, the unanswerable count, the
  no-overlap rule, and the LOUD rejection of a bad gold id (D1
  invariant 2);
* the shared scorer's two rules and the 85 threshold;
* the retrieval metrics: recall, precision, MRR, per category and
  overall;
* the judge's BLINDING (the payload has no arm) and its versioning;
* the judge's spot-check sample and disagreement;
* the run's persistence: the two tables, joinable per question per arm;
* the 10-question smoke gate (PLAN.md Day 6's acceptance);
* the two API routes and the 422 error body.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, cast

import pytest
from apps.api.agent.graph import CANNED_ABSTENTION
from apps.api.api.app import create_app
from apps.api.evaluation import config as eval_config
from apps.api.evaluation import dataset as dataset_module
from apps.api.evaluation import judge as eval_judge
from apps.api.evaluation import metrics as eval_metrics
from apps.api.evaluation import report as eval_report
from apps.api.evaluation import scorer as eval_scorer
from apps.api.evaluation import store as eval_store
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[3]
CORPUS = REPO_ROOT / "data" / "documents" / "civil-code-ra386-icnl.pdf"
DATASET_DIR = REPO_ROOT / "data" / "datasets" / "v1"
FIXTURE_CONFIG = Path(__file__).resolve().parents[1] / "evaluation" / "fixtures" / "example-run-config.json"

#: The synthetic corpus's articles. Each has its own topic, so the
#: retrieval layer can tell them apart and the gate can find a supporter.
_ARTICLES: list[tuple[str, str]] = [
    (
        "Article 1. Requisites of contracts.",
        "The requisites of contracts are consent, object, and certainty of the terms. "
        "A contract requires the consent of the parties, a lawful object, and certainty. ",
    ),
    (
        "Article 2. Succession.",
        "Succession is the transfer of the rights and obligations of a deceased person "
        "to the heirs or legatees by his will or by operation of law. ",
    ),
    (
        "Article 3. Ignorance of the law.",
        "Ignorance of the law excuses no one from compliance therewith. ",
    ),
    (
        "Article 4. Prescription.",
        "Prescription extinguishes a claim after the period laid down by law. The "
        "periods of prescription are enumerated in this article. ",
    ),
    (
        "Article 5. Damages.",
        "Damages are the sum of money which the court orders the defendant to pay. The "
        "kinds of damages are provided here. ",
    ),
    (
        "Article 6. Partition.",
        "Partition may be made by agreement between the parties or by judicial "
        "proceedings. Partition shall be governed by the Rules of Court. ",
    ),
    (
        "Article 7. Holographic wills.",
        "A person may execute a holographic will which must be entirely written, "
        "dated, and signed by the hand of the testator himself. ",
    ),
    (
        "Article 8. Usufruct.",
        "Usufruct gives a right to enjoy the property of another with the obligation "
        "of preserving its form and substance, unless the title otherwise provides. ",
    ),
]


def _fitz() -> ModuleType:
    import fitz

    return cast(ModuleType, fitz)


def _write_pdf(path: Path) -> None:
    """The synthetic statute: one page per article, a heading and a body."""
    fitz = _fitz()
    doc = fitz.open()
    for heading, body in _ARTICLES:
        page = doc.new_page()
        page.insert_textbox(
            fitz.Rect(50, 50, 560, 780), heading + "\n" + body * 3, fontsize=11
        )
    doc.save(path)
    doc.close()


@pytest.fixture(scope="module")
def corpus(tmp_path_factory: pytest.TempPathFactory) -> Any:
    """A synthetic document, ingested, indexed, and ready to query."""
    import shutil

    import apps.api.retrieval.scoring as scoring
    from apps.api.ingestion.service import ingest_pdf
    from apps.api.retrieval.index import build_indexes

    root = tmp_path_factory.mktemp("eval")
    pdf_path = root / "synthetic.pdf"
    _write_pdf(pdf_path)

    db_path = str(root / "corpus.db")
    document = ingest_pdf(str(pdf_path), slug="synth-doc", db_path=db_path)
    index_root = root / "indexes"
    build_indexes(db_path, document.id, index_root=index_root)

    original_root = scoring.INDEX_ROOT
    scoring.INDEX_ROOT = index_root
    try:
        yield {
            "db_path": db_path,
            "doc_id": document.id,
            "slug": "synth-doc",
            "index_root": str(index_root),
        }
    finally:
        scoring.INDEX_ROOT = original_root
        shutil.rmtree(root, ignore_errors=True)


@pytest.fixture(scope="module")
def real_corpus(tmp_path_factory: pytest.TempPathFactory) -> Any:
    """The REAL Civil Code, ingested once, for the dataset validation test."""
    if not os.path.exists(CORPUS):
        pytest.skip("corpus file missing: civil-code-ra386-icnl.pdf")
    from apps.api.ingestion.service import ingest_pdf
    from apps.api.retrieval.index import build_indexes

    root = tmp_path_factory.mktemp("eval-real")
    db_path = str(root / "civil-code.db")
    document = ingest_pdf(str(CORPUS), slug="civil-code-public", db_path=db_path)
    build_indexes(db_path, document.id, index_root=root / "indexes")
    return {"db_path": db_path, "doc_id": document.id, "slug": "civil-code-public"}


def _run_config(snapshot: str = "v1-test") -> eval_config.RunConfig:
    """The fixture run config, validated."""
    payload = json.loads(FIXTURE_CONFIG.read_text(encoding="utf-8"))
    payload["dataset_snapshot"] = snapshot
    payload.pop("config_id", None)
    return eval_config.load_run_config(payload)


def _run_config_for(
    judge: Any, snapshot: str = "v1-test"
) -> eval_config.RunConfig:
    """The fixture run config, naming the judge the run will actually use.

    The runner REFUSES a run whose config names a judge_prompt_version
    the judge does not have: the config's value is what the run was asked
    to use and the judge's is what it used, and recording one while using
    the other would make two scores look comparable when they came from
    two instruments. A test that injects a canned judge therefore has to
    name the canned judge, exactly as a real run names the real one.
    """
    payload = json.loads(FIXTURE_CONFIG.read_text(encoding="utf-8"))
    payload["dataset_snapshot"] = snapshot
    payload.pop("config_id", None)
    payload["config"]["judge_prompt_version"] = str(judge.prompt_version)
    return eval_config.load_run_config(payload)


# ---------------------------------------------------------------------------
# 1. The dataset (evaluation.md §2, §8)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def splits() -> tuple[dataset_module.Dataset, dataset_module.Dataset]:
    """The committed dev and test splits."""
    dev = dataset_module.load_split(DATASET_DIR, "dev")
    test = dataset_module.load_split(DATASET_DIR, "test")
    return dev, test


def test_dataset_split_sizes_and_stratification(splits: Any) -> None:
    """24 dev + 48 test, seven categories, each non-empty in both."""
    dev, test = splits
    dataset_module.validate_split(dev, expected_size=dataset_module.DEV_SIZE)
    dataset_module.validate_split(test, expected_size=dataset_module.TEST_SIZE)
    counts = dataset_module.validate_stratification(dev, test)
    assert set(counts) == set(dataset_module.CATEGORIES)
    for category, split_counts in counts.items():
        assert split_counts["dev"] >= dataset_module.MIN_PER_CATEGORY_DEV, category
        assert split_counts["test"] >= dataset_module.MIN_PER_CATEGORY_TEST, category


def test_dataset_has_no_overlap_and_enough_unanswerable(splits: Any) -> None:
    """No question in both splits; at least 5 unanswerable rows."""
    dev, test = splits
    dataset_module.validate_no_overlap(dev, test)
    count = dataset_module.validate_unanswerable_count(dev, test)
    assert count >= dataset_module.MIN_UNANSWERABLE


def test_dataset_gold_overlap_is_measured(splits: Any) -> None:
    """The shared GOLD provisions between the splits are measured, not hidden.

    The overlap check on question ids and question text is not enough:
    two questions can ask about the same provision in different words. The
    dev split is the tuning split and the test split is the one touched
    once per frozen config, so a shared gold provision means the tuning
    has already seen that answer key.

    The frozen split shares 9 provisions today (measured), so this asserts
    the MEASUREMENT exists and is reported, and that the report's
    effective-n figure is published next to the nominal n.
    """
    dev, test = splits
    overlap = dataset_module.gold_overlap(dev, test)

    assert overlap["n_test_gold_nodes"] > 0
    assert overlap["n_shared_gold_nodes"] >= 0
    assert 0.0 <= overlap["shared_fraction_of_test_golds"] <= 1.0
    # The measurement must be CONSISTENT with the splits it describes.
    dev_golds = {g for q in dev.questions for g in q.gold_nodes}
    test_golds = {g for q in test.questions for g in q.gold_nodes}
    assert overlap["shared_gold_nodes"] == sorted(dev_golds & test_golds)
    # Every question reported as fully contained must really be.
    for qid in overlap["test_questions_fully_contained_in_dev"]:
        question = next(q for q in test.questions if q.id == qid)
        assert set(question.gold_nodes) <= dev_golds

    # The effective sample size is the number of DISTINCT gold provisions
    # among the answerable questions. A question can carry SEVERAL golds,
    # so this is not bounded by the question count - what it bounds is the
    # number of independent PROVISIONS the split can say anything about,
    # and it is at most the total number of gold references.
    effective = dataset_module.effective_n(test)
    answerable = sum(1 for q in test.questions if q.answerable)
    total_references = sum(
        len(q.gold_nodes) for q in test.questions if q.answerable
    )
    assert 0 < effective <= total_references
    assert effective >= 1
    # Every distinct gold of an answerable question is counted exactly once.
    distinct = {
        g for q in test.questions if q.answerable for g in q.gold_nodes
    }
    assert effective == len(distinct)
    assert answerable >= 1


def test_the_report_publishes_the_effective_sample_size(splits: Any) -> None:
    """The report prints the effective n, not only the nominal n.

    A reader who sees only "n=41" reads 41 independent observations. Six
    gold provisions are used by more than one question, so the effective
    count is lower and the steps between numbers are larger than they
    look.
    """
    dev, test = splits
    overlap = dataset_module.gold_overlap(dev, test)
    text = eval_report.render_report(
        evaluation_id="eval_test",
        config={"config": {}, "config_id": "cfg_x", "k": [1, 3, 5, 10]},
        dataset_name=test.name,
        dataset_snapshot=test.snapshot_id,
        split_seed=test.seed,
        n_questions=48,
        aggregates={"arms": {}},
        dataset_overlap=overlap,
        n_distinct_golds=dataset_module.effective_n(test),
    )
    assert "n distinct golds" in text, "the report must publish the effective n"
    assert str(overlap["n_distinct_test_gold_provisions"]) in text
    # The shared golds must be disclosed as a limitation, not just counted.
    if overlap["n_shared_gold_nodes"]:
        assert "SHARE" in text, "the shared gold provisions must be disclosed"
        assert "tuning split" in text, (
            "the report must say what a shared gold provision costs"
        )


def test_dataset_seed_is_recorded(splits: Any) -> None:
    """The split seed is 42 and both splits carry it (evaluation.md §1)."""
    dev, test = splits
    assert dev.seed == dataset_module.SPLIT_SEED
    assert test.seed == dataset_module.SPLIT_SEED
    assert dev.seed == 42


def test_dataset_golds_resolve_against_the_real_corpus(real_corpus: Any) -> None:
    """D1 invariant 2: every gold resolves, and carries the document slug.

    This is the check PLAN.md calls "the most dangerous item": a gold id
    that does not resolve yields Recall@5 = 0 for every question while the
    run "completes" and nothing fails.
    """
    conn = sqlite3.connect(real_corpus["db_path"])
    conn.row_factory = sqlite3.Row
    try:
        node_ids = {str(row["id"]) for row in conn.execute("SELECT id FROM nodes")}
        slugs = {str(row["slug"]) for row in conn.execute("SELECT slug FROM documents")}
    finally:
        conn.close()
    for split in ("dev", "test"):
        dataset = dataset_module.load_split(DATASET_DIR, split)
        dataset_module.validate_against_corpus(dataset, node_ids, slugs)


def test_dataset_validator_rejects_a_bad_gold_id(splits: Any) -> None:
    """A gold that does not resolve is a LOUD failure, not a warning."""
    dev, _ = splits
    broken = dataset_module.Dataset(
        name="dev",
        snapshot_id=dev.snapshot_id,
        seed=dev.seed,
        questions=[
            dataset_module.Question(
                id="q001",
                question="What does Article 999999 say?",
                document="civil-code-public",
                category="direct-lookup",
                gold_nodes=["node_civil-code-public_article_999999"],
                expected_claims=["nothing"],
            )
        ],
    )
    with pytest.raises(dataset_module.DatasetError) as excinfo:
        dataset_module.validate_against_corpus(
            broken, {"node_civil-code-public_article_1318"}, {"civil-code-public"}
        )
    assert "999999" in str(excinfo.value)


def test_dataset_validator_rejects_an_unknown_document(splits: Any) -> None:
    """D1 invariant 3: the document must resolve to exactly one document."""
    dev, _ = splits
    broken = dataset_module.Dataset(
        name="dev",
        snapshot_id=dev.snapshot_id,
        seed=dev.seed,
        questions=[
            dataset_module.Question(
                id="q001",
                question="What?",
                document="not-a-document",
                category="direct-lookup",
                gold_nodes=["node_not-a-document_article_1"],
                expected_claims=["nothing"],
            )
        ],
    )
    with pytest.raises(dataset_module.DatasetError) as excinfo:
        dataset_module.validate_against_corpus(
            broken, {"node_not-a-document_article_1"}, {"civil-code-public"}
        )
    assert "not-a-document" in str(excinfo.value)


def test_dataset_unanswerable_rows_have_absent_vocabulary(real_corpus: Any) -> None:
    """Every token of every unanswerable row is absent from the corpus.

    The measurement is against a REAL ingest, and the check is the strict
    one the corpus demands: not merely the content tokens, but every
    token, because a single matching token gives lex = 1.0 and the
    sufficiency gate passes.
    """
    from apps.api.evaluation.generate_dataset import tokens_of

    conn = sqlite3.connect(real_corpus["db_path"])
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("SELECT title, text FROM nodes").fetchall()
    finally:
        conn.close()
    vocabulary: set[str] = set()
    for row in rows:
        vocabulary |= tokens_of(f"{row['title']} {row['text']}")

    for split in ("dev", "test"):
        dataset = dataset_module.load_split(DATASET_DIR, split)
        for question in dataset.questions:
            if question.answerable:
                continue
            for token in question_module_tokens(question.question):
                assert token not in vocabulary, (
                    f"{question.id}: the token {token!r} occurs in the corpus, so the "
                    "row is answerable by vocabulary"
                )


def question_module_tokens(question: str) -> list[str]:
    """The folded tokens a question carries, via the dataset module."""
    from apps.api.evaluation.generate_dataset import content_tokens

    return content_tokens(question)


def test_the_generator_checks_against_both_corpora(tmp_path: Path) -> None:
    """The generator's absence check REACHES the secondary corpus.

    The defect this pins: the generator checked the unanswerable rows
    against the vocabulary of every OTHER ingested document, and it
    ingested ONLY the Civil Code into its temporary store - so "every
    other document" was always the empty set and the check could never
    fire. "tell", "explain" and "enumerate" are absent from the Civil
    Code and PRESENT in the U.S. Code Title 11, so the generated dataset
    passed the generator's check and was then REFUSED by the runner,
    which checks the whole live store.

    So the generator has to ingest the secondary corpus for its own check
    to mean anything, and the rows have to be absent from BOTH.
    """
    from apps.api.evaluation.generate_dataset import SECONDARY_PDF, tokens_of
    from apps.api.ingestion.service import ingest_pdf

    if not os.path.exists(CORPUS) or not SECONDARY_PDF.exists():
        pytest.skip("a corpus PDF is missing")

    db_path = str(tmp_path / "both.db")
    ingest_pdf(str(CORPUS), slug="civil-code-public", db_path=db_path)
    ingest_pdf(str(SECONDARY_PDF), slug="usc-title-11", db_path=db_path)

    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("SELECT title, text FROM nodes").fetchall()
    finally:
        conn.close()
    vocabulary: set[str] = set()
    for row in rows:
        vocabulary |= tokens_of(f"{row[0]} {row[1]}")

    # The tokens the three rewritten rows now use, and the ones they
    # replaced. The replacements are absent from BOTH corpora; the
    # originals are present in the secondary one, which is why the rows
    # had to change.
    for token in ("narrate", "elucidate", "explicate"):
        assert token not in vocabulary, (
            f"{token!r} must be absent from BOTH corpora"
        )
    # The originals are present - that is what the generator missed.
    for token in ("tell", "explain", "enumerate"):
        assert token in vocabulary, (
            f"{token!r} is present in the secondary corpus, which is why the "
            f"rows using it had to be rewritten"
        )


def test_dataset_generator_is_deterministic() -> None:
    """The generator produces the committed split, byte for byte.

    The generator is committed beside the split so the split is
    reproducible and auditable. A generator whose output drifted from the
    committed file would make the committed file unauditable.
    """
    from apps.api.evaluation.generate_dataset import QUESTIONS, build_split

    dev_rows, test_rows = build_split(QUESTIONS, seed=dataset_module.SPLIT_SEED)
    committed_dev = dataset_module.load_split(DATASET_DIR, "dev")
    committed_test = dataset_module.load_split(DATASET_DIR, "test")
    assert [row["id"] for row in dev_rows] == [q.id for q in committed_dev.questions]
    assert [row["id"] for row in test_rows] == [q.id for q in committed_test.questions]
    assert [row["question"] for row in dev_rows] == [
        q.question for q in committed_dev.questions
    ]
    assert [row["question"] for row in test_rows] == [
        q.question for q in committed_test.questions
    ]


# ---------------------------------------------------------------------------
# 2. The shared scorer (evaluation.md §3)
# ---------------------------------------------------------------------------


def test_scorer_matches_on_a_substring() -> None:
    """A claim quoted verbatim matches, whatever the punctuation."""
    assert eval_scorer.claim_matches("consent", "The consent of the parties,")
    assert eval_scorer.claim_matches("Consent", "the consent of the parties")


def test_scorer_matches_on_the_fuzzy_rule() -> None:
    """A paraphrased claim matches at the 85 threshold.

    The threshold is the spec's (evaluation.md §3: RapidFuzz
    ``token_set_ratio`` >= 85), and it is not lowered to make a test
    pass. A paraphrase that drops a word scores below it and does NOT
    match, which is the correct direction for a metric that should not
    credit a claim the answer did not make.
    """
    assert eval_scorer.claim_matches(
        "consent of the contracting parties",
        "The consent of the contracting parties is required.",
    )
    assert not eval_scorer.claim_matches(
        "consent of the contracting parties",
        "The consent of the parties is required.",
    )
    assert not eval_scorer.claim_matches(
        "a wholly unrelated proposition about shipping",
        "The consent of the parties is required.",
    )


def test_scorer_is_case_and_punctuation_insensitive() -> None:
    """Case folding and punctuation stripping are applied to both sides."""
    assert eval_scorer.normalize("Consent, Object;") == "consent object"
    assert eval_scorer.claim_matches("object certain", "Object  certain.")


def test_scorer_fraction_counts_matched_claims() -> None:
    """The fraction is the matched claims over the total claims."""
    fraction = eval_scorer.claim_fraction(
        ["consent", "object", "cause"], "The consent and the object are required."
    )
    assert 0.0 < fraction < 1.0
    assert eval_scorer.claim_fraction([], "anything") == 0.0
    assert eval_scorer.claim_fraction(["consent"], "the consent") == 1.0


def test_split_sentences_is_the_one_splitter() -> None:
    """Sentences end at a terminator plus a capital, and are stripped."""
    text = "Article 2 applies.  It takes effect after publication.\n\nIs it retroactive?"
    assert eval_scorer.split_sentences(text) == [
        "Article 2 applies.",
        "It takes effect after publication.",
        "Is it retroactive?",
    ]
    assert eval_scorer.split_sentences("") == []


def test_citation_markers_expand_a_range() -> None:
    """A range marker names every number in the range, in order."""
    assert eval_scorer.citation_markers("a [3] b") == [3]
    assert eval_scorer.citation_markers("a [1-3] b") == [1, 2, 3]
    assert eval_scorer.citation_markers("a [3-1] b") == [1, 2, 3]
    assert eval_scorer.citation_markers("a [2] b [4]") == [2, 4]
    assert eval_scorer.citation_markers("no marker here") == []


def test_strip_citation_markers_leaves_the_proposition() -> None:
    """The marker is removed and the words of the sentence stay."""
    stripped = eval_scorer.strip_citation_markers("A sentence [2] with a marker [1-3].")
    assert "[" not in stripped and "]" not in stripped
    assert "A sentence" in stripped and "with a marker" in stripped


def test_split_citation_claims_attaches_each_marker_to_its_sentence() -> None:
    """One claim per marker, with the markers stripped from the sentence."""
    claims = eval_scorer.split_citation_claims(
        "Article 2 applies [3]. It is not retroactive [1-2]. No marker here."
    )
    assert [claim.marker for claim in claims] == [3, 1, 2]
    assert claims[0].sentence.startswith("Article 2 applies")
    assert claims[1].sentence.startswith("It is not retroactive")
    assert claims[1].sentence == claims[2].sentence
    assert all("[" not in claim.sentence for claim in claims)


# ---------------------------------------------------------------------------
# 3. The retrieval metrics (evaluation.md §3)
# ---------------------------------------------------------------------------


def _retrieval_row(
    question_id: str,
    category: str,
    retrieved: list[str],
    gold: list[str],
    arm: str = "tuklas",
) -> eval_metrics.RetrievalRow:
    return eval_metrics.RetrievalRow(
        question_id=question_id,
        arm=arm,
        category=category,
        answerable=bool(gold),
        retrieved_node_ids=retrieved,
        gold_node_ids=gold,
    )


def test_recall_and_precision_at_k() -> None:
    """Recall@K and Precision@K follow evaluation.md §3's definitions."""
    row = _retrieval_row("q1", "direct-lookup", ["a", "b", "c", "d", "e"], ["c", "d"])
    assert row.recall_at(1) == 0.0
    assert row.recall_at(3) == 1 / 2
    assert row.recall_at(5) == 1.0
    assert row.precision_at(1) == 0.0
    assert row.precision_at(3) == 1 / 3
    assert row.precision_at(5) == 2 / 5


def test_mrr_uses_the_first_gold_hit() -> None:
    """MRR@10 is 1 / rank of the first gold hit, else 0."""
    row = _retrieval_row("q1", "direct-lookup", ["x", "y", "gold", "z"], ["gold"])
    assert row.reciprocal_rank() == 1 / 3
    missed = _retrieval_row("q2", "direct-lookup", ["x"] * 12, ["gold"])
    assert missed.reciprocal_rank() == 0.0


def test_retrieval_aggregates_report_per_category_and_overall() -> None:
    """The aggregate carries the overall numbers and the per-category ones."""
    rows = [
        _retrieval_row("q1", "direct-lookup", ["a", "b"], ["a"]),
        _retrieval_row("q2", "direct-lookup", ["x", "y"], ["x"]),
        _retrieval_row("q3", "definition", ["a", "b", "c"], ["a"]),
    ]
    aggregates = eval_metrics.retrieval_aggregates(rows)
    assert aggregates["overall"]["n"] == 3
    assert aggregates["overall"]["recall@5"] == pytest.approx(1.0)
    assert aggregates["by_category"]["direct-lookup"]["recall@5"] == pytest.approx(1.0)
    assert aggregates["by_category"]["definition"]["n"] == 1


def test_unanswerable_rows_are_excluded_from_the_retrieval_aggregate() -> None:
    """A row with no gold set is EXCLUDED, not scored as zero."""
    rows = [
        _retrieval_row("q1", "direct-lookup", ["a"], ["a"]),
        _retrieval_row("q2", "direct-lookup", ["b", "c"], []),
    ]
    aggregates = eval_metrics.retrieval_aggregates(rows)
    assert aggregates["n_rows"] == 2
    assert aggregates["n_scored"] == 1
    assert aggregates["n_unanswerable_excluded"] == 1
    assert aggregates["overall"]["recall@5"] == pytest.approx(1.0)


def test_percentile_is_the_linear_interpolation() -> None:
    """p50 and p95 are computed, not guessed."""
    assert eval_metrics.percentile([], 0.5) == 0.0
    assert eval_metrics.percentile([5.0], 0.95) == 5.0
    assert eval_metrics.percentile([1.0, 2.0, 3.0], 0.5) == pytest.approx(2.0)
    assert eval_metrics.percentile([1.0, 2.0, 3.0, 4.0], 0.95) == pytest.approx(3.85)


# ---------------------------------------------------------------------------
# 4. The judge (evaluation.md §4)
# ---------------------------------------------------------------------------


def test_judge_payload_carries_no_arm() -> None:
    """The judge is BLINDED: its payload has no arm and no arm slot.

    The blinding is mechanical: the payload function's signature does not
    accept an arm, and the payload has three keys.
    """
    payload = eval_judge.judge_payload("a claim", "the evidence")
    assert set(payload) == {"claim", "evidence", "judge_prompt_version"}
    assert "arm" not in payload
    for value in payload.values():
        assert "arm" not in str(value).lower() or value == payload["judge_prompt_version"]


def test_judge_prompt_names_no_arm() -> None:
    """The rendered prompt does not leak an arm label."""
    rendered = eval_judge.render_judge_prompt("a claim", "the evidence")
    lowered = rendered.lower()
    assert "tuklas" not in lowered
    assert "vector" not in lowered
    assert "arm" not in lowered


def test_judge_prompt_is_versioned() -> None:
    """The prompt carries a version, and the payload reports it."""
    assert eval_judge.JUDGE_PROMPT_VERSION
    payload = eval_judge.judge_payload("a claim", "the evidence")
    assert payload["judge_prompt_version"] == eval_judge.JUDGE_PROMPT_VERSION


def test_judge_parses_only_a_leading_zero_or_one() -> None:
    """A judge's answer that says neither scores 0, never 1."""
    assert eval_judge.parse_verdict("1") == 1
    assert eval_judge.parse_verdict("1 the evidence supports it") == 1
    assert eval_judge.parse_verdict("0") == 0
    assert eval_judge.parse_verdict("I cannot determine") == 0
    assert eval_judge.parse_verdict("") == 0


def test_judge_splits_claims_deterministically() -> None:
    """The same answer is split the same way every time."""
    answer = "The consent of the parties is required. The object must be certain. Yes."
    claims = eval_judge.split_claims(answer)
    assert claims == eval_judge.split_claims(answer)
    assert len(claims) == 3


def test_judge_scores_a_claim_against_the_cited_evidence() -> None:
    """A claim the evidence supports scores 1; one it does not scores 0."""
    judge = eval_judge.CannedJudge()
    score, judged, verdicts = eval_judge.judge_answer(
        judge,
        "The consent of the parties is required.",
        {"node_1": "The consent of the parties is required for a contract."},
        ["node_1"],
    )
    assert judged == 1
    assert score == pytest.approx(1.0)
    assert verdicts[0].supported


def test_judge_gives_an_answer_with_no_evidence_zero() -> None:
    """An answer that cites nothing cannot be faithful."""
    judge = eval_judge.CannedJudge()
    score, judged, _ = eval_judge.judge_answer(
        judge, "The consent of the parties is required.", {}, []
    )
    assert judged == 1
    assert score == pytest.approx(0.0)


def test_the_batched_judge_prompt_names_no_arm() -> None:
    """The batched prompt is blinded exactly like the single-claim one.

    The batched prompt is a DIFFERENT prompt, so it needs its own blinding
    assertion: a prompt that leaked an arm label would be a blinded judge
    that is not blinded, and the leak would be in the prompt the runs
    actually use.
    """
    rendered = eval_judge.render_batch_judge_prompt(
        ["a claim", "another claim"], "the evidence"
    )
    lowered = rendered.lower()
    assert "tuklas" not in lowered
    assert "vector" not in lowered
    assert "arm" not in lowered


def test_the_batched_judge_prompt_is_versioned_separately() -> None:
    """The batched prompt has its OWN version.

    A faithfulness score produced by the batched prompt is not comparable
    with one produced by the single-claim prompt: they are two
    instruments. A single version for both would let a report compare
    them without saying so.
    """
    assert eval_judge.JUDGE_BATCH_PROMPT_VERSION
    assert eval_judge.JUDGE_BATCH_PROMPT_VERSION != eval_judge.JUDGE_PROMPT_VERSION


def test_the_batched_judge_parses_per_claim_verdicts() -> None:
    """One answer carries one verdict per claim, in the order asked."""
    text = "1 1\n2 0\n3 1"
    assert eval_judge.parse_batch_verdicts(text, 3) == [1, 0, 1]


def test_the_batched_judge_scores_an_unanswered_claim_zero() -> None:
    """A claim the model did not answer for scores 0, never 1.

    A dropped claim would RAISE the faithfulness score, which is the
    wrong direction for a failure.
    """
    assert eval_judge.parse_batch_verdicts("1 1", 3) == [1, 0, 0]
    assert eval_judge.parse_batch_verdicts("", 2) == [0, 0]
    assert eval_judge.parse_batch_verdicts("1 yes\n2 maybe", 2) == [0, 0]
    # A number outside the range is ignored rather than mapped.
    assert eval_judge.parse_batch_verdicts("1 1\n9 1", 2) == [1, 0]


def test_the_batched_judge_makes_one_call_per_answer() -> None:
    """The batched judge scores an answer's claims in ONE model call.

    This is the cost property, and it is what makes a 72-question split
    over two arms finish: a single-claim judge costs one call per claim,
    which is ~700 calls for the split.
    """

    class _CountingLLM:
        def __init__(self) -> None:
            self.calls = 0

        def generate(self, prompt: str, stop: list[str] | None = None,
                     max_tokens: int | None = None) -> Any:
            self.calls += 1
            return SimpleNamespace(text="1 1\n2 0\n3 1")

    llm = _CountingLLM()
    judge = eval_judge.BatchedLLMJudge(llm)
    claims = ["first claim", "second claim", "third claim"]
    verdicts = judge.score_claims(claims, "the evidence")

    assert llm.calls == 1, "the batched judge must make ONE call per answer"
    assert [v.score for v in verdicts] == [1, 0, 1]
    assert [v.claim for v in verdicts] == claims
    assert judge.prompt_version == eval_judge.JUDGE_BATCH_PROMPT_VERSION


def test_the_batched_judge_scores_zero_when_the_backend_fails() -> None:
    """A backend failure scores every claim 0, and does not raise."""

    class _BrokenLLM:
        def generate(self, prompt: str, stop: list[str] | None = None,
                     max_tokens: int | None = None) -> Any:
            raise RuntimeError("the backend is down")

    judge = eval_judge.BatchedLLMJudge(_BrokenLLM())
    verdicts = judge.score_claims(["a claim", "another claim"], "the evidence")
    assert [v.score for v in verdicts] == [0, 0]


def test_the_runner_refuses_a_config_that_names_a_different_judge(
    corpus: Any,
) -> None:
    """A config naming a judge prompt the run does not use is REFUSED.

    The config's ``judge_prompt_version`` is what the run was asked to
    use; the judge's own version is what it USED. Recording one while
    using the other would make two scores look comparable when they came
    from two instruments, and the run's own record is what a reader trusts
    to describe its numbers. So the contradiction is a loud failure, not
    a mismatch to note.
    """
    from apps.api.agent.graph import StubGenerator
    from apps.api.evaluation.runner import Runner

    judge = eval_judge.CannedJudge()
    payload = json.loads(FIXTURE_CONFIG.read_text(encoding="utf-8"))
    payload.pop("config_id", None)
    payload["config"]["judge_prompt_version"] = "some-other-judge-v9"
    config = eval_config.load_run_config(payload)

    runner = Runner(
        corpus["db_path"],
        corpus["slug"],
        config,
        index_root=Path(corpus["index_root"]),
        generator=StubGenerator(),
        judge=judge,
    )
    with pytest.raises(ValueError, match="judge_prompt_version"):
        runner.run(_smoke_dataset())


def test_judge_answer_uses_the_batched_path_when_the_judge_has_one() -> None:
    """judge_answer asks a batching judge for all the claims at once."""

    class _BatchOnlyJudge:
        prompt_version = "test-batch"

        def score_claims(self, claims: list[str], evidence: str) -> list[Any]:
            return [
                eval_judge.JudgeVerdict(claim=c, score=1, supported=True)
                for c in claims
            ]

        def score(self, claim: str, evidence: str) -> Any:
            raise AssertionError("the per-claim path must not be used")

        def score_claim(self, claim: str, evidence: str) -> int:
            raise AssertionError("the per-claim path must not be used")

    answer = "First claim. Second claim. Third claim."
    score, judged, verdicts = eval_judge.judge_answer(
        _BatchOnlyJudge(), answer, {"node_1": "evidence"}, ["node_1"]
    )
    assert judged == 3
    assert score == pytest.approx(1.0)
    assert len(verdicts) == 3


def test_spot_check_sample_is_stratified_and_at_least_ten() -> None:
    """The 20% sample is stratified and never smaller than 10."""
    ids = [f"q{index:03d}" for index in range(1, 49)]
    sample = eval_judge.spot_check_sample(ids)
    # 20% of 48 is 9, and the floor of 10 raises it to 10.
    assert len(sample) == 10
    assert sample == eval_judge.spot_check_sample(ids)
    assert sample == sorted(sample)
    # The sample is spread across the split by a fixed stride, so it
    # reaches the last rows and is not clustered at the front.
    assert sample[-1] in ids[40:]
    assert len(set(sample)) == len(sample)


def test_spot_check_disagreement_is_computed_per_claim() -> None:
    """The disagreement is the fraction of claims that differ."""
    assert eval_judge.spot_check_disagreement([1, 0, 1], [1, 0, 1]) == 0.0
    assert eval_judge.spot_check_disagreement([1, 0, 1, 0], [1, 1, 1, 0]) == pytest.approx(0.25)
    with pytest.raises(ValueError):
        eval_judge.spot_check_disagreement([1], [1, 0])


# ---------------------------------------------------------------------------
# 5. The run config and its id (api.md §4, evaluation.md §6)
# ---------------------------------------------------------------------------


def test_the_fixture_config_is_valid() -> None:
    """The committed fixture config passes the checker."""
    payload = json.loads(FIXTURE_CONFIG.read_text(encoding="utf-8"))
    violations, config_id = eval_config.check_run_config(payload)
    assert violations == []
    assert config_id == payload["config_id"]


def test_the_config_id_is_derived_from_the_content() -> None:
    """The same config yields the same id; a change yields a different one."""
    payload = json.loads(FIXTURE_CONFIG.read_text(encoding="utf-8"))
    first = eval_config.derive_config_id(payload)
    assert first == eval_config.derive_config_id(dict(payload))
    changed = json.loads(FIXTURE_CONFIG.read_text(encoding="utf-8"))
    changed["config"]["seed"] = 43
    assert eval_config.derive_config_id(changed) != first


def test_the_config_checker_rejects_a_parity_violation() -> None:
    """Arms that disagree on the model are an INVALID run."""
    payload = json.loads(FIXTURE_CONFIG.read_text(encoding="utf-8"))
    payload["arms"]["vector"]["model"] = "some-other-model"
    payload.pop("config_id", None)
    violations, _ = eval_config.check_run_config(payload)
    assert any("parity" in violation for violation in violations), violations


def test_the_config_checker_rejects_a_non_zero_temperature() -> None:
    """Temperature 0 is fixed (api.md §4)."""
    payload = json.loads(FIXTURE_CONFIG.read_text(encoding="utf-8"))
    payload["config"]["temperature"] = 0.7
    payload["arms"]["tuklas"]["temperature"] = 0.7
    payload["arms"]["vector"]["temperature"] = 0.7
    payload.pop("config_id", None)
    violations, _ = eval_config.check_run_config(payload)
    assert any("temperature" in violation for violation in violations), violations


# ---------------------------------------------------------------------------
# 6. The smoke run (PLAN.md Day 6's acceptance)
# ---------------------------------------------------------------------------


def _smoke_dataset() -> dataset_module.Dataset:
    """A ten-question dataset over the synthetic corpus.

    The acceptance gate is a validity check, not the ratchet set
    (CONSTRAINTS.md: "the 10Q smoke gate is a validity check, not the
    ratchet set"), so it runs over a SMALL dataset and the answer
    generator is the deterministic stub.
    """
    questions = [
        dataset_module.Question(
            id="q001",
            question="What are the requisites of contracts?",
            document="synth-doc",
            category="direct-lookup",
            gold_nodes=["node_synth-doc_article_1"],
            expected_claims=["consent", "object"],
        ),
        dataset_module.Question(
            id="q002",
            question="What is succession?",
            document="synth-doc",
            category="definition",
            gold_nodes=["node_synth-doc_article_2"],
            expected_claims=["transfer", "heirs"],
        ),
        dataset_module.Question(
            id="q003",
            question="Does ignorance of the law excuse compliance?",
            document="synth-doc",
            category="exception",
            gold_nodes=["node_synth-doc_article_3"],
            expected_claims=["ignorance", "excuses"],
        ),
        dataset_module.Question(
            id="q004",
            question="What is prescription?",
            document="synth-doc",
            category="definition",
            gold_nodes=["node_synth-doc_article_4"],
            expected_claims=["extinguishes", "claim"],
        ),
        dataset_module.Question(
            id="q005",
            question="What are damages?",
            document="synth-doc",
            category="definition",
            gold_nodes=["node_synth-doc_article_5"],
            expected_claims=["sum of money"],
        ),
        dataset_module.Question(
            id="q006",
            question="How is partition made?",
            document="synth-doc",
            category="multi-section",
            gold_nodes=["node_synth-doc_article_6"],
            expected_claims=["agreement", "judicial"],
        ),
        dataset_module.Question(
            id="q007",
            question="What is a holographic will?",
            document="synth-doc",
            category="definition",
            gold_nodes=["node_synth-doc_article_7"],
            expected_claims=["entirely written", "signed"],
        ),
        dataset_module.Question(
            id="q008",
            question="What is usufruct?",
            document="synth-doc",
            category="definition",
            gold_nodes=["node_synth-doc_article_8"],
            expected_claims=["right to enjoy"],
        ),
        dataset_module.Question(
            id="q009",
            question="Explain zeppelin airship quantum observatory",
            document="synth-doc",
            category="direct-lookup",
            gold_nodes=[],
            expected_claims=[],
            answerable=False,
        ),
        dataset_module.Question(
            id="q010",
            question="Outline spectrometer centrifuge embargo",
            document="synth-doc",
            category="exception",
            gold_nodes=[],
            expected_claims=[],
            answerable=False,
        ),
    ]
    return dataset_module.Dataset(
        name="smoke",
        questions=questions,
        snapshot_id="smoke-10",
    )


def _smoke_runner(corpus: Any) -> Any:
    """A runner over the synthetic corpus, with the stub generator."""
    from apps.api.agent.graph import StubGenerator
    from apps.api.evaluation.runner import Runner

    judge = eval_judge.CannedJudge()
    config = _run_config_for(judge)
    return Runner(
        corpus["db_path"],
        corpus["slug"],
        config,
        index_root=Path(corpus["index_root"]),
        generator=StubGenerator(),
        judge=judge,
    )


def test_the_smoke_run_completes_and_persists(corpus: Any) -> None:
    """The 10-question smoke: it completes, and the rows are joinable.

    PLAN.md Day 6's acceptance: the smoke completes; all rows carry node
    ids (except ``insufficient: true`` rows, which carry ``[]``); the
    judge scores are present.
    """
    runner = _smoke_runner(corpus)
    outcome = runner.run(_smoke_dataset())

    assert outcome["status"] == "done"
    assert outcome["aggregates"]["n_questions"] == 10
    assert outcome["aggregates"]["n_arms"] == 2

    loaded = eval_store.load_evaluation(corpus["db_path"], outcome["evaluation_id"])
    assert loaded is not None
    rows = loaded["questions"]
    assert len(rows) == 20  # 10 questions x 2 arms
    keys = {(row["question_id"], row["arm"]) for row in rows}
    assert len(keys) == 20

    for row in rows:
        if row["insufficient"]:
            # An abstention carries NO node ids, by contract (evaluation.md §6).
            assert row["retrieved_nodes"] == [] or isinstance(row["retrieved_nodes"], list)
        else:
            assert row["retrieved_nodes"], row
            assert all(str(node).startswith("node_synth-doc_") for node in row["retrieved_nodes"])


def test_the_smoke_run_records_the_judge_scores(corpus: Any) -> None:
    """The judge scores are present on the rows that have claims."""
    runner = _smoke_runner(corpus)
    outcome = runner.run(_smoke_dataset())
    loaded = eval_store.load_evaluation(corpus["db_path"], outcome["evaluation_id"])
    assert loaded is not None
    judged = [row for row in loaded["questions"] if row["claims_total"] > 0]
    assert judged
    for row in judged:
        assert row["faithfulness"] is not None
        assert 0.0 <= float(row["faithfulness"]) <= 1.0


def test_the_smoke_run_refuses_the_unanswerable_rows(corpus: Any) -> None:
    """The VECTORLESS arm refuses the unanswerable rows, with no citations.

    The refusal detection is BINDING (evaluation.md §6): a refusal is
    ``insufficient: true`` with ``citations: []``.

    The assertion is ARM-SPECIFIC, and deliberately so. The dense arm's
    sufficiency gate CANNOT fail on lexical grounds — its score is a
    cosine similarity, which is non-zero for every node against every
    query, so the support rule always passes. Its abstention score is
    therefore structurally 0, which is a property of the arm's ranking
    and not a measurement of its refusal quality. The report states this.
    """
    runner = _smoke_runner(corpus)
    outcome = runner.run(_smoke_dataset())
    loaded = eval_store.load_evaluation(corpus["db_path"], outcome["evaluation_id"])
    assert loaded is not None
    vectorless = [
        row
        for row in loaded["questions"]
        if not bool(row["answerable"]) and row["arm"] == "tuklas"
    ]
    assert vectorless
    for row in vectorless:
        assert bool(row["insufficient"]), row
        assert row["citation_node_ids"] == []
    aggregates = outcome["aggregates"]["arms"]["tuklas"]["generation"]
    assert aggregates["abstention_score"] == pytest.approx(1.0)


def test_the_dense_arm_gate_cannot_fail_lexically(corpus: Any) -> None:
    """The dense arm's gate always passes; the vectorless arm's can fail.

    This pins a STRUCTURAL asymmetry between the arms, which the run
    report states. The support rule is "the node's lex component is
    above 0". The vectorless arm's lex layer is all-zero when no query
    term matches any node, so its gate fails closed. The dense arm's
    score is a cosine similarity, which is non-zero for every node
    against every query, so its gate never fails on lexical grounds.

    The consequence is that the dense arm's abstention score is
    structurally 0 on this dataset — a property of the arm's ranking,
    not a measurement of its refusal quality. A test that asserted the
    two arms' abstention scores were comparable would be asserting
    something false.
    """
    runner = _smoke_runner(corpus)
    outcome = runner.run(_smoke_dataset())
    vectorless = outcome["aggregates"]["arms"]["tuklas"]["generation"]
    dense = outcome["aggregates"]["arms"]["vector"]["generation"]
    assert vectorless["abstention_score"] == pytest.approx(1.0)
    assert dense["abstention_score"] == pytest.approx(0.0)


def test_the_smoke_run_reports_the_arms_separately(corpus: Any) -> None:
    """The aggregates carry both arms, and no verdict between them."""
    runner = _smoke_runner(corpus)
    outcome = runner.run(_smoke_dataset())
    aggregates = outcome["aggregates"]
    assert set(aggregates["arms"]) == {"tuklas", "vector"}
    for arm in aggregates["arms"].values():
        assert "retrieval" in arm
        assert "generation" in arm
        assert "system" in arm
    # No winner field anywhere in the payload.
    serialised = json.dumps(aggregates, default=str).lower()
    for forbidden in ("winner", "beat", "better", "superior", "outperform"):
        assert forbidden not in serialised


def test_the_smoke_run_is_reproducible(corpus: Any) -> None:
    """The same config over the same snapshot reproduces retrieval EXACTLY.

    evaluation.md §6: "Same config → same retrieval metrics exactly".
    """
    dataset = _smoke_dataset()
    first = _smoke_runner(corpus).run(dataset)
    second = _smoke_runner(corpus).run(dataset)

    for arm in ("tuklas", "vector"):
        one = first["aggregates"]["arms"][arm]["retrieval"]["overall"]
        two = second["aggregates"]["arms"][arm]["retrieval"]["overall"]
        assert one == two, arm


# ---------------------------------------------------------------------------
# 7. The API routes (api.md §4)
# ---------------------------------------------------------------------------


@pytest.fixture
def client(corpus: Any) -> TestClient:
    """A test client bound to the fixture's temporary database."""
    return TestClient(create_app(db_path=corpus["db_path"]))


def _valid_body() -> dict[str, Any]:
    """A valid evaluation request body."""
    payload: dict[str, Any] = json.loads(FIXTURE_CONFIG.read_text(encoding="utf-8"))
    payload.pop("config_id", None)
    return payload


def test_post_evaluation_rejects_a_malformed_body(client: TestClient) -> None:
    """A bad body is the frozen 422, not FastAPI's default shape."""
    response = client.post("/v1/evaluations", json={"dataset_snapshot": "v1-test"})
    assert response.status_code == 422
    body = response.json()
    assert set(body.keys()) == {"error"}
    assert body["error"]["code"] == "VALIDATION_ERROR"
    # The details NAME the offending field (api.md §1: "details naming the
    # offending field and the problem type"), and never echo the value.
    assert body["error"]["details"]
    for field, kind in body["error"]["details"].items():
        assert field.startswith("body")
        assert kind


def test_post_evaluation_rejects_an_unknown_snapshot(client: TestClient) -> None:
    """An unknown snapshot is a 422 (api.md §4)."""
    body = _valid_body()
    body["dataset_snapshot"] = "v1-nope"
    response = client.post("/v1/evaluations", json=body)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_post_evaluation_rejects_an_unknown_arm(client: TestClient) -> None:
    """An arm mismatch is a 422 (api.md §4)."""
    body = _valid_body()
    body["arms"]["third"] = dict(body["arms"]["tuklas"])
    response = client.post("/v1/evaluations", json=body)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_post_evaluation_rejects_a_parity_violation(client: TestClient) -> None:
    """Arms that disagree on the model are an invalid run, and a 422."""
    body = _valid_body()
    body["arms"]["vector"]["model"] = "another-model"
    response = client.post("/v1/evaluations", json=body)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_post_evaluation_rejects_a_non_zero_temperature(client: TestClient) -> None:
    """Temperature 0 is fixed, so a non-zero value is a bad body."""
    body = _valid_body()
    body["config"]["temperature"] = 0.7
    response = client.post("/v1/evaluations", json=body)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_get_evaluation_answers_404_for_an_unknown_id(client: TestClient) -> None:
    """An unknown evaluation is a 404 (api.md §4)."""
    response = client.get("/v1/evaluations/eval_deadbeef")
    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "NOT_FOUND"


def test_post_evaluation_rejects_a_run_that_names_only_some_arms(
    client: TestClient,
) -> None:
    """A run must name EVERY arm, not a subset of them.

    A run that named one arm would produce one arm's numbers and be read
    as a comparison, which is the thing the spec forbids: both arms, or
    neither.
    """
    body = _valid_body()
    body["arms"].pop("vector")
    response = client.post("/v1/evaluations", json=body)
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert "vector" in str(error["details"])


def test_post_evaluation_rejects_a_run_config_the_checker_refuses(
    client: TestClient,
) -> None:
    """A config the parity checker refuses is a 422 naming the violations.

    The checker's own message is the useful one: it says WHICH field
    disagrees, and a client that only saw "invalid" would have to guess.
    """
    body = _valid_body()
    body["config"]["fuzzy_threshold"] = 99
    response = client.post("/v1/evaluations", json=body)
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert "fuzzy_threshold" in str(error["details"])


def test_post_evaluation_reports_an_unreadable_snapshot_as_a_422(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A snapshot that cannot be read is a 422 naming the reason.

    The route has a distinct answer for "the snapshot does not load" and
    for "the config does not load", and both name the reason: a client
    that only saw "invalid" would have to guess which of the two it hit.
    """
    from apps.api.evaluation import dataset as dataset_module

    def _boom(*args: Any, **kwargs: Any) -> Any:
        raise dataset_module.DatasetError("the snapshot file is corrupt")

    monkeypatch.setattr(dataset_module, "load_dataset", _boom)

    response = client.post("/v1/evaluations", json=_valid_body())
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert "corrupt" in str(error["details"])


def test_post_evaluation_reports_an_unloadable_config_as_a_422(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A config that cannot be loaded is a 422, not a 500.

    The config is validated twice: once by the parity checker, which
    reports the violations it found, and once by the loader, which can
    fail on something the checker does not look at. The second failure is
    a bad REQUEST, so it is a 422 and not a server fault.
    """
    from apps.api.evaluation import config as eval_config_module

    def _boom(*args: Any, **kwargs: Any) -> Any:
        raise eval_config_module.ConfigError("the weights are not a mapping")

    monkeypatch.setattr(eval_config_module, "load_run_config", _boom)

    response = client.post("/v1/evaluations", json=_valid_body())
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert "not a mapping" in str(error["details"])


def test_post_evaluation_reports_a_runner_crash_as_eval_failed(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A runner crash is a 500 EVAL_FAILED, and leaks nothing.

    api.md §4 names EVAL_FAILED for a runner crash, and api.md §1 says the
    details of an internal fault are logged and never returned. So the
    body carries the frozen error shape and NO traceback, and the
    exception is still logged.
    """
    from apps.api.evaluation import runner as eval_runner_module

    def _boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("the runner exploded: /secret/path/detail")

    monkeypatch.setattr(eval_runner_module.Runner, "run", _boom)

    response = client.post("/v1/evaluations", json=_valid_body())
    assert response.status_code == 500
    body = response.json()
    assert set(body.keys()) == {"error"}
    assert body["error"]["code"] == "EVAL_FAILED"
    assert body["error"]["details"] == {}, "the details of a fault are never returned"
    assert "secret" not in response.text, "the internal message must not leak"
    assert "Traceback" not in response.text


def test_get_evaluation_returns_the_frozen_shape(corpus: Any) -> None:
    """The GET answer is the §4 shape: status, config, rows, aggregates."""
    from apps.api.agent.graph import StubGenerator
    from apps.api.evaluation.runner import Runner

    judge = eval_judge.CannedJudge()
    runner = Runner(
        corpus["db_path"],
        corpus["slug"],
        _run_config_for(judge),
        index_root=Path(corpus["index_root"]),
        generator=StubGenerator(),
        judge=judge,
    )
    outcome = runner.run(_smoke_dataset())

    client = TestClient(create_app(db_path=corpus["db_path"]))
    response = client.get(f"/v1/evaluations/{outcome['evaluation_id']}")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {
        "status",
        "config",
        "per_question",
        "aggregates",
        "system_stats",
    }
    assert body["status"] == "done"
    assert len(body["per_question"]) == 20
    row = body["per_question"][0]
    assert set(row) == {
        "question_id",
        "arm",
        "retrieved_node_ids",
        "gold_hit",
        "rank_first_hit",
        "claims_matched",
        "claims_total",
        "faithfulness",
        "citations_valid",
        "insufficient",
        "latency_ms",
        "tokens",
    }
    assert set(row["latency_ms"]) == {"retrieval", "generation"}
    assert set(row["tokens"]) == {"in", "out"}
    assert set(body["system_stats"]) == {
        "n_questions",
        "n_arms",
        "split_seed",
        "dataset_snapshot",
    }
    assert set(body["aggregates"]["arms"]) == {"tuklas", "vector"}


# ---------------------------------------------------------------------------
# 8. The report (evaluation.md §7)
# ---------------------------------------------------------------------------


def test_the_report_states_the_asymmetry_and_claims_no_verdict() -> None:
    """The report names the asymmetry and states no outcome.

    evaluation.md §7: the wording is "competitive on X, weaker on Y,
    measured on Z (n=48)" — never "superior". The report must not name a
    winner, and it must name the identifier-precedence asymmetry.
    """
    text = eval_report.render_report(
        evaluation_id="eval_test",
        config=_valid_body(),
        dataset_name="test",
        dataset_snapshot="v1-test",
        split_seed=42,
        n_questions=48,
        aggregates={
            "arms": {
                "tuklas": {
                    "retrieval": {"overall": {"recall@5": 0.5}, "by_category": {}},
                    "generation": {
                        "correctness": 0.5,
                        "faithfulness": 0.5,
                        "citation_accuracy": 1.0,
                        "abstention_score": 1.0,
                    },
                    "system": {"latency_p50": 1.0, "latency_p95": 2.0},
                },
                "vector": {
                    "retrieval": {"overall": {"recall@5": 0.4}, "by_category": {}},
                    "generation": {
                        "correctness": 0.4,
                        "faithfulness": 0.4,
                        "citation_accuracy": 1.0,
                        "abstention_score": 1.0,
                    },
                    "system": {"latency_p50": 3.0, "latency_p95": 4.0},
                },
            },
            "n_questions": 48,
            "n_arms": 2,
        },
        spot_check={"performed": False, "reason": "no re-scores supplied"},
    )
    lowered = text.lower()
    assert "identifier precedence" in lowered
    assert "like-for-like" in lowered
    assert "not a like-for-like result" in " ".join(lowered.split())
    for forbidden in (
        "outperforms",
        "beats",
        "better than",
        "superior",
        "most accurate",
        "best",
    ):
        assert forbidden not in lowered, forbidden


def test_the_report_names_the_run_and_its_config() -> None:
    """The report names n, the seed, the model, the judge version."""
    text = eval_report.render_report(
        evaluation_id="eval_test",
        config=_valid_body(),
        dataset_name="test",
        dataset_snapshot="v1-test",
        split_seed=42,
        n_questions=48,
        aggregates={"arms": {}, "n_questions": 48, "n_arms": 2},
    )
    assert "n questions          : 48" in text
    assert "split seed           : 42" in text
    assert "qwen2.5:3b" in text
    assert "judge-v1" in text
    assert "eval_test" in text


def test_the_canned_abstention_is_the_contract_string() -> None:
    """The refusal wording is the one api.md §3 pins."""
    assert CANNED_ABSTENTION == (
        "The retrieved evidence is insufficient to answer this question from the "
        "documents."
    )
