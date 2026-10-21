"""SQLite store — architecture.md §8 DDL (frozen), created by ingestion only.

One connection per call site; WAL for concurrent readers. The schema is exactly the
§8 DDL with the D1 `slug TEXT UNIQUE` column already recorded there. Retrieval reads
through its own access layer; agent/ never touches this module (CONSTRAINTS.md).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    slug TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    file_path TEXT NOT NULL,
    file_hash TEXT NOT NULL UNIQUE,
    page_count INTEGER NOT NULL,
    parse_confidence REAL NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS nodes (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES documents(id),
    parent_id TEXT,
    type TEXT NOT NULL,
    title TEXT NOT NULL,
    text TEXT NOT NULL,
    page_start INTEGER,
    page_end INTEGER,
    depth INTEGER NOT NULL,
    position INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_nodes_doc_parent ON nodes(document_id, parent_id);
CREATE INDEX IF NOT EXISTS idx_nodes_doc_type ON nodes(document_id, type);
CREATE TABLE IF NOT EXISTS references_ (
    id TEXT PRIMARY KEY,
    source_node_id TEXT NOT NULL REFERENCES nodes(id),
    target_node_id TEXT REFERENCES nodes(id),
    reference_text TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS queries (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL,
    question TEXT NOT NULL,
    created_at TEXT NOT NULL,
    -- ADR-004: the run record. A run cannot be compared across runs unless
    -- the model, backend, sampling parameters and prompt version that
    -- produced it are named, so they live with the ask, not only in the
    -- response. NULL when no model ran (a refusal, a model-free generator).
    model TEXT,
    backend TEXT,
    temperature REAL,
    seed INTEGER,
    prompt_version TEXT,
    question_hash TEXT,
    iterations INTEGER,
    evidence_ids TEXT,
    tokens_in INTEGER,
    tokens_out INTEGER,
    tokens_source TEXT,
    latency_ms INTEGER,
    insufficient INTEGER
);
CREATE TABLE IF NOT EXISTS retrieval_runs (
    id TEXT PRIMARY KEY,
    query_id TEXT NOT NULL,
    algorithm TEXT NOT NULL,
    latency_ms INTEGER,
    retrieved_nodes TEXT,
    weights TEXT,
    normalization TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS evaluation_runs (
    id TEXT PRIMARY KEY,
    dataset TEXT NOT NULL,
    config TEXT,
    metrics TEXT,
    created_at TEXT NOT NULL
);
"""

#: The eval columns the DDL above does not carry, added after the fact.
#:
#: architecture.md §8's DDL is frozen, and the two eval tables it declares
#: are the ones the eval runner writes. But evaluation.md §6 requires the
#: ``retrieval_runs`` row to carry the JOIN KEYS ``(evaluation_id,
#: question_id, arm)`` plus the node ids, the weights snapshot and the
#: latency, and the frozen DDL has no column for any of the three join
#: keys. The DDL is not rewritten: the columns are added here, and the
#: migration is idempotent, so a store created by the frozen DDL and a
#: store created by the current code end up with the same shape.
#:
#: The pattern is the one ``apps/api/retrieval/index.py`` already uses for
#: the ``ident_index.level`` column: CREATE IF NOT EXISTS, then ADD COLUMN
#: when the column is missing.
EVAL_COLUMN_MIGRATIONS: tuple[tuple[str, str, str], ...] = (
    # (table, column, DDL)
    (
        "evaluation_runs",
        "config_id",
        "config_id TEXT",
    ),
    (
        "evaluation_runs",
        "dataset_snapshot",
        "dataset_snapshot TEXT",
    ),
    (
        "evaluation_runs",
        "status",
        "status TEXT",
    ),
    (
        "evaluation_runs",
        "split_seed",
        "split_seed INTEGER",
    ),
    (
        "evaluation_runs",
        "judge_prompt_version",
        "judge_prompt_version TEXT",
    ),
    (
        "evaluation_runs",
        "code_version",
        "code_version TEXT",
    ),
    (
        "evaluation_runs",
        "model",
        "model TEXT",
    ),
    (
        "evaluation_runs",
        "backend",
        "backend TEXT",
    ),
    (
        "evaluation_runs",
        "temperature",
        "temperature REAL",
    ),
    (
        "evaluation_runs",
        "seed",
        "seed INTEGER",
    ),
    (
        "evaluation_runs",
        "arms",
        "arms TEXT",
    ),
    (
        "evaluation_runs",
        "k",
        "k TEXT",
    ),
    # The run's measurement provenance (docs/TODO.md, the OPEN GAP item):
    # which retrieval INSTRUMENT produced the metric, and which LEX PATH
    # produced the vectorless arm's lex scores. Both are NULL on rows
    # recorded before the columns existed - a NULL is the fact "not
    # recorded", and it is never back-filled from what we now believe the
    # run used. Rows recorded after the change carry both.
    (
        "evaluation_runs",
        "retrieval_instrument",
        "retrieval_instrument TEXT",
    ),
    (
        "evaluation_runs",
        "lex_paths",
        "lex_paths TEXT",
    ),
    # The per-question, per-arm row. The join keys come first; the rest is
    # what the GET endpoint's QuestionRow needs, so the endpoint is a
    # join and not a second computation.
    (
        "retrieval_runs",
        "evaluation_id",
        "evaluation_id TEXT",
    ),
    (
        "retrieval_runs",
        "question_id",
        "question_id TEXT",
    ),
    (
        "retrieval_runs",
        "arm",
        "arm TEXT",
    ),
    (
        "retrieval_runs",
        "category",
        "category TEXT",
    ),
    (
        "retrieval_runs",
        "answerable",
        "answerable INTEGER",
    ),
    (
        "retrieval_runs",
        "gold_nodes",
        "gold_nodes TEXT",
    ),
    (
        "retrieval_runs",
        "rank_first_hit",
        "rank_first_hit INTEGER",
    ),
    (
        "retrieval_runs",
        "gold_hit",
        "gold_hit INTEGER",
    ),
    (
        "retrieval_runs",
        "generation_latency_ms",
        "generation_latency_ms INTEGER",
    ),
    (
        "retrieval_runs",
        "total_latency_ms",
        "total_latency_ms INTEGER",
    ),
    (
        "retrieval_runs",
        "tokens_in",
        "tokens_in INTEGER",
    ),
    (
        "retrieval_runs",
        "tokens_out",
        "tokens_out INTEGER",
    ),
    (
        "retrieval_runs",
        "tokens_source",
        "tokens_source TEXT",
    ),
    (
        "retrieval_runs",
        "tool_iterations",
        "tool_iterations INTEGER",
    ),
    (
        "retrieval_runs",
        "insufficient",
        "insufficient INTEGER",
    ),
    (
        "retrieval_runs",
        "citations_valid",
        "citations_valid INTEGER",
    ),
    (
        "retrieval_runs",
        "citation_node_ids",
        "citation_node_ids TEXT",
    ),
    (
        "retrieval_runs",
        "claims_matched",
        "claims_matched INTEGER",
    ),
    (
        "retrieval_runs",
        "claims_total",
        "claims_total INTEGER",
    ),
    (
        "retrieval_runs",
        "faithfulness",
        "faithfulness REAL",
    ),
    (
        "retrieval_runs",
        "judge_verdicts",
        "judge_verdicts TEXT",
    ),
    (
        "retrieval_runs",
        "answer_markdown",
        "answer_markdown TEXT",
    ),
    (
        "retrieval_runs",
        "judge_prompt_version",
        "judge_prompt_version TEXT",
    ),
)


def apply_column_migrations(conn: sqlite3.Connection) -> None:
    """Add the eval columns the frozen §8 DDL does not carry.

    Idempotent: a column that is already there is left alone, so a store
    created by the frozen DDL and a store created by the current code end
    up with the same shape. A store that is read-only (a restored copy on
    a read-only mount) raises, and the caller sees the error rather than a
    table that is silently missing the join keys.
    """
    for table, column, ddl in EVAL_COLUMN_MIGRATIONS:
        columns = {
            str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})")
        }
        if column in columns:
            continue
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


@contextmanager
def connect(db_path: str | Path) -> Iterator[sqlite3.Connection]:
    """Open a connection with the §8 schema applied and FK enforcement on."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    apply_column_migrations(conn)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
