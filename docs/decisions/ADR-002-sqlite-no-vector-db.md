# ADR-002: SQLite + filesystem for MVP storage (no Postgres, no vector DB)

## Status
Accepted

## Date
2026-10-07

## Context
MVP is local-first, single-user, one research artifact. Candidates: SQLite+files,
Postgres, Postgres+pgvector, dedicated vector DB. The retrieval path is vectorless
by design, so any vector store exists only to serve the quarantined baseline.

## Decision
SQLite (`documents, nodes, references, queries, retrieval_runs, evaluation_runs`)
+ filesystem (PDFs, extracted artifacts, BM25 indexes). FTS via SQLite FTS5.
No Postgres, no vector DB in v1.

## Alternatives Considered

### Postgres (+ pgvector for the baseline)
- Pros: production-shaped; baseline could live in the same store.
- Cons: process to run, backup, configure — for a single-user local tool; tempts
  primary-path code toward vector queries.
- Rejected: cost without research benefit; revisit only with multi-user requirement.

### Dedicated vector DB (Chroma/Qdrant/FAISS service)
- Pros: fast ANN for the baseline arm.
- Cons: the primary path must never touch it; running infra whose only legal
  caller is `evaluation/` invites leakage and weakens the vectorless claim.
- Rejected: baseline runs in-process under `experiments/vector/`; swap if it can't
  hold 100-question scale (it can).

## Consequences
- Zero infra to boot: `uvicorn` + file. Demo-friendly.
- Migration path documented: schema is plain SQL; a Postgres move is mechanical.
- Import scan enforces that no vector-store client appears in `apps/` outside `evaluation`.
