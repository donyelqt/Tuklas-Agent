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


@contextmanager
def connect(db_path: str | Path) -> Iterator[sqlite3.Connection]:
    """Open a connection with the §8 schema applied and FK enforcement on."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
