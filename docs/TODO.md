# TODO — Tuklas Agent Task Tracker

**Companion to:** `PLAN.md` (build order + acceptance criteria), `SPEC.md` (frozen
contracts), `CONSTRAINTS.md` (quality floor), `architecture.md` §10 (slices).

**Update as you go:** move `[ ]` → `[x]` per completed task. Each task owns its acceptance
criteria in `PLAN.md`; this list tracks the gates. A slice is **done** only when its
acceptance criteria pass and the CONSTRAINTS.md floor holds — not before.

The three open decisions (D1/D2/D3, `SPEC.md` §10) are **hard blockers** — nothing in
Day 1–7 may begin until all three are resolved. Record each decision in the doc `PLAN.md`
points at.

---

## Day 0 — Pre-flight (BLOCKED on D1 + D2 + D3)

- [x] **D1 — Node id format.** ~~Pin the format that resolves `gold_nodes` in the frozen
  dataset to tree node ids produced by ingestion; write one line in
  `architecture.md` §3 documenting it.~~
  **DONE (2026-10-19):** format `node_{doc_slug}_{type}_{locator}` recorded in `architecture.md` §3; acceptance = the four invariants (existence + slug-prefix match + uniqueness + rebuild determinism), restated in PLAN risk table.
  - *Consequence if skipped:* `Recall@5 = 0` silently — the run "completes" but every gold
    node mismatches. The most dangerous failure mode because it is silent.
  - *Acceptance:* id format documented + a seed-data test asserting every `gold_node` in
    the dataset exists as a node id in the ingested tree.
- [x] **D2 — Benchmark corpus.** ~~Pick 1–2 public statutes (PLAN.md candidates: U.S. Civil
  Rights Act; California Civil Code — public domain, hierarchical, ≥100pp).~~
  **DONE (2026-10-19):** primary = Civil Code of the Philippines (R.A. 386, ICNL
  text-layer PDF, 167 pp); secondary = U.S. Code Title 11 (official GPO PDF, 334 pp).
  Both PDFs fetched into `data/documents/` (sha256 in `docs/CORPUS.md`),
  `data/datasets/v1/` initialized; evidence + rejected-candidates record in
  `docs/CORPUS.md`. Dataset slugs: `civil-code-public`, `usc-title-11`.
  - *Consequence if skipped:* nothing to ingest, `gold_nodes` reference articles that
    don't exist, 72Q dataset is fictional.
  - *Acceptance:* corpus PDFs fetched into `data/documents/`; `data/datasets/v1/`
    structure initialized. ✔ verified locally: 167/334 pages, text layer extracted,
    `Article 1318` seed example located verbatim.
- [x] **D3 — Arm B embedding model.** ~~Pin a CPU-runnable dense embedding model for the
  vector baseline; record it in `docs/decisions/ADR-002`.~~ **DONE (2026-10-19):**
  `BAAI/bge-small-en-v1.5` via `fastembed==0.9.0` (384 dims, MIT, ONNX Runtime CPU, HF
  revision `5c38ec7c405ec4b44b94cc5a9bb96e735b38267a`), recorded in ADR-002.
  - *Consequence if skipped:* Arm B cannot be built; the "arms differ only in retrieval"
    parity claim is unenforceable.
  - *Acceptance:* embedding model named in ADR-002; the package lands in the pinned
    pinned manifest (Day 7, poetry.lock). ✔ model recorded; package pin carried to Day 7.
- [ ] Confirm the 6 reconciliation edits from `PLAN.md` Day 0 are present on main
  (threshold 0.15, quarantine scope incl. `api/`, cosine grep, COPY.md backends label,
  DESIGN.md sources, dead design.ts ref) — committed in `97d862d`/`5e16463`.
  - *Acceptance:* spot-check each of the 6 against its file; any regression re-fixed here.

**Block condition:** D1 + D2 + D3 all `[x]`. No slice starts until then.

---

## Day 1 — Slice 1: Ingestion (PDF → pages → tree)

**Targets:** `apps/api/ingestion/`, `apps/api/api/` (route handlers), `data/` layout,
`scripts/check_no_vectors.py`. **Depends on:** D1, D2.

- [ ] `POST /v1/documents` returns 201 / 422 / 409 per `api.md` §2 with the frozen error
  shape (`VALIDATION_ERROR | OCR_REQUIRED | PARSE_FAILED`, dup-hash `CONFLICT`).
- [ ] PyMuPDF pipeline: PDF → pages → text + page_start/end. Page < 50 chars = empty;
  doc > 30% empty → 422 `OCR_REQUIRED`; encrypted/unreadable → 422 `PARSE_FAILED`;
  SHA-256 dedup → 409.
- [ ] Structure parser (regex + RapidFuzz heading match) → `nodes` + `references` tables
  per `SPEC.md` §3 DDL, indexes included.
- [ ] `parse_confidence ∈ [0,1]`; `< 0.5` → flat fallback (document → page nodes, depth
  0/1), flag stored on document + returned by `GET /documents/{id}` + badged in UI.
- [ ] `scripts/check_no_vectors.py` written and green — the gate that lets slice 2
  proceed (quarantine scope + agent-thinness + cosine grep, recursive scan).
  - *Acceptance (PLAN.md Day 1):* `pytest -k ingest` on the 2 chosen statutes asserts
    `page_count` correct, `root.type` correct, `child_count > 0`, 100% leaves carry
    `page_start`/`page_end`, flat fixture gets `parse_confidence < 0.5` + fallback flag.

---

## Day 2 — Slice 2: Retrieval engine (BM25 + structural + reference, no LLM)

**Targets:** `apps/api/retrieval/`. **Depends on:** Day 1 + `check_no_vectors.py` green.

- [ ] SQLite tree store: `nodes` + `references`, indexes `(document_id, parent_id)`,
  `(document_id, type)`, FTS5 on `(title, text)`.
- [ ] Per-document BM25 index → `data/indexes/{doc_id}/bm25.json`.
- [ ] Identifier index: normalized labels → node ids (normalizer per `retrieval.md` §4;
  ranges expanded, canonical map, both sides normalized).
- [ ] Two-pass scoring: `lex`/`ident`/`title` (pass 1, freezes lex-top-5/top-3) →
  `ref`/`hier` (pass 2, computed against the frozen sets). Weights 1.0 defaults, range
  [0,2]; disjoint inputs enforced.
- [ ] 7 tool functions per `retrieval.md` §5 with binding caps: `search_nodes` (default
  10, hard max 20), `inspect_node` (≤8000 chars), `list_children` (max 50 rows, 200-char
  summaries), `get_parent`, `expand_context` (radius ≤ 2, ≤5 nodes, ≤2000 chars/node),
  `follow_reference` (typed `resolved|unresolved|error`; unresolved → move on, never
  invent), `get_source`. `select_evidence` retrieval-owned, called direct by the gate.
- [ ] `select_evidence()`: dedup → drop <50 chars → drop page-span-less → rank by score →
  keep top 8 → per-sub-question support check at **0.15**.
- [ ] Context Builder: `build_context(EvidenceSet) -> PromptContext`, 12k char budget,
  score-desc order (ties: shallower depth, then position), gate-set nodes never dropped,
  `context_truncated` flag on proportional truncation.
  - *Acceptance (PLAN.md Day 2 + CONSTRAINTS.md):* `test_context_builder_budget` passes
    (order/truncation/overflow hold). Golden 10-question smoke: every `gold_node` in
    top-5. `apps/api/retrieval/` has zero `cosine_similarity`/vector imports
    (script + grep both green).

---

## Day 3 — Slice 3: Agent loop (LangGraph + sufficiency gate)

**Targets:** `apps/api/agent/`. **Depends on:** Day 2. **Must NOT import** `sqlite3`,
`sqlalchemy`, `apps.api.models` (CONSTRAINTS.md, same script).

- [ ] LangGraph graph: `START → query_plan → search → inspect → (relevant? expand/follow :
  mark irrelevant + backtrack) → select_evidence → sufficiency_gate → (more? search again
  : build_context) → generate → END`.
- [ ] **7 thin wrappers** only (arg validation + call `retrieval.api` + trace logging).
- [ ] Max 12 tool iterations **including** the broaden-retry; retry does not reset.
- [ ] Sufficiency gate (binding): per-sub-question `score ≥ 0.15 AND (lex > 0 OR
  identifier-hit)`; every planned sub-question needs ≥1 supporting node (planner emitting
  0 is invalid → gate fails closed → retry plan once, then insufficient). One
  broaden-retry (stopword-strip + identifier-only, stoplist per `retrieval.md` §3),
  then `insufficient_evidence` exit with the unified abstention shape.
- [ ] Backtrack: `relevant: bool` on every `tool_result`; irrelevant branches contribute
  zero evidence, logged with branch id; visited-set prevents re-inspect loops.
  - *Acceptance (PLAN.md Day 3):* multi-hop fixture trace shows ≥ 1 abandoned branch
    (relevant=false, zero evidence) + ≥ 2 inspect/expand before the answer.
    `mypy apps/api` zero errors.

---

## Day 4 — Slice 4: Generation (LLMInterface + citations + refusal)

**Targets:** `apps/api/generation/`. **Depends on:** Days 2 + 3.

- [ ] `LLMInterface.generate()` + `.generate_stream()` (ADR-004): ollama default,
  `temperature=0`, seed pinned.
- [ ] Renders opaque `PromptContext` — never reorders or truncates evidence (that is the
  Context Builder's job; the contract test catches violations).
- [ ] Citation assembly: `citations[] = [{n, node_id, label, page_start, page_end}]`.
- [ ] Advice blocklist post-generation: rejects "you should", "I advise", "your case",
  "you must" (single oracle shared with `architecture.md` §6 / PRD US-2).
- [ ] Refusal path: gate failure → canned abstention string (pin the exact wording) +
  `citations: []` + `insufficient: true`. Never a fabricated answer.
  - *Acceptance (PLAN.md Day 4):* 5 gold questions pass an arm-blinded LLM judge with
    100% of factual claims cited; builder spot-checks all 5. Advice blocklist unit test
    green.

---

## Day 5 — Slice 5: Web three-pane explorer + SSE client

**Targets:** `apps/web/src/app/app/` (new route), new components. **Depends on:** Day 4.

- [ ] Three panes: tree / source / answer at `/app/*`.
- [ ] SSE event payload schemas spec'd in `api.md` **before** this slice (PLAN.md gap #18)
  and client built against the frozen sequence: `plan → tool_call → tool_result →
  (repeat) → evidence → token* → done | error`.
- [ ] Citation click → `GET /v1/nodes/{id}/source` → source pane renders
  `text + page + hierarchy_path`.
- [ ] Persistent disclaimer banner (answer pane) — worded exactly as `copy.ts` line 221;
  persists across answer interactions, does not scroll away.
- [ ] Explorer runtime boundary (`architecture.md` §7): calls the local API at
  `NEXT_PUBLIC_API_BASE_URL` (default `http://127.0.0.1:8000`) via `site.ts`; API
  unreachable → fail loud, never fabricate.
- [ ] Web conventions (`SPEC.md` §12): `copy.ts` is the only source of marketing copy; no
  inline strings in JSX; `@theme` tokens in `globals.css`; icons from
  `@phosphor-icons/react/ssr`; radius 0 / no shadows; ASCII hyphens only on the marketing
  route; `text-ink-5` never on readable strings; `accent-2` only in
  `comparison-arms.tsx`.
- [ ] `"use client"` boundary: SSE consumer + disclaimer state are client components;
  tree + source panes server-rendered. No blanket `"use client"` on the page.
  - *Acceptance (AGENTS.md pre-flight):* `npx tsc --noEmit -p tsconfig.json` clean;
    `npm run lint` zero warnings; `npm run build` clean. Browser test: SSE events render
    in order; citation click opens source; disclaimer stays visible during stream.
    Marketing-route pre-flight per `docs/landing-page/DESIGN.md` §13 if the landing page
    is touched.

---

## Day 6 — Slice 6: Evaluation framework (both arms + metrics)

**Targets:** `apps/api/evaluation/`, `experiments/vector/`. **Depends on:** D2, D3,
slices 1–5.

- [ ] `POST /v1/evaluations` → 202 `{evaluation_id, status}`; `GET /v1/evaluations/{id}` →
  per-question rows + aggregates + system stats per `api.md` §4.
- [ ] Frozen 72Q dataset at `data/datasets/v1/{dev,test}.json` (24 dev + 48 test, 7
  categories, ≥3 dev + ≥6 test per category, ≥5 `answerable: false`, seed 42).
- [ ] Arm A (Tuklas): full agent loop, slices 1–5.
- [ ] Arm B (Vector): dense retrieval under `experiments/vector/` only, embedding model
  from D3. Exposes the same retrieval result shape so "arms differ only in retrieval"
  holds (Arm-B parity boundary = PLAN.md risk D4).
- [ ] Versioned arm-blinded judge prompt → faithfulness 0/1 per claim; 20% spot-check
  (min 10 answers) by the builder; > 15% disagreement invalidates the run.
- [ ] `evaluation_runs` + per-question `retrieval_runs` persisted per question per arm
  (join keys `(evaluation_id, question_id, arm)`); weights + normalization snapshot in
  every `retrieval_runs` row.
  - *Acceptance (PLAN.md Day 6):* 10-question smoke completes; all rows carry node ids
    (except `insufficient: true` → `[]`); judge scores present. `check_no_vectors.py`
    green on the quarantine assertion.

---

## Day 7 — Requirements, CI, reproducibility

**Targets:** repo root (`pyproject.toml` + `poetry.lock` (Poetry), `.github/workflows/ci.yml`,
`scripts/check_eval_config.py`). **Depends on:** all slices green.

- [ ] `pyproject.toml` + `poetry.lock` pinned (Poetry): PyMuPDF, rank-bm25 (or FTS5-only), rapidfuzz, langgraph,
  pydantic, fastapi, uvicorn, diff-cover, and the D3 embedding package.
- [ ] `scripts/check_no_vectors.py` + `scripts/check_eval_config.py` finalized
  (Day 1 + Day 7 deliverables per CONSTRAINTS.md).
- [ ] `.github/workflows/ci.yml`: mypy on `apps/api` (zero errors) → ruff on `apps/api`
  (zero errors) → tsc + eslint on `apps/web` (zero errors) → gitleaks (zero findings) →
  both check scripts (exit 0) → pytest green → diff-cover ≥ 80% on changed lines.
- [ ] **Ratchet:** first benchmark run on dev-24 sets `Recall@5`, `MRR@10`, p95 latency in
  CONSTRAINTS.md "Measured, not yet enforced". Update that table; ship no unmeasured
  numbers.
  - *Acceptance (PLAN.md Day 7):* full CI green. Re-run with same config id → retrieval
    metrics reproduce exactly, generation within ±5pp.

---

## What MUST be true before the first benchmark run is reportable

From the eval-readiness audit — a run violating any of these **completes but cannot be
published honestly** (`SPEC.md` §9b):

1. [ ] Corpus ingested (D2 resolved) → questions + nodes exist.
2. [ ] Every `gold_node` resolves to a real ingested node id (D1) — no missing or prefix-mismatched gold (`node_{question.document}_`).
3. [ ] Judge blinded; prompt versioned; disagreement computable (≤ 15%).
4. [ ] `evaluation_runs`/`retrieval_runs` rows persisted per question per arm.
5. [ ] Model parity check green (same model + backend + temperature(0) + seed across arms).
6. [ ] Quarantine scan green (`check_no_vectors.py` exit 0).
7. [ ] Requirements manifest installs the full toolchain (incl. the RapidFuzz scorer).
8. [ ] Node id format match re-verified on the frozen dataset (seed-data test green) — uniqueness or determinism broken would fail it.
