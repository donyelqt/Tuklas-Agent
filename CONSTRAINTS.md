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
  - **Tightened (2026-10-21, Day 7):** the quarantined directories may not import `fastembed`, `onnxruntime` or `numpy` either. The deny list names libraries; the intent is that the vectorless arm contains no vector machinery, and those three are the runtime the dense arm is actually built on. `numpy` is not among the five named packages, and the `cosine_similarity` grep cannot see a cosine written as `np.dot(a, b) / (norm(a) * norm(b))`. Tightening is permitted without approval.
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
| Recall@5 (Tuklas, dev-24) | 0.674 | must not fall |
| MRR@10 (Tuklas, dev-24) | 0.711 | must not fall |
| Ask p95 latency (local) | 6,833 ms | record; budget set after first run |

First benchmark run sets the ratchet values above — update this table then, not before.
(Dev split is 24Q per `evaluation.md §1`; the 10Q smoke gate is a validity check, not the ratchet set.)

**Status (2026-10-21, Day 6 + 7, RE-SET after the Day-8 instrument fix):** the
ratchet is SET, from a real dev-24 run of the evaluation harness. Config
`cfg_0f73ff26c0d38d54`, split seed 42, model `qwen2.5:3b` on ollama at
temperature 0, seed 42, judge prompt `judge-v1-batch`, n = 24 questions x 2
arms. The values above are the Tuklas (vectorless) arm's, macro-averaged over
the 23 answerable dev questions; the 1 unanswerable row is excluded from the
retrieval aggregate and scored by abstention instead.

**The ratchet was RE-SET, because the INSTRUMENT changed (not because the
system got better or worse).** The earlier values — Recall@5 0.717, MRR@10
0.708, ask p95 608,259 ms — were measured over the OLD instrument, and that
instrument is gone:

* the retrieval metric is now computed over the node ids the ANSWER was
  produced from (the union of the agent's per-sub-question `search_nodes`
  results), where the old one was computed over a separate one-off search over
  the whole question — a ranking the model never saw. The old numbers
  therefore measured a DIFFERENT retrieval, and comparing them with the new
  ones as if the instrument had been constant would be wrong in both
  directions;
* `latency_ms` is now the sum of the agent's own `search_nodes` calls for the
  question, where the old figure was a one-off whole-question probe. The old
  608,259 ms p95 was one local generation call taking ten minutes, measured on
  the OLD probe; the new p95 (6,833 ms) is over the NEW measure.

A ratchet that silently changes its instrument is not a ratchet, so the change
is stated here rather than left for a reader to infer from a diff. The
replacement values are from the re-run of the same config over the same dev
split: `Recall@5 = 0.674`, `MRR@10 = 0.711`, ask p95 = 6,833 ms with a p50 of
3,376 ms.

One fact about the p95 value, so it is not misread. It is 6,833 ms against a
p50 of 3,376 ms, and the p95 is still a single order statistic over 24 samples,
so the budget is NOT set by this row: "must not fall" on a p95 over 24 samples
would be a claim about noise. The metric is recorded as measured, because a
ratchet that smooths a tail away is not a ratchet. The p50 (3,376 ms) is the
number a reader should compare against.

The reproducible artifact is the run itself: the `evaluation_runs` row for the
re-run, with its 48 `retrieval_runs` rows in the store, joinable on
`(evaluation_id, question_id, arm)`. The published reports are
`data/eval-reports/v1-dev.md` and `data/eval-reports/v1-test.md`.

**What is NOT in this table, and why.** The dense arm's numbers are not ratchet
values: the ratchet is the vectorless arm's, and the two arms differ in more
than the retrieval mechanism (ADR-005's identifier precedence and `ident`
component exist only in the vectorless arm), so a ratchet that tracked both would
be a ratchet on two different systems. The reported TEST-48 numbers live in
`data/eval-reports/v1-test.md`, not here: evaluation.md §1 makes the test split
the reported one and the dev split the tuning one, and a ratchet that the tuning
split can move is not a ratchet.

## Exceptions

| ID | Rule | Path | Reason | Owner | Expires |
|----|------|------|--------|-------|---------|
| — | — | — | none yet | — | — |
