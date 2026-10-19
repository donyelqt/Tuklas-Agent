# Constraints (rev 2 — post adversarial review)

Last reviewed: 2026-10-07 by staff-eng (rev 2 — review fixes applied, still pre-implementation).

Agents: read this file before writing code. Do not weaken it to make a change pass.
Tightening is silent; loosening requires human approval in the commit message.

## Floor (always enforced, no setup required)

- No new suppression comments: `# type: ignore`, `# noqa`, `eslint-disable`, `@ts-ignore`.
- No unimplemented stubs: `raise NotImplementedError`, `...`, `pass`-only handlers, empty `catch {}`.
- No skipped or deleted tests without a reason in the commit message.
- No secrets in source (API keys, tokens, absolute private paths).
- Vectorless quarantine (research-credibility gate): `apps/api/{ingestion,retrieval,agent,generation,api}/` must not import `sentence_transformers | faiss | chromadb | qdrant_client | pgvector`, and `cosine_similarity` must not appear in `apps/api/retrieval/`. `experiments/vector/` must be imported only by `apps/api/evaluation/`. Checked by `scripts/check_no_vectors.py` (Day 3 deliverable, before any retrieval code merges).
- Agent thinness: `apps/api/agent/` must not import `sqlite3 | sqlalchemy | apps.api.models` — all node access via the 7 tools. Same script, same gate.
- No legal-advice language in prompts/UI ("you should", "I advise", "your case", "you must") — research tool framing only.
- This file does not get weakened to make a change pass.

## Enforced with numbers

| Dimension | Rule | Checked by | Runs at |
|-----------|------|-----------|---------|
| Types (Python) | Zero `mypy` errors on `apps/api` | `mypy apps/api` | task end, CI |
| Types (TS) | Zero type errors on `apps/web` | `tsc --noEmit -p apps/web` | task end, CI |
| Lint (Python) | Zero errors from repo config | `ruff check apps/api` | every edit |
| Lint (TS) | Zero errors from repo config | `eslint apps/web` | every edit |
| Secrets | No findings | `gitleaks detect --redact --no-banner` | every edit, CI |
| Vectorless quarantine + agent thinness | Script exit 0 | `python scripts/check_no_vectors.py` | task end, CI |
| Eval model parity | Same model+backend+temperature(0)+seed across arms | `python scripts/check_eval_config.py <run-config.json>` (Day 7; until then reviewer asserts config equality in the eval PR) | CI on eval runs |
| Tests | New/changed behavior has a test; suite green | `pytest apps/api -q` / `npm test --prefix apps/web` | task end, CI |
| Coverage (Python, changed lines) | ≥ 80% on diff | `pytest --cov --cov-report=xml apps/api && diff-cover --compare-branch=main --fail-under=80 coverage.xml` (`diff-cover` pinned in requirements) | task end, CI |
| SSE contract | Event order pinned incl. zero-tool-call + error cases | contract test `test_ask_stream_order` | CI |
| Context budget | Builder order/truncation/overflow rules hold | contract test `test_context_builder_budget` | CI |
| Breaking API | No response-field removal/rename under `/v1` | schema-snapshot test `test_api_v1_snapshot` | CI |

## Measured, not yet enforced

| Metric | Today | Direction |
|--------|-------|-----------|
| Recall@5 (Tuklas, dev-24) | unmeasured | must not fall once first run lands |
| MRR@10 (Tuklas, dev-24) | unmeasured | must not fall once first run lands |
| Ask p95 latency (local) | unmeasured | record; budget set after first run |

First benchmark run sets the ratchet values above — update this table then, not before.
(Dev split is 24Q per `evaluation.md §1`; the 10Q smoke gate is a validity check, not the ratchet set.)

## Exceptions

| ID | Rule | Path | Reason | Owner | Expires |
|----|------|------|--------|-------|---------|
| — | — | — | none yet | — | — |
