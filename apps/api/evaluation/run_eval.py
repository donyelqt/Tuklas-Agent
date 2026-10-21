#!/usr/bin/env python3
"""Run one evaluation and print its report.

    python -m apps.api.evaluation.run_eval --config <run-config.json> \
        --snapshot v1-test [--db data/tuklas.sqlite] [--report out.md]

The runner is the SAME one the API route uses; this script is the command
line surface over it, and it exists so a run is one command away (the
"one command runs both arms on the same snapshot" of evaluation.md §8).

The script:

* loads the run config and VALIDATES it (a config whose arms disagree on
  the model is an invalid run, and the script refuses it before it spends
  a token);
* loads the dataset snapshot and validates it against the ingested corpus
  (every gold must resolve; every unanswerable row's tokens must be
  absent);
* runs both arms;
* writes the report to stdout, and optionally to a file.

It exits non-zero when the run itself failed, and prints the reason.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, cast

from apps.api.evaluation import config as eval_config
from apps.api.evaluation import dataset as dataset_module
from apps.api.evaluation import report as eval_report
from apps.api.evaluation import runner as eval_runner
from apps.api.evaluation.arms import ARM_TUKLAS, ARM_VECTOR

#: The dataset snapshots this script can run.
SNAPSHOTS: dict[str, str] = {
    "v1-dev": "dev",
    "v1-test": "test",
}

#: The repository root, from this file.
REPO_ROOT = Path(__file__).resolve().parents[3]

#: The dataset directory, relative to the repo root.
DATASET_DIR = REPO_ROOT / "data" / "datasets" / "v1"


def _fail(message: str) -> int:
    """Print the message to stderr and return the exit code."""
    print(message, file=sys.stderr)
    return 1


def _corpus_state(db_path: str) -> dict[str, Any]:
    """The ingested corpus's node ids and slugs, for the dataset's checks."""
    from apps.api.ingestion import db as ingestion_db

    with ingestion_db.connect(db_path) as conn:
        slugs = {str(row["slug"]) for row in conn.execute("SELECT slug FROM documents")}
        node_ids = {str(row["id"]) for row in conn.execute("SELECT id FROM nodes")}
    return {"slugs": slugs, "node_ids": node_ids}


def _document_id(db_path: str, slug: str) -> str:
    """The id of the ingested document with this slug.

    D1 invariant 3: the slug must resolve to EXACTLY ONE document. A
    slug that resolves to none, or to two, is an error the runner
    reports rather than a document it guesses.
    """
    from apps.api.ingestion import db as ingestion_db

    with ingestion_db.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT id FROM documents WHERE slug = ?", (slug,)
        ).fetchall()
    if len(rows) != 1:
        raise ValueError(
            f"the document slug {slug!r} resolves to {len(rows)} ingested documents; "
            "it must resolve to exactly one"
        )
    return str(rows[0]["id"])


def _corpus_vocabulary(db_path: str, document_id: str) -> set[str]:
    """The document's folded token vocabulary, for the unanswerable check.

    The vocabulary is the DOCUMENT's, not the whole store's: retrieval
    scores a document's nodes against the query, so a word that occurs
    only in a different document cannot be matched by this run. The
    dataset's questions all name the Civil Code, and that is the
    document this check is about.
    """
    return _vocabulary_of(db_path, "document_id = ?", (document_id,))


def _store_vocabulary(db_path: str) -> set[str]:
    """The WHOLE store's folded token vocabulary.

    The check is deliberately stricter than the run needs: a word that
    occurs in ANY ingested document would make the row answerable if the
    dataset were ever pointed at that document. A stricter check is
    free; a weaker one is a dataset that breaks silently.
    """
    return _vocabulary_of(db_path, "1 = 1", ())


def _vocabulary_of(db_path: str, where: str, params: tuple[Any, ...]) -> set[str]:
    """The folded token vocabulary of the nodes matching a WHERE clause."""
    from apps.api.evaluation.generate_dataset import tokens_of
    from apps.api.ingestion import db as ingestion_db

    vocabulary: set[str] = set()
    with ingestion_db.connect(db_path) as conn:
        rows = conn.execute(f"SELECT title, text FROM nodes WHERE {where}", params).fetchall()
    for row in rows:
        vocabulary |= tokens_of(f"{row['title']} {row['text']}")
    return vocabulary


def _node_count(db_path: str, document: str) -> int:
    """How many nodes the document has.

    The number the dense arm embeds: every node of the document, in tree
    order.
    """
    from apps.api.ingestion import db as ingestion_db

    with ingestion_db.connect(db_path) as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM nodes WHERE document_id ="
            " (SELECT id FROM documents WHERE id = ? OR slug = ?)",
            (document, document),
        ).fetchone()
    return int(row[0]) if row else 0


def _lex_corpus_size(db_path: str, document: str) -> int:
    """How many nodes are in the per-document lexical corpus.

    The rule is the ONE definition the primary path owns
    (``apps.api.retrieval.index.in_lex_corpus``): the leaf-node bodies
    plus the section-intro nodes. The harness imports the rule rather
    than restating it, so the size the report publishes and the corpus
    the BM25 index actually holds cannot drift apart.
    """
    from apps.api.ingestion import db as ingestion_db
    from apps.api.retrieval.index import in_lex_corpus

    with ingestion_db.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT type, text FROM nodes WHERE document_id ="
            " (SELECT id FROM documents WHERE id = ? OR slug = ?)",
            (document, document),
        ).fetchall()
    return sum(1 for row in rows if in_lex_corpus(str(row["type"]), str(row["text"])))


def main(argv: list[str] | None = None) -> int:
    """Run one evaluation. Returns the process exit code."""
    parser = argparse.ArgumentParser(description="Run one Tuklas evaluation.")
    parser.add_argument("--config", required=True, help="the run config JSON file")
    parser.add_argument(
        "--snapshot",
        required=True,
        choices=sorted(SNAPSHOTS),
        help="the dataset snapshot to run",
    )
    parser.add_argument(
        "--db",
        default=str(REPO_ROOT / "data" / "tuklas.sqlite"),
        help="the SQLite store the corpus is ingested into",
    )
    parser.add_argument(
        "--index-root",
        default=str(REPO_ROOT / "data" / "indexes"),
        help="the per-document retrieval index root",
    )
    parser.add_argument("--report", default="", help="write the report to this file too")
    parser.add_argument("--json", default="", help="write the aggregates as JSON here")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    config_path = Path(args.config)
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return _fail(f"the run config is missing: {config_path.as_posix()}")
    except json.JSONDecodeError as err:
        return _fail(f"the run config is invalid JSON: {err}")

    # The config's own snapshot wins over the flag ONLY when they agree;
    # a config that names one snapshot and a flag that names another is
    # a contradiction, and a run that silently picked one of them would
    # report numbers for a split the reader did not ask for.
    if payload.get("dataset_snapshot") != args.snapshot:
        return _fail(
            f"the run config names snapshot {payload.get('dataset_snapshot')!r} but "
            f"--snapshot names {args.snapshot!r}"
        )

    violations, _config_id = eval_config.check_run_config(payload)
    if violations:
        return _fail(
            "the run config is invalid:\n  - " + "\n  - ".join(violations)
        )
    try:
        run_config = eval_config.load_run_config(payload)
    except eval_config.ConfigError as err:
        return _fail(str(err))

    split = SNAPSHOTS[args.snapshot]
    dataset_path = DATASET_DIR / f"{split}.json"
    try:
        dataset = dataset_module.load_dataset(dataset_path)
    except dataset_module.DatasetError as err:
        return _fail(f"the dataset snapshot is invalid: {err}")

    expected = dataset_module.DEV_SIZE if split == "dev" else dataset_module.TEST_SIZE
    try:
        dataset_module.validate_split(dataset, expected_size=expected)
    except dataset_module.DatasetError as err:
        return _fail(f"the dataset snapshot is invalid: {err}")

    try:
        state = _corpus_state(args.db)
    except Exception as err:
        return _fail(f"the store could not be read: {err}")

    try:
        _document_id(args.db, dataset.questions[0].document)
    except Exception as err:
        return _fail(f"the document could not be resolved: {err}")

    if dataset.questions:
        for question in dataset.questions:
            if question.document not in state["slugs"]:
                return _fail(
                    f"the dataset's document {question.document!r} is not ingested in "
                    f"{args.db}; ingest the corpus before running an evaluation"
                )
    try:
        dataset_module.validate_against_corpus(dataset, state["node_ids"], state["slugs"])
    except dataset_module.DatasetError as err:
        return _fail(f"the dataset does not resolve against the corpus: {err}")

    vocabulary = _store_vocabulary(args.db)
    offenders = dataset_module.check_unanswerable_absence(dataset, vocabulary)
    if offenders:
        return _fail(
            "the dataset's unanswerable rows share vocabulary with the corpus "
            f"(questions {offenders}); the abstention metric would measure the "
            "dataset rather than the system"
        )

    runner = eval_runner.Runner(
        args.db,
        dataset.questions[0].document,
        run_config,
        index_root=Path(args.index_root),
    )
    try:
        outcome = runner.run(dataset)
    except Exception as err:
        logging.exception("the evaluation runner failed")
        return _fail(f"the evaluation run failed: {err}")

    dense_description: dict[str, Any] | None = None
    vectorless_description: dict[str, Any] | None = None
    try:
        arms = runner.build_arms()
        dense_description = cast(dict[str, Any], arms[ARM_VECTOR].describe())
        vectorless_description = cast(dict[str, Any], arms[ARM_TUKLAS].describe())
    except Exception:
        dense_description = None
        vectorless_description = None
    # The SCOPE of each arm: how many nodes it ranks, how large the
    # per-document LEXICAL corpus is, and what surface each arm scores.
    # The arm's own describe() payload is the source, and it wins: the
    # vectorless arm ranks the document's whole node set, while the dense
    # arm embeds the lexical node set, and only the arm knows which.
    # These are therefore fallbacks for an arm that does not describe
    # itself, and they describe the DOCUMENT's node count and the
    # DOCUMENT's lexical corpus, not a property of either arm. The
    # vectorless arm's lexical corpus is the leaf bodies plus the section
    # intros, so it is a strict subset of the document's nodes, and a node
    # outside it carries lex = 0 by construction rather than by scoring
    # low.
    if vectorless_description is not None:
        vectorless_description.setdefault(
            "n_lex_nodes", _lex_corpus_size(args.db, dataset.questions[0].document)
        )
        vectorless_description.setdefault("n_nodes", _node_count(args.db, dataset.questions[0].document))
    if dense_description is not None:
        dense_description.setdefault("n_nodes", _node_count(args.db, dataset.questions[0].document))

    # The gold overlap between the splits is a property of the DATASET, not
    # of the run, and the report needs it: the dev split is the tuning
    # split, so a provision both splits gold is a provision whose answer key
    # the tuning has already seen.
    dataset_overlap = None
    try:
        dev_dataset = dataset_module.load_dataset(str(DATASET_DIR / "dev.json"))
    except Exception:
        dev_dataset = None
    if dev_dataset is not None:
        try:
            dataset_overlap = dataset_module.gold_overlap(dev_dataset, dataset)
        except Exception:
            dataset_overlap = None

    text = eval_report.render_report(
        evaluation_id=outcome["evaluation_id"],
        config=outcome["config"],
        dataset_name=dataset.name,
        dataset_snapshot=dataset.snapshot_id,
        split_seed=dataset.seed,
        n_questions=int(outcome["aggregates"].get("n_questions", 0)),
        aggregates=outcome["aggregates"],
        spot_check=outcome["aggregates"].get("spot_check"),
        dense_arm_description=dense_description,
        vectorless_arm_description=vectorless_description,
        dataset_overlap=dataset_overlap,
    )
    print(text)
    if args.report:
        Path(args.report).write_text(text + "\n", encoding="utf-8")
    if args.json:
        Path(args.json).write_text(
            json.dumps(outcome["aggregates"], ensure_ascii=False, indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
