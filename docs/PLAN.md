# Tuklas Agent - Implementation Plan (Day 0-7)

**Status:** Derived from the audited spec suite and three parallel audits. The Day 0-9 slices it sequences are built and recorded in `docs/TODO.md` (57 items checked, 0 open); this document stays as the record of WHY each slice was cut where, and of the three decisions the audit forced before any code was written. **Read this before writing new code.**
**Scope:** the plan is a contract between the spec docs and the builders. It does not re-author requirements - it sequences them and flags every latent inconsistency.
**Authoritative precedence when docs conflicted during audit:** PRD (requirements) → api.md (contracts) → architecture.md (module boundaries + build order) → retrieval.md → evaluation.md → ADRs → CONSTRAINTS (floor).

## Changes applied during this audit (Day 0)

These are the spec-contradictions the audit found. All are corrected so builders inherit one reading.

| # | Conflict | Resolution | File |
|---|----------|------------|------|
| 1 | Sufficiency gate `0.20` (retrieval.md) vs `0.15` (architecture.md + PRD) | `0.15`, authoritative to architecture.md | retrieval.md §5 |
| 2 | Quarantine scope missed `api/` route-handler subdir | Added `api/` to DENY_PRIMARY scope | architecture.md §2, CONSTRAINTS.md |
| 3 | `cosine (` grep too brittle | `cosine\s*\(` (the space is no longer mandatory) | architecture.md §2 |
| 4 | COPY.md backends label + 3 items vs copy.ts 7 marks + actual label | Updated label to "Local runtimes, and the open-weight models they serve"; added 4 model rows | COPY.md |
| 5 | COPY.md sources omitted DESIGN.md | Added `docs/landing-page/DESIGN.md` to sources-of-truth | COPY.md |
| 6 | Dead `design.ts` reference in COPY.md repo-tree | Corrected to `copy.ts + site.ts` | COPY.md |

## Open decisions blocking Day 1 (not resolved here)

These are genuinely open questions the spec itself leaves live. **Do not begin any slice without them.**

- **D1 — Node id format.** ~~`gold_nodes` in the frozen dataset (e.g. `article_1318`) must resolve to real node ids produced by ingestion. No doc defines the id format (`architecture.md §8` DDL: `id TEXT PK` — no generator). **Consequence of not deciding:** `Recall@5 = 0` for every question, the run "completes," and nothing fails. The most dangerous failure mode because it is silent.~~ **RESOLVED (2026-10-19):** `node_{doc_slug}_{type}_{locator}` — grammar, examples and the four seed-test invariants in `architecture.md` §3.
- **D2 — Benchmark corpus.** ~~PRD §10 Q1. No public statute is selected.~~ **RESOLVED (2026-10-19):** primary = Civil Code of the Philippines (R.A. 386, ICNL text-layer PDF, 167 pp); secondary = U.S. Code Title 11 (official govinfo PDF, 334 pp). Evidence + checksums in `docs/CORPUS.md`. The original candidates (Civil Rights Act, California Civil Code) were verified and rejected — the "≥100pp" claim was false and no official CA PDF exists.
- **D3 — Arm B embedding model.** ~~ADR-004 pins the *generation* model across arms but names no embedding model for the dense baseline.~~ **RESOLVED (2026-10-19): `BAAI/bge-small-en-v1.5` via `fastembed==0.9.0` (384 dims, MIT, ONNX Runtime CPU, HF revision pinned); recorded in ADR-002 "Arm B embedding model".**

## Build order (architecture.md §10, annotated with dependencies)

architecture.md §10 lists 6 vertical slices. In dependency order with the blocking conditions:

```
[Day 0]  pre-flight: D1 + D2 + D3 decided → 0.1 reconciliation closed
         ↓
[Day 1]  slice 1: ingestion (PDF → pages → tree → SQLite + indexes)
         needs D1/D2; produces check_no_vectors.py as gate for slice 2
         ↓
[Day 2]  slice 2: retrieval (BM25 + structural + reference, no LLM)
         needs slice 1; quarantined from vector imports
         ↓
[Day 3]  slice 3: agent loop (LangGraph + sufficiency gate)
         needs slice 2; 7 thin wrappers, max 12 iterations
         ↓
[Day 4]  slice 4: generation (LLMInterface + citations + refusal)
         needs slices 2+3; ADR-004 ollama default, temp=0, seed pinned
         ↓
[Day 5]  slice 5: web three-pane explorer + SSE client at /app/*
         needs slice 4; persistent disclaimer banner, advice blocklist
         ↓
[Day 6]  slice 6: evaluation framework (both arms + metrics + judges)
         needs slices 1–5 + D3 (embedding model) + D2 (dataset)
         ↓
[Day 7]  pinned dependency manifest (Poetry) + CI (check_eval_config.py, all gates)
         needs every slice green
```

### 0. Pre-flight (Day 0, morning) — REQUIRED

- [x] Resolve D1: pin the node id format and write one line in architecture.md §3 documenting it. → **`node_{doc_slug}_{type}_{locator}` (see §3)**
- [x] Resolve D2: pick 1–2 public statutes (candidates: U.S. Civil Rights Act; California Civil Code — both public domain, hierarchical, ≥100pp). → **R.A. 386 (primary) + U.S. Code Title 11 (secondary); original candidates rejected on verified evidence — `docs/CORPUS.md`**
- [x] Resolve D3: pick a CPU-runnable dense embedding model and record it in ADR-002. → **`BAAI/bge-small-en-v1.5` via `fastembed==0.9.0` (HF revision `5c38ec7c405ec4b44b94cc5a9bb96e735b38267a`)**
- [x] Apply the 6 reconciliation edits above (done in this pass; re-verified on main 2026-10-19).

**Block:** nothing in Day 1–7 may begin until all three are resolved.

### Day 1 — Slice 1: Ingestion (PDF → pages → tree)

**Targets:** `apps/api/ingestion/`, `apps/api/api/` (route handlers), `data/` layout

**Concrete deliverables:**
- `POST /v1/documents` → 201 / 422 `VALIDATION_ERROR | OCR_REQUIRED | PARSE_FAILED` / 409 CONFLICT / 404.
- PyMuPDF pipeline: `PDF → pages → text + page_start/end`. Page < 50 chars = empty. Doc > 30% empty → 422 `OCR_REQUIRED`. Encrypted → 422 `PARSE_FAILED`. Hash dedup → 409.
- Structure parser: regex + RapidFuzz heading match → `nodes` table (id, document_id, parent_id, type, title, text, page_start, page_end, depth, position) + `references` table.
- `parse_confidence` ∈ [0,1] = fraction of pages with ≥ 1 heading match. `< 0.5` → flat fallback (doc → page nodes, depth 0/1), flag stored + returned by `GET /documents/{id}`.
- `scripts/check_no_vectors.py` written and green on this slice — the gate that lets slice 2 proceed.

**Verification (PRD §7 US-1):**
- `pytest -k ingest` on the 2 chosen statutes: `page_count` correct, `root.type` correct, `child_count > 0`, 100% leaves have `page_start`/`page_end`, flat fixture gets `parse_confidence < 0.5` + fallback flag.

**Depends on:** D1 (node id format), D2 (corpus).

### Day 2 — Slice 2: Retrieval engine (BM25 + structural + reference, no LLM)

**Targets:** `apps/api/retrieval/`

**Concrete deliverables:**
- SQLite tree store: `nodes` + `references`, indexes `(document_id, parent_id)`, `(document_id, type)`, FTS5 on `(title, text)`.
- Per-document BM25 index → `data/indexes/{doc_id}/bm25.json`.
- Identifier index: normalized labels → node ids (normalizer per retrieval.md §4).
- Two-pass scoring: `lex`/`ident`/`title` (pass 1, fixes lex-top-5/top-3) → `ref`/`hier` (pass 2). Weights all 1.0 defaults, range [0,2].
- 7 tool functions: `search_nodes`, `inspect_node`, `list_children`, `get_parent`, `expand_context`, `follow_reference`, `get_source`. (+ `select_evidence` called direct by the agent gate, not a wrapper: this is the 7-vs-8 reconciliation.)
- `select_evidence()`: dedup → drop < 50 chars → drop page-span-less → rank by score → top 8 → per-sub-question support check at **0.15** (post-reconciliation).
- Context Builder: `build_context(EvidenceSet) -> PromptContext`, 12k char budget, score-desc order, gate-set nodes never dropped, `context_truncated` flag.

**Verification (retrieval.md §8):**
- `test_context_builder_budget` (CONSTRAINTS.md): order/truncation/overflow rules hold.
- Golden 10-question smoke: every `gold_node` in top-5. (Note: `gold_nodes` use the D1 format — full ids with the document slug prefix.)

**Depends on:** Day 1. `check_no_vectors.py` must be green.

### Day 3 — Slice 3: Agent loop (LangGraph + sufficiency gate)

**Targets:** `apps/api/agent/`

**Concrete deliverables:**
- LangGraph graph: `START → query_plan → search → inspect → (expand : backtrack) → select_evidence → sufficiency_gate → build_context → generate → END`.
- **7 thin wrappers** only (arg validation + call `retrieval.api` + trace logging).
- Max 12 tool iterations **including** broaden-retry. Retry does not reset.
- Per-tool output caps (architecture.md §4 l70): `search_nodes` max 20, `list_children` max 50, `inspect_node`/`get_parent`/`get_source` 8000-char cap, `expand_context` radius ≤ 2 / ≤5 nodes / ≤2000 chars, `follow_reference` single node.
- Sufficiency gate: per-sub-question `score ≥ 0.15 AND (lex > 0 OR identifier-hit)`. `relevant: bool` on every `tool_result`; irrelevant branches logged with branch id. Broaden-retry once (stoplist per retrieval.md §3), then `insufficient_evidence` exit with the unified abstention shape.

**Verification (PRD §7 US-3):**
- Multi-hop fixture trace: ≥ 1 abandoned branch (relevant=false, zero evidence) + ≥ 2 inspect/expand before the answer.

**Depends on:** Day 2. **Must NOT import** `sqlite3, sqlalchemy, apps.api.models` (CONSTRAINTS.md l15, same script).

### Day 4 — Slice 4: Generation (LLMInterface + citations + refusal)

**Targets:** `apps/api/generation/`

**Concrete deliverables:**
- `LLMInterface.generate()` + `.generate_stream()` (ADR-004): ollama default, `temperature=0`, `seed` pinned.
- Renders opaque `PromptContext` — never reorders or truncates evidence (that's retrieval's Context Builder; CONTRACT TEST catches it).
- Citation assembly: `citations[] = [{n, node_id, label, page_start, page_end}]`.
- Advice blocklist post-generation: rejects "you should", "I advise", "your case", "you must".
- Refusal path: insufficient evidence → canned abstention string (pin the exact wording) + `citations: []` + `insufficient: true`.

**Verification (PRD §7 US-2):**
- 5 gold questions: LLM-judge (blinded) confirms 100% of factual claims cited; builder spot-checks all 5.
- Advice blocklist unit test.

**Depends on:** Days 2+3.

### Day 5 — Slice 5: Web three-pane explorer + SSE client

**Targets:** `apps/web/src/app/app/` (new route), new components

**Concrete deliverables:**
- Three panes: tree / source / answer at `/app/*`.
- `POST /v1/ask/stream` SSE client consuming the frozen event sequence: `plan → tool_call → tool_result → (repeat) → evidence → token* → done | error`. Payload schemas are pinned in `api.md` §3 (gap #18 from the audit — **closed by the Day-3 slice**; the abstention string is pinned there too).
- Citation click → `GET /v1/nodes/{id}/source` → source pane renders `text + page + hierarchy_path`.
- **Persistent disclaimer banner** in the answer pane — worded exactly as in `copy.ts` line 221. Must persist across answer interactions, not scroll with sections.

**Verification (AGENTS.md pre-flight §13):**
- tsc `--noEmit` clean, eslint clean, `npm run build` clean.
- Browser test: SSE events render in order; citation click opens source; disclaimer stays visible during stream.

**Depends on:** Day 4.

### Day 6 — Slice 6: Evaluation framework (both arms + metrics)

**Targets:** `apps/api/evaluation/`, `experiments/vector/`

**Concrete deliverables:**
- `POST /v1/evaluations` → 202 `{ evaluation_id, status }`. `GET /v1/evaluations/{id}` → per-question rows + aggregates + system stats.
- Frozen 72Q dataset at `data/datasets/v1/{dev,test}.json` (24 dev + 48 test, 7 categories, ≥ 3 dev + ≥ 6 test per category, ≥ 5 `answerable: false`, seed 42).
- Arm A (Tuklas): full agent loop, slices 1–5.
- Arm B (Vector): dense retrieval, `experiments/vector/` only, embedding model from D3. **Must expose the same retrieval result shape** so "arms differ only in retrieval" holds.
- Versioned arm-blinded judge prompt → faithfulness 0/1 per claim. 20% spot-check by builder; > 15% disagreement invalidates the run.
- `evaluation_runs` + per-question `retrieval_runs` (join keys `(evaluation_id, question_id, arm)`), persisted per question per arm.
- `check_eval_config.py` (Day 7 deliverable, per CONSTRAINTS.md) — model/parity check: same model + backend + temperature(0) + seed across arms.

**Verification (PRD §7 US-4, US-5):**
- 10-question smoke: completes, all rows have node ids (except `insufficient: true` → `[]`), judge scores present.
- `check_no_vectors.py` green on the quarantine assertion.

**Depends on:** D2 (dataset), D3 (embedding model), slices 1–5.

### Day 7 — Requirements, CI, reproducibility

**Targets:** repo root (`pyproject.toml` + `poetry.lock`, `.github/workflows/ci.yml`)

**Concrete deliverables:**
- `pyproject.toml` (Poetry, PEP 621 metadata + `poetry.lock`): PyMuPDF, rank-bm25 (or FTS5-only), rapidfuzz, langgraph, pydantic, fastapi, uvicorn (pinned; install with `poetry install`).
- `scripts/check_no_vectors.py` + `scripts/check_eval_config.py` finalized.
- `.github/workflows/ci.yml`: mypy on `apps/api` → zero errors; ruff on `apps/api` → zero errors; tsc + eslint on `apps/web` → zero errors; gitleaks → zero findings; both check scripts → exit 0; pytest → green; diff-cover ≥ 80% on changed lines.
- **Ratchet:** first benchmark run on dev-24 sets `Recall@5`, `MRR@10`, p95 latency in CONSTRAINTS.md's "Measured, not yet enforced" table. Update that table; do not ship unmeasured numbers.

**Verification:** full CI green. Re-run with same config id → retrieval exact, generation within ±5pp.

**Depends on:** all slices green.

---

## What MUST be true before the first benchmark run is reportable

From the eval-readiness audit - a run that violates any of these **completes but cannot be published honestly**. All eight now hold: the corpus is fetched and checksum-verified, the node id format is pinned and asserted by `test_d1_invariants`, the judge is blinded with its disagreement reported, the run rows are stored, the model-parity and quarantine checks are CI gates, and the dependency manifest is pinned. The list is kept as the failure modes they were.

1. **No corpus** (D2) → no questions, no nodes.
2. **`gold_nodes` don't resolve to real ids** (D1) → `Recall@5 = 0` silently, everything else looks valid.
3. **Judge not blinded / disagreement > 15% uncomputable** (no prompt text) → faithfulness is opinion, not measurement.
4. **No `evaluation_runs`/`retrieval_runs` rows** → nothing to GET, nothing to audit.
5. **No model parity check** → invalid run (different models across arms) passes CI.
6. **No quarantine scan** → "vectorless" claim unverifiable; Arm A could secretly use embeddings.
7. **No pinned dependency manifest (poetry.lock)** → scorer (RapidFuzz) can't be installed; the 85-threshold is aspirational.
8. **Node id format mismatch** (D1 again — it is the most dangerous item) → all Recall/MRR structurally zero.

## The three most expensive risks to not catch here

| Risk | Mechanism | Mitigation |
|------|-----------|------------|
| Silent zero-recall | `gold_nodes` format ≠ tree node id format | **Caught before Day 1.** D1 pinned the id grammar `node_{doc_slug}_{type}_{locator}` (architecture.md §3); `test_d1_invariants` (apps/api/tests/test_ingest.py) asserts the four invariants (existence, `node_{question.document}_` prefix match, id uniqueness, rebuild determinism) |
| Invalid comparison | Arms differ in more than retrieval (agent mediation asymmetry) | **Caught and decided (D4).** Arm B runs the same seven tools by TOOL SUBSTITUTION - `arms.py` swaps `TOOL_TABLE["search_nodes"]` for the dense tool and restores it in a `finally`, and both arms call the same `agent_graph.run_agent`. The parity boundary is recorded in ADR-006 §1 |
| Undetectable vector leak | Route handler in `api/` imports `faiss`, passes the old `check_no_vectors.py` scope | **Caught.** `scripts/check_no_vectors.py` puts `apps/api/api/` in `QUARANTINE_DIRS` and scans recursively; it is a CI gate |
