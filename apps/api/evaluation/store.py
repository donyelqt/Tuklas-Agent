"""The eval store: the ``evaluation_runs`` and ``retrieval_runs`` rows.

evaluation.md §6 fixes the shape of what is written:

* ``evaluation_runs`` — the RUN-level row: the evaluation id, the dataset
  snapshot id, the code version, the weights, the normalization id, the
  fuzzy threshold, the bonus flags, the stoplist version, the model and
  backend, temperature 0, the seed, the judge prompt version, the margins
  and the K values.
* ``retrieval_runs`` — ONE row per question per arm, carrying the join
  keys ``(evaluation_id, question_id, arm)`` plus the node ids, the
  weights snapshot and the latency.

The join is the acceptance criterion ("``retrieval_runs`` +
``evaluation_runs`` rows joinable per question per arm"), so the join keys
are COLUMNS and not something a reader has to parse out of a JSON blob.

**The write is INCREMENTAL, and the run row lands LAST.** The runner
writes one ``retrieval_runs`` row per question per arm AS the question is
produced, and writes the ``evaluation_runs`` row - with the run's
metrics - at the END of the run. Two consequences follow, and both are
properties of the code rather than intentions:

* an INTERRUPTED run leaves its per-question rows in the store with NO
  run row, because the run row is written last. A reader who finds
  ``retrieval_runs`` rows for an ``evaluation_id`` that has no
  ``evaluation_runs`` row is looking at an interrupted run, and those
  rows carry no run-level metrics;
* a COMPLETED run has both, and the two join on
  ``(evaluation_id, question_id, arm)``.

This is the opposite of a single-transaction write, and the difference is
deliberate: a 72-question split over two arms runs for a long time, and a
write-at-the-end policy would lose every finished question to an
interruption. The cost of the incremental write is that the store can
hold the rows of a run that never completed, so a reader has to check for
the run row before treating a set of rows as a reportable run. The
function that reads a run back, :func:`load_evaluation`, is the join: it
returns ``None`` for an unknown evaluation, and a run whose run row is
absent is an unknown evaluation.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from apps.api.ingestion import db as ingestion_db


def new_evaluation_id() -> str:
    """A run id (api.md §1: ids are ``eval_<uuid8>``)."""
    return f"eval_{uuid.uuid4().hex[:8]}"


def _now() -> str:
    """The row's timestamp, in UTC ISO-8601."""
    return datetime.now(UTC).isoformat()


def _ensure_verification_column(conn: Any) -> None:
    """Add the ``verification`` column if the store does not have it yet.

    The frozen §8 DDL and the eval column migrations in
    ``apps/api/ingestion/db.py`` predate this slice, so a store produced
    by an earlier run has no such column. The column is nullable TEXT:
    a row produced before this slice stores NULL, which reads back as
    "no verification was recorded" rather than as an empty record.

    The ALTER is idempotent (it catches the "duplicate column" error),
    so a store that already has the column is untouched and a store that
    does not gets exactly one addition.
    """
    try:
        conn.execute("ALTER TABLE retrieval_runs ADD COLUMN verification TEXT")
    except sqlite3.OperationalError:
        return


@dataclass
class EvaluationRun:
    """The run-level record of one evaluation."""

    id: str
    dataset: str
    dataset_snapshot: str
    config: dict[str, Any]
    config_id: str
    code_version: str
    model: str
    backend: str
    temperature: float
    seed: int
    judge_prompt_version: str
    split_seed: int
    arms: list[str]
    k: list[int]
    status: str = "queued"
    metrics: dict[str, Any] = field(default_factory=dict)
    #: The retrieval INSTRUMENT that produced the run's retrieval metric:
    #: a versioned name from ``apps.api.evaluation.arms`` (currently
    #: ``answer-node-union-v1``, the metric computed over the node ids the
    #: ANSWER was produced from). ``None`` on a run recorded before the
    #: field existed - that absence is a FACT about the run, and it is
    #: never back-filled from what we now believe the run used.
    retrieval_instrument: str | None = None
    #: The lex path(s) the run's retrieval calls took, as the retrieval
    #: layer reported them (the per-document BM25 artifact's facts, or the
    #: FTS5 fallback). ``None`` means not recorded - a run before the
    #: field existed, or a run in which no search reported a path; an
    #: EMPTY list is a recorded run whose searches reported none. The two
    #: are kept distinct on purpose.
    lex_paths: list[str] | None = None

    def to_record(self) -> dict[str, Any]:
        """The run row as the store writes it."""
        return {
            "id": self.id,
            "dataset": self.dataset,
            "dataset_snapshot": self.dataset_snapshot,
            "config": self.config,
            "config_id": self.config_id,
            "code_version": self.code_version,
            "model": self.model,
            "backend": self.backend,
            "temperature": self.temperature,
            "seed": self.seed,
            "judge_prompt_version": self.judge_prompt_version,
            "split_seed": self.split_seed,
            "arms": self.arms,
            "k": self.k,
            "status": self.status,
            "metrics": self.metrics,
            "retrieval_instrument": self.retrieval_instrument,
            "lex_paths": self.lex_paths,
        }


@dataclass
class RetrievalRun:
    """The per-question, per-arm record of one evaluation."""

    evaluation_id: str
    question_id: str
    arm: str
    category: str
    answerable: bool
    retrieved_node_ids: list[str]
    gold_node_ids: list[str]
    weights: dict[str, float]
    normalization: str
    latency_ms: int
    rank_first_hit: int | None = None
    gold_hit: bool = False
    generation_latency_ms: int = 0
    total_latency_ms: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    tokens_source: str | None = None
    tool_iterations: int = 0
    insufficient: bool = False
    citations_valid: bool = False
    citation_node_ids: list[str] = field(default_factory=list)
    claims_matched: int = 0
    claims_total: int = 0
    faithfulness: float | None = None
    judge_verdicts: str = "[]"
    answer_markdown: str = ""
    judge_prompt_version: str = ""
    verification_record: dict[str, Any] | None = None


def write_evaluation(
    db_path: str,
    run: EvaluationRun,
    rows: list[RetrievalRun],
) -> str:
    """Write the run row and its per-question rows in ONE transaction.

    Returns the evaluation id.
    """
    with ingestion_db.connect(db_path) as conn:
        _ensure_verification_column(conn)
        conn.execute(
            "INSERT INTO evaluation_runs (id, dataset, dataset_snapshot, config,"
            " config_id, code_version, model, backend, temperature, seed,"
            " judge_prompt_version, split_seed, arms, k, status, metrics, created_at,"
            " retrieval_instrument, lex_paths)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run.id,
                run.dataset,
                run.dataset_snapshot,
                json.dumps(run.config, ensure_ascii=False, sort_keys=True),
                run.config_id,
                run.code_version,
                run.model,
                run.backend,
                run.temperature,
                run.seed,
                run.judge_prompt_version,
                run.split_seed,
                json.dumps(run.arms),
                json.dumps(run.k),
                run.status,
                json.dumps(run.metrics, ensure_ascii=False, sort_keys=True),
                _now(),
                # The measurement provenance. A run that does not carry it
                # (constructed without the fields, e.g. by an old code
                # path) stores NULL, which reads as NOT RECORDED - never
                # as an empty string or a path name, which would be a
                # claim about what the run used.
                run.retrieval_instrument,
                (
                    json.dumps(run.lex_paths, ensure_ascii=False)
                    if run.lex_paths is not None
                    else None
                ),
            ),
        )
        conn.executemany(
            "INSERT INTO retrieval_runs (id, query_id, algorithm, latency_ms,"
            " retrieved_nodes, weights, normalization, created_at, evaluation_id,"
            " question_id, arm, category, answerable, gold_nodes, rank_first_hit,"
            " gold_hit, generation_latency_ms, total_latency_ms, tokens_in,"
            " tokens_out, tokens_source, tool_iterations, insufficient,"
            " citations_valid, citation_node_ids, claims_matched, claims_total,"
            " faithfulness, judge_verdicts, answer_markdown, judge_prompt_version, verification)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,"
            " ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    f"rr_{uuid.uuid4().hex[:12]}",
                    row.question_id,
                    row.arm,
                    row.latency_ms,
                    json.dumps(row.retrieved_node_ids),
                    json.dumps(row.weights, sort_keys=True),
                    row.normalization,
                    _now(),
                    row.evaluation_id,
                    row.question_id,
                    row.arm,
                    row.category,
                    1 if row.answerable else 0,
                    json.dumps(row.gold_node_ids),
                    row.rank_first_hit,
                    1 if row.gold_hit else 0,
                    row.generation_latency_ms,
                    row.total_latency_ms,
                    row.tokens_in,
                    row.tokens_out,
                    row.tokens_source,
                    row.tool_iterations,
                    1 if row.insufficient else 0,
                    1 if row.citations_valid else 0,
                    json.dumps(row.citation_node_ids),
                    row.claims_matched,
                    row.claims_total,
                    row.faithfulness,
                    row.judge_verdicts,
                    row.answer_markdown,
                    row.judge_prompt_version,
                    (
                        json.dumps(row.verification_record, sort_keys=True)
                        if row.verification_record is not None
                        else None
                    ),
                )
                for row in rows
            ],
        )
    return run.id


def write_evaluation_rows(db_path: str, rows: list[RetrievalRun]) -> None:
    """Append per-question rows to an EXISTING run.

    A run over 72 questions and two arms takes long enough that an
    interrupted run would otherwise lose everything: the run row and its
    metrics are written at the END, so a run killed half way through
    leaves nothing at all. This appends each question's row as it is
    produced, so an interrupted run keeps the rows it did produce.

    The rows are still JOINABLE on ``(evaluation_id, question_id, arm)``,
    which is the key the metrics are computed from, so a resumed or a
    completed run is read the same way.
    """
    if not rows:
        return
    with ingestion_db.connect(db_path) as conn:
        _ensure_verification_column(conn)
        conn.executemany(
            "INSERT INTO retrieval_runs (id, query_id, algorithm, latency_ms,"
            " retrieved_nodes, weights, normalization, created_at, evaluation_id,"
            " question_id, arm, category, answerable, gold_nodes, rank_first_hit,"
            " gold_hit, generation_latency_ms, total_latency_ms, tokens_in,"
            " tokens_out, tokens_source, tool_iterations, insufficient,"
            " citations_valid, citation_node_ids, claims_matched, claims_total,"
            " faithfulness, judge_verdicts, answer_markdown, judge_prompt_version, verification)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,"
            " ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    f"rr_{uuid.uuid4().hex[:12]}",
                    row.question_id,
                    row.arm,
                    row.latency_ms,
                    json.dumps(row.retrieved_node_ids),
                    json.dumps(row.weights, sort_keys=True),
                    row.normalization,
                    _now(),
                    row.evaluation_id,
                    row.question_id,
                    row.arm,
                    row.category,
                    1 if row.answerable else 0,
                    json.dumps(row.gold_node_ids),
                    row.rank_first_hit,
                    1 if row.gold_hit else 0,
                    row.generation_latency_ms,
                    row.total_latency_ms,
                    row.tokens_in,
                    row.tokens_out,
                    row.tokens_source,
                    row.tool_iterations,
                    1 if row.insufficient else 0,
                    1 if row.citations_valid else 0,
                    json.dumps(row.citation_node_ids),
                    row.claims_matched,
                    row.claims_total,
                    row.faithfulness,
                    row.judge_verdicts,
                    row.answer_markdown,
                    row.judge_prompt_version,
                    (
                        json.dumps(row.verification_record, sort_keys=True)
                        if row.verification_record is not None
                        else None
                    ),
                )
                for row in rows
            ],
        )


def load_evaluation(db_path: str, evaluation_id: str) -> dict[str, Any] | None:
    """One evaluation, joined: the run row and its per-question rows.

    ``None`` when the evaluation is unknown — the caller maps that onto
    the 404 (api.md §4).
    """
    with ingestion_db.connect(db_path) as conn:
        run_row = conn.execute(
            "SELECT * FROM evaluation_runs WHERE id = ?", (evaluation_id,)
        ).fetchone()
        if run_row is None:
            return None
        question_rows = conn.execute(
            "SELECT * FROM retrieval_runs WHERE evaluation_id = ?"
            " ORDER BY question_id, arm",
            (evaluation_id,),
        ).fetchall()
    run = dict(run_row)
    for key in ("config", "metrics", "arms", "k", "lex_paths"):
        raw = run.get(key)
        if isinstance(raw, str):
            try:
                run[key] = json.loads(raw)
            except json.JSONDecodeError:
                run[key] = None
    # The measurement provenance: a run recorded BEFORE these fields
    # existed reads back as SQL NULL. That NULL is the fact "not
    # recorded" and is passed through AS NULL - it is never mapped to an
    # empty string, a path name, or any inferred value.
    questions = [_question_row(dict(row)) for row in question_rows]
    return {"run": run, "questions": questions}


def _question_row(row: dict[str, Any]) -> dict[str, Any]:
    """One ``retrieval_runs`` row as the QuestionRow shape (api.md §4)."""
    for key in ("retrieved_nodes", "gold_nodes", "citation_node_ids", "weights"):
        raw = row.get(key)
        if isinstance(raw, str):
            try:
                row[key] = json.loads(raw)
            except json.JSONDecodeError:
                row[key] = []
    # The v2 verification record is stored as JSON, or NULL for rows
    # produced before this column existed. A NULL reads back as None -
    # the fact "no verification was recorded" - and is never mapped to
    # an empty dict, which would be a claim that the layer ran.
    raw = row.get("verification")
    if isinstance(raw, str):
        try:
            row["verification"] = json.loads(raw)
        except json.JSONDecodeError:
            row["verification"] = None
    row["answerable"] = bool(row.get("answerable"))
    row["gold_hit"] = bool(row.get("gold_hit"))
    row["insufficient"] = bool(row.get("insufficient"))
    row["citations_valid"] = bool(row.get("citations_valid"))
    return row


__all__ = [
    "EvaluationRun",
    "RetrievalRun",
    "load_evaluation",
    "new_evaluation_id",
    "write_evaluation",
    "write_evaluation_rows",
]
