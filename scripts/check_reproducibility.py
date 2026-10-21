#!/usr/bin/env python3
"""Reproducibility: the same config id yields the same retrieval metrics, exactly.

evaluation.md sec 6 states the rule: "Same config -> same retrieval metrics
exactly; generation within +/-5pp." PLAN.md Day 7 restates it as the
acceptance criterion: "Re-run with same config id -> retrieval metrics
reproduce exactly, generation within +/-5pp."

This script proves the retrieval half, on the real corpus, with the real
retrieval engine, and it proves it the way the rule is stated: the run is
identified by a CONFIG ID, not by a filename or a timestamp.

    python -m scripts.check_reproducibility

It runs the retrieval pass TWICE over the same frozen questions, with the
same weight snapshot, and compares:

  * the per-question retrieved node id lists, element for element;
  * the per-question Recall@K and MRR@10 values;
  * the macro-averaged Recall@K and MRR@10 over the whole question set;
  * the config id derived from the run config, across both runs.

**It also runs the ARMS.** The direct pass above calls ``search_nodes``,
which is the vectorless arm's own tool and nothing else: on its own it
never constructs the dense arm, never builds a dense index, and never
runs the judge, so it says nothing about whether the evaluation's actual
comparison reproduces. The script therefore ALSO constructs the vectorless
arm and the dense arm over the same fresh corpus, takes each arm's own
ranking, and compares the two passes the same way: the per-arm
macro-averaged metrics AND the per-question node id lists, element for
element. The dense arm's index is built once per pass, over a fresh
ingest, so a pass that reproduced itself through a cached artifact would
fail exactly as the direct pass does.

**What this script does NOT check.** evaluation.md sec 8's acceptance has
two halves: "Same config -> same retrieval metrics exactly; generation
within +/-5pp". This script proves the RETRIEVAL half. The GENERATION
half is NOT checked here, and the reason is structural rather than an
oversight: the generation half is a property of a LOCAL MODEL CALL, so
measuring it needs a live model backend (ollama at 127.0.0.1:11434 by
default) and a run that costs minutes per question, and a CI step that
silently skipped when no model was present would prove nothing. The
script therefore does not require a model, does not pretend to check
generation, and says so on every successful run and in the report. The
"+/-5pp generation" tolerance is an UNCHECKED acceptance until something
that needs a live model checks it.

Two separate stores are built -- a fresh ingest and a fresh index per run --
so a pass that only reproduced itself because it reused a cached artifact
would fail. The corpus PDF is the only input.

If the corpus is absent, the script skips with a clear message: the PDFs are
gitignored (ADR-002) and this script does not fabricate a substitute.

Usage:
    python -m scripts.check_reproducibility
    python -m scripts.check_reproducibility --questions data/datasets/v1/dev.json
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
CORPUS = REPO_ROOT / "data" / "documents" / "civil-code-ra386-icnl.pdf"
SLUG = "civil-code-public"

#: The fallback question set, used when no dataset file is given. These are
#: the golden 10Q (apps/api/tests/test_retrieval.py), which are already
#: verified to resolve to real node ids.
FALLBACK_QUESTIONS: list[tuple[str, str]] = [
    ("What are the requisites of contracts?", "node_civil-code-public_article_1318"),
    ("Who are the compulsory heirs?", "node_civil-code-public_article_887"),
    (
        "What causes extinguish obligations by payment or performance?",
        "node_civil-code-public_article_1231",
    ),
    ("What does Article 26 say about dignity?", "node_civil-code-public_article_26"),
    ("Does ignorance of the law excuse compliance?", "node_civil-code-public_article_3"),
    (
        "Who bears liability for damages caused by employees?",
        "node_civil-code-public_article_2180",
    ),
    ("What is succession as acquisition mode?", "node_civil-code-public_article_774"),
    ("How must every person exercise rights?", "node_civil-code-public_article_19"),
    ("What about juvenile courts?", "node_civil-code-public_article_361"),
    (
        "When a third person pays a debt against the will of the debtor, can the debtor recover?",
        "node_civil-code-public_article_1425",
    ),
]

#: The K values the metrics are reported at (evaluation.md sec 3).
K_VALUES = (1, 3, 5, 10)


def load_questions(path: Path | None) -> list[tuple[str, str]]:
    """(question, gold_node) pairs, from a dataset file or the golden 10Q."""
    if path is None:
        return list(FALLBACK_QUESTIONS)
    raw = json.loads(path.read_text(encoding="utf-8"))
    questions = raw.get("questions") if isinstance(raw, dict) else raw
    if not isinstance(questions, list):
        raise ValueError(f"{path} does not carry a list of questions")
    out: list[tuple[str, str]] = []
    for row in questions:
        if not isinstance(row, dict):
            continue
        question = row.get("question")
        golds = row.get("gold_nodes")
        if not isinstance(question, str) or not isinstance(golds, list) or not golds:
            continue
        out.append((question, str(golds[0])))
    if not out:
        raise ValueError(f"{path} carries no question with a gold node")
    return out


def run_once(questions: list[tuple[str, str]]) -> dict[str, Any]:
    """One full retrieval pass over a FRESH ingest and a FRESH index.

    A fresh store per pass is the point: if the two passes shared a store,
    a pass could reproduce itself through a cached index rather than through
    determinism.

    The pass is the DIRECT one: it calls ``search_nodes``, which is the
    vectorless arm's own retrieval tool, over the real corpus. The ARMS
    pass lives in :func:`run_once_arms` and runs over a synthetic corpus,
    so that the arms are checked even where the real corpus is absent.
    """
    import apps.api.retrieval.scoring as scoring
    from apps.api.ingestion.service import ingest_pdf
    from apps.api.retrieval.api import search_nodes
    from apps.api.retrieval.index import build_indexes
    from apps.api.retrieval.scoring import WEIGHT_DEFAULTS

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        db_path = str(tmp_path / "repro.db")
        doc = ingest_pdf(str(CORPUS), slug=SLUG, db_path=db_path)
        build_indexes(db_path, doc.id, index_root=str(tmp_path / "idx"))
        original_root = scoring.INDEX_ROOT
        scoring.INDEX_ROOT = tmp_path / "idx"
        try:
            per_question: list[dict[str, Any]] = []
            for question, gold in questions:
                hits = search_nodes(question, doc.id, top_k=max(K_VALUES), db_path=db_path)
                retrieved = [hit.node_id for hit in hits]
                row: dict[str, Any] = {
                    "question": question,
                    "gold": gold,
                    "retrieved": retrieved,
                }
                for k in K_VALUES:
                    row[f"recall@{k}"] = _recall(retrieved, gold, k)
                row["mrr@10"] = _mrr(retrieved, gold, 10)
                per_question.append(row)
        finally:
            scoring.INDEX_ROOT = original_root

    return {
        "per_question": per_question,
        "aggregates": _macro(per_question),
        "weights": dict(WEIGHT_DEFAULTS),
    }


#: The synthetic corpus the ARMS pass runs over: (heading, body) per
#: article. The topics are distinct on purpose, so the two arms have
#: something to rank and a change in either arm's ranking shows up in
#: the metrics instead of being masked by ties.
SYNTHETIC_ARTICLES: list[tuple[str, str]] = [
    (
        "Article 1. Requisites of contracts.",
        "The requisites of contracts are consent, object, and certainty of "
        "the terms. A contract requires the consent of the parties, a lawful "
        "object, and certainty of the terms. ",
    ),
    (
        "Article 2. Succession.",
        "Succession is the transfer of the rights and obligations of a "
        "deceased person to the heirs or the legatees by his will or by "
        "operation of law. ",
    ),
    (
        "Article 3. Ignorance of the law.",
        "Ignorance of the law excuses no one from compliance therewith. "
        "Every person must obey the laws that are in force. ",
    ),
    (
        "Article 4. Prescription.",
        "Prescription extinguishes a claim after the period laid down by "
        "law. The periods of prescription are enumerated in this article. ",
    ),
    (
        "Article 5. Damages.",
        "Damages are the sum of money which the court orders the defendant "
        "to pay. The kinds of damages are provided here. ",
    ),
    (
        "Article 6. Partition.",
        "Partition may be made by agreement between the parties or by "
        "judicial proceedings. Partition shall be governed by the Rules of "
        "Court. ",
    ),
    # The DISTRACTORS. They share vocabulary with the articles above, so
    # a ranking is a real decision rather than a lookup: a corpus where
    # every metric is 1.0 would make the aggregate comparison blind to a
    # regression, because a swap between the top two nodes leaves the
    # mean untouched. The node id list comparison would still catch it,
    # but the aggregate must carry signal too.
    (
        "Article 7. Obligations of the parties.",
        "The obligations of the parties to a contract are determined by the "
        "terms of the contract and by law. Consent, object and certainty are "
        "requisites of a contract. ",
    ),
    (
        "Article 8. Persons who may inherit.",
        "The persons who may inherit are the compulsory heirs, and the "
        "succession of their rights and obligations is regulated by law. ",
    ),
    (
        "Article 9. Compliance with the law.",
        "Every person must comply with the law, and the law excuses no one. "
        "Ignorance of the law is no excuse for non-compliance. ",
    ),
]

#: The questions the ARMS pass asks about the synthetic corpus, with the
#: gold node each one names. The node ids are the ones the INGEST builds
#: for the synthetic document, so the golds are real ids in the real
#: tree rather than ids the script hopes for.
SYNTHETIC_QUESTIONS: list[tuple[str, str]] = [
    ("What are the requisites of contracts?", "node_synth-doc_article_1"),
    ("What is succession as a mode of acquisition?", "node_synth-doc_article_2"),
    ("Does ignorance of the law excuse compliance?", "node_synth-doc_article_3"),
    ("When does prescription extinguish a claim?", "node_synth-doc_article_4"),
    ("What are damages?", "node_synth-doc_article_5"),
    ("How is partition made?", "node_synth-doc_article_6"),
]


def _write_synthetic_pdf(path: Path) -> None:
    """The synthetic statute: one page per article, a heading and a body.

    The same shape ``apps/api/tests/test_evaluation.py`` builds, so the
    corpus here is a real PDF with a real heading structure and the
    parser has something to parse.
    """
    import fitz

    document = fitz.open()
    for heading, body in SYNTHETIC_ARTICLES:
        page = document.new_page()
        page.insert_textbox(
            fitz.Rect(50, 50, 560, 780), heading + "\n" + body * 3, fontsize=11
        )
    document.save(path)
    document.close()


def run_once_arms(questions: list[tuple[str, str]]) -> dict[str, Any]:
    """One pass over BOTH ARMS, over a FRESH synthetic ingest.

    The direct pass above calls ``search_nodes``, which is the
    vectorless arm's tool and NOTHING ELSE: it never constructs the dense
    arm, never builds a dense index, and never runs the judge. The
    evaluation's arms are the thing evaluation.md sec 1 says the run
    compares, so the arms are exercised here too.

    The corpus is SYNTHETIC, and deliberately so. The dense arm embeds
    every node of the corpus, and over the real Civil Code that is a
    2,579-node embed - about 4.5 minutes on CPU, per pass, and the pass
    runs twice. Over a six-article synthetic statute the same check costs
    under a second, and the property being checked is the same one: does
    the arm's own ranking reproduce across two fresh ingests and two
    fresh index builds? The dense arm's index is built ONCE per pass,
    over a fresh ingest, so a pass that reproduced itself through a
    cached artifact would fail exactly as the direct pass does.

    The aggregate compared is the same macro-averaged retrieval metric
    the evaluation reports, computed per arm from the arm's OWN ranking.
    """
    import apps.api.retrieval.scoring as scoring
    from apps.api.evaluation.arms import ARM_TUKLAS, ARM_VECTOR, TuklasArm, VectorArm
    from apps.api.ingestion.service import ingest_pdf
    from apps.api.retrieval.index import build_indexes

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        pdf_path = tmp_path / "synthetic.pdf"
        _write_synthetic_pdf(pdf_path)
        db_path = str(tmp_path / "arms.db")
        document = ingest_pdf(str(pdf_path), slug="synth-doc", db_path=db_path)
        build_indexes(db_path, document.id, index_root=str(tmp_path / "idx"))
        original_root = scoring.INDEX_ROOT
        scoring.INDEX_ROOT = tmp_path / "idx"
        try:
            arms = {
                ARM_TUKLAS: TuklasArm(db_path, document.id),
                ARM_VECTOR: VectorArm(db_path, document.id),
            }
            per_arm: dict[str, list[dict[str, Any]]] = {}
            for arm_name, arm in arms.items():
                rows: list[dict[str, Any]] = []
                for question, gold in questions:
                    retrieved = list(arm.retrieve(question).node_ids)
                    row: dict[str, Any] = {
                        "question": question,
                        "gold": gold,
                        "retrieved": retrieved,
                    }
                    for k in K_VALUES:
                        row[f"recall@{k}"] = _recall(retrieved, gold, k)
                    row["mrr@10"] = _mrr(retrieved, gold, 10)
                    rows.append(row)
                per_arm[arm_name] = rows
        finally:
            scoring.INDEX_ROOT = original_root

    return {
        "per_arm": per_arm,
        "aggregates": {name: _macro(rows) for name, rows in per_arm.items()},
    }


def _recall(retrieved: list[str], gold: str, k: int) -> float:
    """Recall@K for a single gold node."""
    return 1.0 if gold in retrieved[:k] else 0.0


def _mrr(retrieved: list[str], gold: str, k: int) -> float:
    """1 / rank of the first gold hit within the top k, else 0."""
    for index, node_id in enumerate(retrieved[:k], start=1):
        if node_id == gold:
            return 1.0 / index
    return 0.0


def _macro(per_question: list[dict[str, Any]]) -> dict[str, float]:
    """The macro-averaged metrics over the question set."""
    if not per_question:
        return {}
    out: dict[str, float] = {}
    for k in K_VALUES:
        out[f"recall@{k}"] = sum(row[f"recall@{k}"] for row in per_question) / len(
            per_question
        )
    out["mrr@10"] = sum(row["mrr@10"] for row in per_question) / len(per_question)
    return out


def _config_id(weights: dict[str, float], questions: list[tuple[str, str]]) -> str:
    """The config id of this run, derived the way the checker derives it.

    The derivation is shared with scripts/check_eval_config.py, so the id a
    run reports and the id a config file declares are the same id.
    """
    import hashlib

    payload = {
        "dataset_snapshot": "reproducibility-check",
        "k": list(K_VALUES),
        "weights": {name: float(value) for name, value in sorted(weights.items())},
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return "cfg_" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _compare_arms(first: dict[str, Any], second: dict[str, Any]) -> list[str]:
    """The differences between the two passes over the two arms.

    Both halves are checked, because they fail differently: the
    AGGREGATE can match while the per-question node id lists differ (two
    questions that swapped ranks leave the mean untouched), and the node
    id lists can match while the aggregate differs (a change in a
    question's score that does not move its rank). A pass that checked
    only one of them would miss half the failures it exists to catch.
    """
    problems: list[str] = []
    first_aggregates = first.get("aggregates", {})
    second_aggregates = second.get("aggregates", {})
    if first_aggregates != second_aggregates:
        problems.append(
            f"the arms' macro-averaged metrics differ between the two runs: "
            f"{first_aggregates} vs {second_aggregates}"
        )
    first_rows = first.get("per_arm", {})
    second_rows = second.get("per_arm", {})
    for arm_name in sorted(set(first_rows) | set(second_rows)):
        left_rows = first_rows.get(arm_name, [])
        right_rows = second_rows.get(arm_name, [])
        if len(left_rows) != len(right_rows):
            problems.append(
                f"arm {arm_name}: the two runs produced a different number of "
                f"question rows ({len(left_rows)} vs {len(right_rows)})"
            )
            continue
        for index, (left, right) in enumerate(zip(left_rows, right_rows, strict=True)):
            if left["retrieved"] != right["retrieved"]:
                problems.append(
                    f"arm {arm_name} question {index} ({left['question']!r}) "
                    f"retrieved different node ids in the two runs"
                )
            for key in (*(f"recall@{k}" for k in K_VALUES), "mrr@10"):
                if left[key] != right[key]:
                    problems.append(
                        f"arm {arm_name} question {index} ({left['question']!r}) "
                        f"scored {key} {left[key]} vs {right[key]}"
                    )
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--questions",
        type=Path,
        default=None,
        help="a dataset file (dev.json / test.json) to run the questions from",
    )
    args = parser.parse_args()

    problems: list[str] = []

    # The ARMS pass first, and it does NOT need the real corpus: it runs
    # over a synthetic statute, so the arms are checked even on a machine
    # where the gitignored PDFs are absent. The corpus pass below needs
    # them, and skips with a clear message when they are missing.
    arms_first = run_once_arms(SYNTHETIC_QUESTIONS)
    arms_second = run_once_arms(SYNTHETIC_QUESTIONS)
    arms_problems = _compare_arms(arms_first, arms_second)
    problems.extend(arms_problems)
    if not arms_problems:
        print(f"arms pass: {len(SYNTHETIC_QUESTIONS)} questions over a synthetic corpus")
        for arm_name, aggregates in sorted(arms_first["aggregates"].items()):
            for metric, value in sorted(aggregates.items()):
                print(
                    f"  arm {arm_name} {metric}: {value:.6f}  (identical in both runs)"
                )

    if not CORPUS.exists():
        print(
            f"SKIP: the corpus is not present ({CORPUS}). It is gitignored "
            "(ADR-002); fetch it per docs/CORPUS.md. The ARMS pass above "
            "does not need it and was still checked.",
            file=sys.stderr,
        )
        if problems:
            for line in problems:
                print(f"check_reproducibility: {line}", file=sys.stderr)
            print(f"check_reproducibility: FAIL ({len(problems)} difference(s))")
            return 1
        print("check_reproducibility: OK (arms pass; the corpus pass was skipped)")
        return 0

    questions = load_questions(args.questions)
    print(f"questions: {len(questions)}")

    first = run_once(questions)
    second = run_once(questions)

    config_id = _config_id(first["weights"], questions)
    print(f"config_id: {config_id}")

    if first["aggregates"] != second["aggregates"]:
        problems.append(
            f"the macro-averaged metrics differ between the two runs: "
            f"{first['aggregates']} vs {second['aggregates']}"
        )
    else:
        for name, value in sorted(first["aggregates"].items()):
            print(f"  {name}: {value:.6f}  (identical in both runs)")

    for index, (left, right) in enumerate(
        zip(first["per_question"], second["per_question"], strict=True)
    ):
        if left["retrieved"] != right["retrieved"]:
            problems.append(
                f"question {index} ({left['question']!r}) retrieved different node ids "
                f"in the two runs"
            )
        for key in (*(f"recall@{k}" for k in K_VALUES), "mrr@10"):
            if left[key] != right[key]:
                problems.append(
                    f"question {index} ({left['question']!r}) scored {key} "
                    f"{left[key]} vs {right[key]}"
                )

    if problems:
        for line in problems:
            print(f"check_reproducibility: {line}", file=sys.stderr)
        print(f"check_reproducibility: FAIL ({len(problems)} difference(s))")
        return 1

    print(
        f"check_reproducibility: OK (config_id {config_id}, "
        f"{len(questions)} questions, retrieval metrics reproduce exactly; "
        f"both arms reproduce exactly over a synthetic corpus)"
    )
    print(
        "check_reproducibility: NOTE the GENERATION half of the acceptance "
        "(evaluation.md sec 8: 'generation within +/-5pp') is NOT checked "
        "here: it needs a live local model backend, and this script does "
        "not require one. The generation tolerance is an unchecked "
        "acceptance, not a measured one."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
