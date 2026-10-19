# Tuklas Agent — Implementation Plan (Day 0–7)

**Status:** Derived from the audited spec suite (PRD, architecture, retrieval, evaluation, api, ADR-001–004, CONSTRAINTS).
**Source of truth for this file:** the docs above + three parallel audits (spec-coherence, web-completeness, eval-readiness). Cross each item against them.
**Authoritative precedence when docs conflict:** PRD (requirements) → api.md (contracts) → architecture.md (module boundaries + build order) → retrieval.md → evaluation.md → ADRs → CONSTRAINTS (floor).

This plan exists because the spec says it should: `architecture.md §10` defers `tasks/` planning "after doc approval"; `lifecycle-strategy-note.md` defers execution planning to "after doc approval." Docs are approved. This is that planning step.

---

## 0. Pre-flight: spec reconciliation (Day 0, morning)

The spec has defects a builder would inherit if uncaught. These are single-line fixes, not design changes. **Do not start any slice until Day 0 closes.**

### CRITICAL

| # | Fix | Location | Authoritative value |
|---|-----|----------|---------------------|
| 0.1 | Threshold `0.20` → `0.15` | `retrieval.md §5` l62: "per-sub-question support check (threshold **0.15**)" | architecture.md §4 ("0.15") and PRD §7 US-2 ("0.15") — both defer to architecture. retrieval.md §5 is the outlier |
| 0.2 | Tool count → clarify the 7 vs 8 split | Add one line to `architecture.md §4`: "The 7 tools are the agent-facing tool set; `select_evidence()` is owned by `retrieval/` and called directly by the sufficiency gate (not a tool wrapper)" | retrieval.md §5 lists 8 functions; PRD/architecture say 7 — the distinction is the direct-call path, never stated |
| 0.3 | `cosine (` → `cosine` (drop the space) | `architecture.md §2` l38: "cosine_similarity|cosine\b" | the space-grep gap (SpecCoherence finding #24) lets `cosine(` slip past |
| 0.4 | `§14` → `§8` | `lifecycle-strategy-note.md` l48 | PRD has no §14; §8 is "Out of Scope" |

### HIGH

| # | Fix | Location | Owner / decision |
|---|-----|----------|------------------|
| 0.5 | Quarantine scope `api/` gap | `architecture.md §2` l37 + `CONSTRAINTS.md` l14: add `apps/api/api/` to the deny list | architect |
| 0.6 | `models/` module ownership | Add a row to `architecture.md §2` table for `models/` (allowed imports, constraints) | architect |
| 0.7 | Local-first enforcement | Add a CI check (gitleaks config or a path-allowlist sniff) — or document it as ADR-gated only | owner TBD |

### MEDIUM (document, don't code)

| # | Fix | Location |
|---|-----|----------|
| 0.8 | Node id format → pick one and write it down | `architecture.md §3` (tree builder) + `evaluation.md §2` (gold_nodes) |
| 0.9 | SSE event payloads → schema per event | `api.md §3` (after the sequence pin) |
| 0.10 | Canned abstention string → pin it | `api.md §3` / `PRD US-2` |
| 0.11 | `disagreement > 15%` metric → define | `evaluation.md §4` |
| 0.12 | `citation accuracy` "support" → define | `evaluation.md §4` |
| 0.13 | `evaluation_runs` DDL → match §6 fields | `architecture.md §8` |
| 0.14 | `retrieval_runs.normalization` → JSON or id (pick one) | `architecture.md §8` / `evaluation.md §6` |
| 0.15 | `backends.models` → document in COPY.md | `docs/landing-page/COPY.md` Backends section |

> **The single highest-risk latent bug (P0.3 from the eval audit):** `gold_nodes` in the dataset must be resolvable to real node ids in the ingested tree. If this isn't decided now, the first benchmark completes with `Recall@5 = 0` for every question — plausible-looking, structurally zero, no gate catches it. Day 0.8 pins it.

---

## 1. Root blocker: corpus decision (Day 0)

`architecture.md §10` build order starts with Ingestion (slice 1). But Ingestion has no input until the corpus is chosen — `PRD §10 Q1` ("which public statute(s) + who writes the gold questions?") is the root of the entire dependency tree. Every auditor flagged this.

**Decision required before Day 1:**
- Pick 1–2 public statutes (legal-safe, ≥ 100 pages, hierarchical Book/Title/Chapter/Article structure — the parser is built FOR this shape; feeding it a flat document is wasted work).
- Confirm they are **public domain** or permissively licensed (no rights risk to the benchmark).
- Assign an owner to write the 72Q dataset (24 dev + 48 test, 7 categories, ≥ 3 dev + ≥ 6 test per category, ≥ 5 `answerable: false`).

**Acceptable candidate corpus (verified public, hierarchical, ≥100pp):**
- U.S. Civil Rights Act (public domain federal law; hierarchical Title→Section). Source: GPO.
- California Civil Code (public domain state statute; Book→Title→Chapter→Section). Source: state legislature site.
- Model Penal Code (public domain; Article-based). Source: ALI.

**Do NOT proceed with slice 1 until this is pinned.** The test fixtures (PRD §7 US-1: "pytest -k ingest on 2 public statutes") cannot be written without it.

---

## 2. Day 1–7: six vertical slices

Each slice = build + test + gate. Slices ship independently. Slices 1–5 are the product; slice 6 is the measurement apparatus. **The web page (slice 5 partial) is already done as the marketing route; only the `/app/*` three-pane explorer + SSE client remain, and that's blocked behind slice 4.**

### Day 1 — Slice 1: Ingestion (PDF→pages→tree)

**Targets:** `apps/api/ingestion/`
- `POST /v1/documents` (multipart PDF) → 201, 422/409/500 per api.md §1/§2.
- PyMuPDF → pages → text + page numbers. ≥ 50 chars/page else counts as empty; > 30% empty → 422 `OCR_REQUIRED`. Encrypted → 422 `PARSE_FAILED`.
- SHA-256 dedup → 409 on identical hash.
- Structure parser: regex + RapidFuzz heading match → `nodes` (id, document_id, parent_id, type, title, text, page_start, page_end, depth, position) + `references`. `parse_confidence` = fraction of pages with ≥ 1 heading. `< 0.5` → flat fallback (doc → page nodes), flag stored + returned.
- **Node id format (Day 0.8 decision must be locked):** e.g. `n_<slug>_<number>`. Whatever is chosen, it must match what `gold_nodes` in the dataset uses.

**Verification:**
- `pytest -k ingest` on the 2 chosen statutes: `page_count == expected`, `len(root.children) > 0`, 100% of leaves have `page_start` + `page_end`, flat fixture gets `parse_confidence < 0.5` + `fallback` flag.
- `scripts/check_no_vectors.py` is **written today** (Day 3 deliverable, per CONSTRAINTS table) and is GREEN on this slice. Without it, no retrieval code is allowed to merge.

**P0 gate:** Day 0.8 (node id format) must be decided before this slice's tests can assert `gold_nodes`-style lookups.

### Day 2 — Slice 2: Retrieval engine (no LLM)

**Targets:** `apps/api/retrieval/`
- SQLite tree store: `nodes` + `references`, indexes `(document_id, parent_id)`, `(document_id, type)`, FTS5 on `(title, text)`.
- Per-document BM25 index → `data/indexes/{doc_id}/bm25.json`.
- Identifier index: normalized labels → node ids (normalizer per retrieval.md §4).
- **Sufficiency gate = 0.15** (after Day 0.1 fix): per-sub-question `score ≥ 0.15 AND (lex > 0 OR identifier-hit)`.
- 7 tool functions (the 8th, `select_evidence`, is called direct by the agent gate per Day 0.2): `search_nodes`, `inspect_node`, `list_children`, `get_parent`, `expand_context`, `follow_reference`, `get_source`. Caps per architecture.md §4 l70.
- `select_evidence()` (retrieval-owned): dedup → drop < 50 chars → drop page-span-less → rank by score → top 8 → per-sub-question support check at 0.15.
- Context Builder: `build_context(EvidenceSet) -> PromptContext`, 12k char budget, score-desc order, gate-set nodes never dropped, truncation flag sets `context_truncated`.

**Verification:**
- Unit tests on the 10-question golden smoke set (retrieval.md §8): every `gold_node` in top-5.
- `check_no_vectors.py` green — this slice is the primary quarantine boundary.

### Day 3 — Slice 3: Agent loop (LangGraph + sufficiency gate)

**Targets:** `apps/api/agent/`
- LangGraph graph: `plan → search → inspect → expand → select_evidence → sufficiency_gate → build_context → generate`. Max 12 iterations.
- 7 thin tool **wrappers** (arg validation + call into `retrieval.api` + trace logging).
- **Must NOT import** `sqlite3, sqlalchemy, apps.api.models` (CONSTRAINTS.md l15). All node access via the 7 tools only.
- Backtrack: `tool_result` carries `relevant: bool`; irrelevant branches logged with branch id, zero evidence.
- Broaden-retry: stopword-strip + identifier-only query (stoplist per retrieval.md §3), consumes iteration budget.
- **SSE emission prep:** the graph state carries the event trace (`plan, tool_call, tool_result, evidence, token, done`) so slice 4 can stream it. Payload schemas from Day 0.9.

**Verification:**
- Multi-hop fixture trace: ≥ 1 abandoned branch + ≥ 2 inspect/expand before answer.
- mypy clean, ruff clean on `apps/api/agent/`.

### Day 4 — Slice 4: Generation (LLMInterface + citations + refusal)

**Targets:** `apps/api/generation/`
- `LLMInterface.generate()` + `.generate_stream()` (ADR-004: ollama default, `temperature=0`, `seed` pinned).
- Renders opaque `PromptContext`; never reorders/truncates evidence (that's `retrieval`'s job — Day 0.12 `context_truncated` contract tested by `test_context_builder_budget`).
- Citation assembly: `citations[] = [{n, node_id, label, page_start, page_end}]`.
- Advice blocklist post-generation (rejects "you should", "I advise", "your case", "you must").
- Refusal path: `insufficient_evidence` → canned abstention string (Day 0.10 decision) + `citations: []` + `insufficient: true`.

**Verification:**
- 5 gold questions: LLM judge (blinded) confirms 100% of factual claims cited; builder spot-checks all 5.
- Advice blocklist unit test: every banned phrase is rejected.
- `test_ask_stream_order` (CONSTRAINTS l32) — contract test for zero-tool-call + error-terminal cases.

### Day 5 — Slice 5: Web three-pane explorer + SSE client

**Targets:** `apps/web/src/app/app/` (new route), new components
- Three panes: tree / source / answer. `POST /v1/ask/stream` SSE client (EventStream decoding the plan→tool_call→tool_result→evidence→token→done sequence from Day 0.9 schemas).
- Citation click → `GET /v1/nodes/{id}/source` → renders `text + page_start + hierarchy_path` in the source pane.
- **Persistent disclaimer banner** in the answer pane — worded exactly from `copy.ts` (already exists there). Must persist across interactions, not scroll with the marketing sections.

**Verification:**
- Browser test: SSE events render in the correct order; citation click opens source; disclaimer stays visible during answer streaming.
- tsc + eslint clean in `apps/web`.
- `npm run build` clean (marketing site must not regress).

### Day 6 — Slice 6: Evaluation framework

**Targets:** `apps/api/evaluation/` + `experiments/vector/` (dense baseline, the ONLY legal importer)

**Arm B dependency:** the embedding model is **not named** in any doc (SpecCoherence finding: ADR-004 pins generation model, not embedding model). This must be decided Day 0 or Day 6 — pick a small, fast, CPU-runnable dense model (e.g. sentence-transformers/all-MiniLM-L6-v2) and pin it to ADR-002.

- `POST /v1/evaluations` → 202 `{ evaluation_id, status }`.
- Runner: same question set → Arm A (Tuklas agent loop, slices 1-4) + Arm B (vector baseline, slice 2 logic + dense retrieval) → writes `evaluation_runs` + one `retrieval_runs` per question per arm.
- Identical generation model/backend/temperature=0/seed across arms (ADR-004 parity — enforced by `check_eval_config.py`, Day 7).
- Judge: versioned blinded prompt (P3.1 — the prompt text must be authored now) → faithfulness per sentence claim 0/1. 20% spot-check by builder; >15% disagreement → invalid.
- Scorer: RapidFuzz `token_set_ratio ≥ 85` (P3.2 — rapidfuzz pinned, requirements manifest must exist).
- **Arm B asymmetry guard (P2.10):** Arm B must NOT run the LangGraph loop — it uses the same 7 retrieval tools but without agent mediation, OR the spec explicitly acknowledges the asymmetry and the comparison is read as "agent-mediated vs. single-shot." Document the decision.

**Verification:**
- 10-question smoke subset: completes, all rows have node ids (except `insufficient: true` rows), judge scores present.
- `check_eval_config.py` green on a sample config.

### Day 7 — Requirements, CI, reproducibility

- `requirements.txt` (or `pyproject.toml`) pinning: PyMuPDF, rank-bm25 (or FTS5-only), rapidfuzz, langgraph, pydantic, fastapi, uvicorn, gitleaks.
- `scripts/check_no_vectors.py` + `scripts/check_eval_config.py` finalized.
- `.github/workflows/ci.yml`: mypy + ruff + pytest + gitleaks + both check scripts — ALL gates from CONSTRAINTS.md enforced-with-numbers.
- Re-run: same config → same retrieval metrics exactly, generation within ±5pp.
- First benchmark run on the dev split (24Q) sets the ratchet values in `CONSTRAINTS.md` Measured table.

**Verification:** full CI green. `diff-cover --fail-under=80` green on diff.

---

## 3. What must NOT happen before Day 0 closes

These are the spec's own guards against building fiction:

| Item | Why it's blocked | Unblocks at |
|------|-----------------|-------------|
| Node id format (`architecture.md §8` DDL says `id TEXT PK` but never the format; `evaluation.md` gold_nodes references `article_1318`) | Silent systematic zero-recall — `gold_nodes` won't match tree ids | Day 0.8 decision |
| Corpus choice (PRD §10 Q1, EvalAudit P0.1) | Nothing to ingest, nothing to retrieve, nothing to score | Day 0 |
| Embedding model for Arm B (ADR-004 pins generation model only) | Arm B can't be built; parity check can't be written | Day 0 or Day 6 decision |
| Judge prompt text (evaluation.md §4 protocol, no text) | Faithfulness metric undefined | Day 5 or Day 6 |
| `requirements.txt` (no manifest exists) | Can't install PyMuPDF/rank-bm25/rapidfuzz; can't write tests | Day 6 |

---

## 4. Decision log (carry these forward)

| ID | Decision | Status |
|----|----------|--------|
| D1 | Node id format: `[decide — must match gold_nodes]` | OPEN (Day 0.8) |
| D2 | Corpus: `[decide 1–2 public statutes]` | OPEN (Day 0) |
| D3 | Arm B embedding model: `[decide CPU-runnable dense model]` | OPEN (Day 0/Day 6) |
| D4 | Arm A vs Arm B agent mediation asymmetry | OPEN (Day 6) |
| D5 | SSE event payload schemas (after sequence pin in api.md) | OPEN (Day 0.9) |
| D6 | Canned abstention string (exact wording) | OPEN (Day 0.10) |
| D7 | `disagreement > 15%` metric definition | OPEN (Day 0.11) |
| D8 | `citation accuracy` "support" definition | OPEN (Day 0.12) |

> These are not new requirements — they are unresolved items already scattered across the spec that would otherwise silently become builder assumptions and then latent bugs. Each links to the exact doc that carries the obligation.
