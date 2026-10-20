"""The frozen 72-question benchmark dataset: schema, loader, validator.

evaluation.md §2 owns the schema; §8 owns the verification checklist. This
module holds the SCHEMA and the VALIDATION, and nothing else: the questions
themselves live in the committed split files under ``data/datasets/v1/``, and
the generator that produced them is
``apps/api/evaluation/generate_dataset.py``.

Three things are worth stating about the shape of this module, because they
are decisions and not consequences:

1. **The schema is a dataclass, not a pydantic model.** The dataset is a
   frozen artefact, read from JSON, validated once, and never mutated. A
   dataclass gives the fields names and types without a second validation
   layer; the pydantic models in ``apps/api/models/`` are the API's
   contract, and this is not the API.

2. **The validation is LOUD.** A gold node id that does not resolve, a
   question whose ``document`` resolves to zero or two documents, a question
   in both splits, a category with too few rows: each of these raises
   :class:`DatasetError` and names the offending question. The Day-6
   measured finding is that a gold id that does not resolve yields
   ``Recall@5 = 0`` for EVERY question while the run "completes" and nothing
   fails. A validator that warns cannot prevent that; a validator that
   raises can.

3. **The unanswerable rows are checked against the corpus, not trusted.**
   See :func:`check_unanswerable_absence` — the constraint is measured, not
   asserted.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: The seven categories evaluation.md §2 declares. The split is stratified
#: over exactly this set: every category is non-empty in BOTH splits.
CATEGORIES: tuple[str, ...] = (
    "direct-lookup",
    "definition",
    "multi-section",
    "cross-reference",
    "exception",
    "multi-hop",
    "comparison",
)

#: The split sizes evaluation.md §1 freezes: 24 dev + 48 test = 72.
DEV_SIZE = 24
TEST_SIZE = 48
TOTAL_SIZE = DEV_SIZE + TEST_SIZE

#: The stratification floor evaluation.md §1 and §8 state: at least 3 dev
#: and at least 6 test rows per category.
MIN_PER_CATEGORY_DEV = 3
MIN_PER_CATEGORY_TEST = 6

#: The planted-unanswerable floor evaluation.md §8 states: at least 5 rows
#: with ``answerable: false`` across the whole dataset.
MIN_UNANSWERABLE = 5

#: The split seed evaluation.md §1 pins. It is recorded in the dataset file
#: and in every run's config, so a run can name the shuffle that produced
#: the split it measured.
SPLIT_SEED = 42

#: The document slug every question in v1 is asked against. The corpus is
#: the Civil Code of the Philippines (docs/CORPUS.md, D2).
DOCUMENT_SLUG = "civil-code-public"

#: The node-id prefix a question's golds must carry (evaluation.md §2: a
#: question's golds must all start with ``node_{question.document}_``).
GOLD_PREFIX = "node_"

#: A question id: ``q013``. The dataset's own numbering.
_QUESTION_ID_RE = re.compile(r"^q\d{3}$")

#: A document slug: lowercase alphanumerics with single internal hyphens
#: (architecture.md §3's slug rule). Used to reject a malformed slug
#: before it reaches the database.
_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

#: A content token: a lowercase alphanumeric run. The tokenization used by
#: the corpus-absence check, and deliberately the same shape as the BM25
#: tokenizer's INPUT (the BM25 index folds suffixes, so ``requirements`` is
#: indexed as ``requirement``; the absence check folds the same way, so a
#: word is absent only when its folded form is absent too).
_TOKEN_RE = re.compile(r"[a-z0-9]+")


class DatasetError(Exception):
    """The dataset is invalid. A loud failure, naming the offending rows.

    Every message names the questions involved. "Invalid dataset" without a
    question id is not actionable: the whole point of raising is that
    someone can go and look at the offending row.
    """


@dataclass(frozen=True)
class Question:
    """One dataset row (evaluation.md §2's schema).

    The field names are the schema's own: ``id``, ``question``,
    ``document``, ``category``, ``gold_nodes``, ``expected_claims``,
    ``answerable``, ``notes``.
    """

    id: str
    question: str
    document: str
    category: str
    gold_nodes: list[str] = field(default_factory=list)
    expected_claims: list[str] = field(default_factory=list)
    answerable: bool = True
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        """The row as the committed JSON file spells it."""
        return {
            "id": self.id,
            "question": self.question,
            "document": self.document,
            "category": self.category,
            "gold_nodes": list(self.gold_nodes),
            "expected_claims": list(self.expected_claims),
            "answerable": self.answerable,
            "notes": self.notes,
        }


@dataclass(frozen=True)
class Dataset:
    """A loaded split: its rows, its name, and the metadata around it."""

    name: str
    questions: list[Question]
    snapshot_id: str
    seed: int = SPLIT_SEED
    notes: str = ""

    def by_id(self) -> dict[str, Question]:
        """The rows keyed by question id."""
        return {question.id: question for question in self.questions}

    def to_dict(self) -> dict[str, Any]:
        """The split as the committed JSON file spells it."""
        return {
            "name": self.name,
            "snapshot_id": self.snapshot_id,
            "seed": self.seed,
            "notes": self.notes,
            "questions": [question.to_dict() for question in self.questions],
        }


def _require(condition: bool, message: str) -> None:
    """Raise :class:`DatasetError` with ``message`` when ``condition`` is false."""
    if not condition:
        raise DatasetError(message)


def parse_question(raw: Any, *, where: str) -> Question:
    """One JSON row into a :class:`Question`, or a loud failure.

    The row is validated field by field, and the message names the field
    AND the row, because a dataset file with one malformed row must be
    fixable without reading the whole file.
    """
    _require(isinstance(raw, dict), f"{where}: a question row must be an object")
    assert isinstance(raw, dict)

    for key in ("id", "question", "document", "category", "answerable"):
        _require(key in raw, f"{where}: missing required field {key!r}")

    question_id = str(raw["id"])
    _require(
        bool(_QUESTION_ID_RE.match(question_id)),
        f"{where}: question id {question_id!r} must match 'q<3 digits>'",
    )
    text = str(raw["question"])
    _require(bool(text.strip()), f"{where} ({question_id}): the question text is empty")
    document = str(raw["document"])
    _require(
        bool(_SLUG_RE.match(document)),
        f"{where} ({question_id}): document slug {document!r} is malformed",
    )
    category = str(raw["category"])
    _require(
        category in CATEGORIES,
        f"{where} ({question_id}): category {category!r} is not one of {list(CATEGORIES)}",
    )

    gold_nodes = raw.get("gold_nodes", [])
    _require(
        isinstance(gold_nodes, list),
        f"{where} ({question_id}): gold_nodes must be a list",
    )
    golds = [str(node) for node in gold_nodes]
    for gold in golds:
        _require(
            gold.startswith(f"{GOLD_PREFIX}{document}_"),
            f"{where} ({question_id}): gold {gold!r} does not start with "
            f"'node_{document}_' (evaluation.md §2)",
        )

    claims = raw.get("expected_claims", [])
    _require(
        isinstance(claims, list),
        f"{where} ({question_id}): expected_claims must be a list",
    )
    expected_claims = [str(claim) for claim in claims]

    answerable = raw["answerable"]
    _require(
        isinstance(answerable, bool),
        f"{where} ({question_id}): answerable must be a boolean",
    )
    if answerable:
        _require(
            bool(golds),
            f"{where} ({question_id}): an answerable question must carry at least one gold node",
        )
        _require(
            bool(expected_claims),
            f"{where} ({question_id}): an answerable question must carry at least one expected claim",
        )
    else:
        _require(
            not golds,
            f"{where} ({question_id}): an unanswerable question must not carry gold nodes",
        )

    return Question(
        id=question_id,
        question=text,
        document=document,
        category=category,
        gold_nodes=golds,
        expected_claims=expected_claims,
        answerable=answerable,
        notes=str(raw.get("notes", "")),
    )


def parse_dataset(raw: Any, *, name: str) -> Dataset:
    """A whole split file into a :class:`Dataset`, or a loud failure."""
    _require(isinstance(raw, dict), f"{name}: a split file must be a JSON object")
    assert isinstance(raw, dict)
    _require("questions" in raw, f"{name}: the split file has no 'questions' key")

    rows = raw["questions"]
    _require(isinstance(rows, list), f"{name}: 'questions' must be a list")
    questions = [
        parse_question(row, where=f"{name}[{index}]") for index, row in enumerate(rows)
    ]

    seen: set[str] = set()
    for question in questions:
        _require(
            question.id not in seen,
            f"{name}: duplicate question id {question.id!r}",
        )
        seen.add(question.id)

    seed = raw.get("seed", SPLIT_SEED)
    _require(
        isinstance(seed, int) and not isinstance(seed, bool),
        f"{name}: 'seed' must be an integer",
    )
    return Dataset(
        name=str(raw.get("name", name)),
        questions=questions,
        snapshot_id=str(raw.get("snapshot_id", name)),
        seed=seed,
        notes=str(raw.get("notes", "")),
    )


def load_dataset(path: str | Path) -> Dataset:
    """Load one split file from disk.

    The split's own name is the file's stem, so ``dev.json`` is the ``dev``
    split.
    """
    file_path = Path(path)
    try:
        raw = json.loads(file_path.read_text(encoding="utf-8"))
    except FileNotFoundError as err:
        raise DatasetError(f"dataset snapshot missing: {file_path.as_posix()}") from err
    except json.JSONDecodeError as err:
        raise DatasetError(f"{file_path.as_posix()}: invalid JSON ({err})") from err
    return parse_dataset(raw, name=file_path.stem)


def load_split(directory: str | Path, split: str) -> Dataset:
    """Load ``<directory>/<split>.json``."""
    return load_dataset(Path(directory) / f"{split}.json")


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_split(dataset: Dataset, *, expected_size: int) -> None:
    """The per-split half of evaluation.md §8's checklist.

    Fails loudly on: the wrong row count, an empty or over-full category, a
    category that is not one of the seven.
    """
    _require(
        len(dataset.questions) == expected_size,
        f"{dataset.name}: expected {expected_size} questions, got {len(dataset.questions)}",
    )
    counts = Counter(question.category for question in dataset.questions)
    for category in CATEGORIES:
        _require(
            counts.get(category, 0) > 0,
            f"{dataset.name}: category {category!r} is empty (evaluation.md §8)",
        )
    unknown = sorted(set(counts) - set(CATEGORIES))
    _require(
        not unknown,
        f"{dataset.name}: unknown categories {unknown}",
    )


def validate_stratification(
    dev: Dataset, test: Dataset
) -> dict[str, dict[str, int]]:
    """The stratification floor: >=3 dev and >=6 test rows per category.

    Returns the per-category counts so the caller can report them.
    """
    dev_counts = Counter(question.category for question in dev.questions)
    test_counts = Counter(question.category for question in test.questions)
    for category in CATEGORIES:
        _require(
            dev_counts.get(category, 0) >= MIN_PER_CATEGORY_DEV,
            f"dev: category {category!r} has {dev_counts.get(category, 0)} rows, "
            f"needs >= {MIN_PER_CATEGORY_DEV} (evaluation.md §1)",
        )
        _require(
            test_counts.get(category, 0) >= MIN_PER_CATEGORY_TEST,
            f"test: category {category!r} has {test_counts.get(category, 0)} rows, "
            f"needs >= {MIN_PER_CATEGORY_TEST} (evaluation.md §1)",
        )
    return {
        category: {
            "dev": dev_counts.get(category, 0),
            "test": test_counts.get(category, 0),
        }
        for category in CATEGORIES
    }


def validate_no_overlap(dev: Dataset, test: Dataset) -> None:
    """No question may appear in both splits (evaluation.md §1)."""
    dev_ids = {question.id for question in dev.questions}
    test_ids = {question.id for question in test.questions}
    overlap = sorted(dev_ids & test_ids)
    _require(
        not overlap,
        f"the two splits share question ids {overlap} (evaluation.md §1)",
    )
    dev_questions = {question.question.strip() for question in dev.questions}
    test_questions = {question.question.strip() for question in test.questions}
    shared = sorted(dev_questions & test_questions)
    _require(
        not shared,
        f"the two splits share question text {shared} (evaluation.md §1)",
    )


def validate_unanswerable_count(dev: Dataset, test: Dataset) -> int:
    """At least 5 unanswerable rows across the whole dataset (§8)."""
    count = sum(
        1
        for question in list(dev.questions) + list(test.questions)
        if not question.answerable
    )
    _require(
        count >= MIN_UNANSWERABLE,
        f"the dataset has {count} unanswerable rows, needs >= {MIN_UNANSWERABLE} "
        "(evaluation.md §8)",
    )
    return count


def validate_against_corpus(
    dataset: Dataset,
    node_ids: set[str],
    document_slugs: set[str],
) -> None:
    """D1 invariants 2 and 3, enforced LOUDLY.

    This is the check the Day-6 brief calls "the most dangerous item": a
    gold node id that does not resolve yields ``Recall@5 = 0`` for every
    question while the run completes and nothing fails. And a
    ``question.document`` that resolves to zero documents (or to two)
    means the runner is asking a different question than the dataset says.

    * ``node_ids`` — every node id in the ingested corpus.
    * ``document_slugs`` — every document slug in the ingested corpus.
    """
    for question in dataset.questions:
        _require(
            question.document in document_slugs,
            f"{dataset.name} ({question.id}): document slug {question.document!r} "
            "does not resolve to exactly one ingested document (D1 invariant 3)",
        )
        for gold in question.gold_nodes:
            _require(
                gold in node_ids,
                f"{dataset.name} ({question.id}): gold node {gold!r} does not "
                "resolve in the ingested corpus (D1 invariant 2)",
            )


def content_tokens(text: str) -> list[str]:
    """The tokens of a question that carry its CONTENT.

    A content token is an alphanumeric run that is not pure punctuation and
    not a bare number. The bare numbers are excluded because an
    unanswerable question that names a provision is not out-of-domain: the
    corpus has that provision, and the identifier channel would find it.

    The check that matters is :func:`check_unanswerable_absence`, and it
    uses the SAME tokenization the corpus scan uses, so a token is absent
    only when the corpus genuinely does not contain it in any form.
    """
    tokens: list[str] = []
    for token in _TOKEN_RE.findall(text.lower()):
        if token.isdigit():
            continue
        tokens.append(token)
    return tokens


def check_unanswerable_absence(
    dataset: Dataset,
    corpus_vocabulary: set[str],
) -> list[str]:
    """Every content token of every unanswerable row must be ABSENT.

    ``corpus_vocabulary`` is the set of tokens the corpus contains, in the
    corpus's own tokenization. A token that is present makes the row
    answerable-by-vocabulary, and the abstention metric then measures the
    dataset rather than the system.

    Returns the offending question ids. The caller decides whether to
    raise; the function itself does not raise, so a caller can report the
    whole list at once.
    """
    offenders: list[str] = []
    for question in dataset.questions:
        if question.answerable:
            continue
        for token in content_tokens(question.question):
            if token in corpus_vocabulary:
                offenders.append(question.id)
                break
    return offenders


__all__ = [
    "CATEGORIES",
    "DEV_SIZE",
    "DOCUMENT_SLUG",
    "Dataset",
    "DatasetError",
    "MIN_PER_CATEGORY_DEV",
    "MIN_PER_CATEGORY_TEST",
    "MIN_UNANSWERABLE",
    "Question",
    "SPLIT_SEED",
    "TEST_SIZE",
    "TOTAL_SIZE",
    "check_unanswerable_absence",
    "content_tokens",
    "load_dataset",
    "load_split",
    "parse_dataset",
    "parse_question",
    "validate_against_corpus",
    "validate_no_overlap",
    "validate_split",
    "validate_stratification",
    "validate_unanswerable_count",
]
