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

## Arm B embedding model (D3, decided 2026-10-19)

The dense baseline (Arm B) embeds with **`BAAI/bge-small-en-v1.5`** via
**`fastembed==0.9.0`** — 384 dims, MIT license, HF revision
`5c38ec7c405ec4b44b94cc5a9bb96e735b38267a` pinned for reproducibility.

- CPU-only: fastembed's dependency chain is ONNX Runtime (`onnxruntime`, never
  `onnxruntime-gpu`) — no torch anywhere in the shared requirements.
- Quality (verified, MTEB Retrieval nDCG@10): 51.68 vs 49.04 (`intfloat/e5-small-v2`,
  not shipped by fastembed, would force a torch path) and 41.95
  (`sentence-transformers/all-MiniLM-L6-v2`); within 0.30 of the best 384-dim CPU
  candidate (`Snowflake/snowflake-arctic-embed-s`, 51.98, runner-up).
- Zero-configuration default model of fastembed; query instruction policy recorded as
  "none" (BGE v1.5 runs without instructions with slight degradation; e5 requires a
  prefix discipline the eval config would have to enforce).
- Parity: Arm B records model + revision + seed in the eval run config
  (`check_eval_config.py`), so arms differ only in retrieval (ADR-003/ADR-004 parity
  boundary unchanged).
- Reproducibility: `fastembed==0.9.0` (PyPI) + HF revision SHA; re-running the baseline
  must produce identical vectors.

## Consequences
- Zero infra to boot: `uvicorn` + file. Demo-friendly.
- Migration path documented: schema is plain SQL; a Postgres move is mechanical.
- Import scan enforces that no vector-store client appears in `apps/` outside `evaluation`.
