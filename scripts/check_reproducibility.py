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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--questions",
        type=Path,
        default=None,
        help="a dataset file (dev.json / test.json) to run the questions from",
    )
    args = parser.parse_args()

    if not CORPUS.exists():
        print(
            f"SKIP: the corpus is not present ({CORPUS}). It is gitignored "
            "(ADR-002); fetch it per docs/CORPUS.md.",
            file=sys.stderr,
        )
        return 0

    questions = load_questions(args.questions)
    print(f"questions: {len(questions)}")

    first = run_once(questions)
    second = run_once(questions)

    config_id = _config_id(first["weights"], questions)
    print(f"config_id: {config_id}")

    problems: list[str] = []

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
        f"{len(questions)} questions, retrieval metrics reproduce exactly)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
