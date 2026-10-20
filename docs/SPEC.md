# Spec: Tuklas Agent — Vectorless Retrieval for Private Law Documents

**Status:** Consolidated frozen contracts for builders, derived from the audited spec suite
(`PRD.md`, `architecture.md`, `retrieval.md`, `api.md`, `evaluation.md`, the ADRs) and the
Day 0 reconciliation already committed in `PLAN.md`. **Read this before writing any code.**
Where any other doc and this file disagree, the source doc named in each section wins; this
file is the one-stop index of the frozen surface.

It does not re-author requirements. The research question lives in `PRD.md`; module
boundaries and build order live in `architecture.md` and `PLAN.md`.

---

## 1. System overview

```text
Next.js (apps/web) ──REST/SSE──▶ FastAPI (apps/api, prefix /v1)
                                      │
        ┌─────────────────────────────┼─────────────────────────────┐
        ▼                             ▼                             ▼
 Document Engine               Tuklas Agent                   LLM Engine
 PyMuPDF → pages →         LangGraph graph                LLMInterface
 structure parser →        (plan/search/inspect/          (generate + generate_stream;
 Document Tree ──┐         expand/sufficiency)            ollama default per ADR-004)
                 │                │
                 ▼                ▼
        Custom Retrieval Engine ◀── 7 tools (ONLY tree access)
        BM25 │ structural │ reference   (ranking + select_evidence live HERE)
                 │
                 ▼
        EvidenceSet → Context Builder (owned by retrieval/)
        → Generation (renders PromptContext opaquely) → Answer + Citations
```

Storage (MVP): **SQLite + filesystem** (ADR-002). Tables: `documents`, `nodes`,
`references`, `queries`, `retrieval_runs`, `evaluation_runs`. Files: PDFs + extracted
artifacts + BM25 indexes. No Postgres, no vector DB in v1.

Repo layout: `apps/web/`, `apps/api/{api,ingestion,retrieval,agent,generation,evaluation,models}/`,
`data/{documents,indexes,datasets}/`, `experiments/{vector,vectorless,results}/`, `docs/`,
`scripts/`.

**Agent graph (architecture.md §4, authoritative):**
`START → query_plan (emits 1–4 sub_questions) → search → inspect →
(relevant? expand/follow : mark irrelevant + backtrack) → select_evidence
(retrieval-owned) → sufficiency_gate → (more? search again : build_context) →
generate → END`.

LangGraph owns: state object, node execution, conditional edges, loop + max-iteration
guard, checkpointing (SQLite checkpointer), step-event streaming. LangGraph is plumbing;
the hierarchical/vectorless retrieval engine is the contribution (ADR-001).

---

## 2. Module boundaries (mechanically enforced)

Deny-import list for the primary path (exact dotted paths, CI-scanned per CONSTRAINTS.md;
checked by `scripts/check_no_vectors.py`, Day 1 deliverable):

```text
DENY_PRIMARY = {sentence_transformers, faiss, chromadb, qdrant_client, pgvector}
  applies to: apps/api/{ingestion,retrieval,agent,generation,api}/
+ grep rule: `cosine_similarity|cosine\s*\(` must not appear in `apps/api/retrieval/`
```

`experiments/vector/` is imported only by `apps/api/evaluation/` (SOLE legal importer).

| Module | Owns | May import | MUST NOT import |
|--------|------|-----------|-----------------|
| `ingestion/` | PDF→pages→text; structure parse → tree; page spans; `parse_confidence` | `models`, PyMuPDF, regex, RapidFuzz | `retrieval`, `agent`, anything in DENY_PRIMARY |
| `retrieval/` | BM25 index, ALL scoring/ranking, `select_evidence()`, Context Builder, reference resolution | `models`, `rank-bm25`/FTS5, RapidFuzz | `agent`, `langgraph*`, DENY_PRIMARY |
| `agent/` | LangGraph graph, 7 tool *wrappers* (arg validation + call into `retrieval.api` + trace logging), sufficiency gate *evaluation* (calls `retrieval.select_evidence`) | `retrieval.api` (tool impls ONLY), `langgraph*`, pydantic, `generation` (final call only) | `sqlite3`, `sqlalchemy`, `apps.api.models`, DENY_PRIMARY — no direct tree/DB reads; all node access via the 7 tools |
| `generation/` | prompt rendering from opaque `PromptContext`, `LLMInterface` calls, citation assembly, advice blocklist | `models`, LLM backends | `retrieval` internals, `sqlite3`, reordering/truncating evidence |
| `evaluation/` | dataset runner, both arms, metrics, judges | everything incl. `experiments/vector/` (SOLE legal importer) | — |
| `experiments/vector/` | dense baseline | — | imported by nothing except `evaluation` |

**Context Builder** (owned by `retrieval/`): `build_context(evidence: EvidenceSet) -> PromptContext`
with fixed rules (architecture.md §2): order by score desc (ties: shallower depth first,
then position asc); budget 12k chars total; drop lowest-score-first; gate-set nodes are
never dropped — if they exceed 12k, truncate each proportionally and set
`context_truncated: true` on the trace. `generation/` renders `PromptContext` opaquely —
any reordering there is a review failure, caught by the truncation contract test
(`test_context_builder_budget`, CONSTRAINTS.md).

---

## 3. Data model (DDL sketch, architecture.md §8 — weights columns included)

```sql
documents(id TEXT PK, slug TEXT UNIQUE, name TEXT, file_path TEXT, file_hash TEXT UNIQUE,
          page_count INT, parse_confidence REAL, created_at TEXT);
nodes(id TEXT PK, document_id TEXT REFERENCES documents(id), parent_id TEXT,
      type TEXT, title TEXT, text TEXT,
      page_start INT, page_end INT, depth INT, position INT);
references(id TEXT PK, source_node_id TEXT, target_node_id TEXT NULL, reference_text TEXT);
queries(id TEXT PK, document_id TEXT, question TEXT, created_at TEXT);
retrieval_runs(id TEXT PK, query_id TEXT, algorithm TEXT, -- 'tuklas' | 'vector'
               latency_ms INT, retrieved_nodes TEXT /* JSON ids */,
               weights TEXT /* JSON snapshot */, normalization TEXT /* JSON */,
               created_at TEXT);
evaluation_runs(id TEXT PK, dataset TEXT, config TEXT /* JSON: code_version, weights,
               model, backend, judge_prompt_version, seeds, K */,
               metrics TEXT /* JSON */, created_at TEXT);
```

Indexes: `nodes(document_id, parent_id)`, `nodes(document_id, type)`, FTS5 on `(title, text)`.

**Id formats (api.md §1):** `doc_<hash8>`, `node_<slug>`, `q_<uuid8>`, `eval_<uuid8>`.
The `node_<slug>` format is defined by decision D1 (resolved 2026-10-19): `<slug>` = `{doc_slug}_{type}_{locator}` per `architecture.md` §3 — the contract that makes `gold_nodes` in the frozen dataset resolve to real node ids.

---

## 4. Document Engine (architecture.md §3)

Pipeline: `PDF → PyMuPDF pages → text+metadata → structure parser (regex + RapidFuzz
heading match) → tree → SQLite nodes + references`.

- Node fields: `id, document_id, parent_id, type, title, text, page_start, page_end,
  depth, position`. `references(id, source_node_id, target_node_id NULL-able,
  reference_text)`.
- `parse_confidence ∈ [0,1]` = fraction of pages with ≥1 heading match. **Flat-fallback
  predicate:** `parse_confidence < 0.5` → emit flat tree (document → page nodes, depth
  0/1). (10–30% empty pages with otherwise good headings still parses structurally; >30%
  empty is always 422 `OCR_REQUIRED` — the two regions never overlap.) Flag stored on
  document, returned by `GET /documents/{id}`, badged in UI. Never a confidently wrong
  deep tree.
- Dedup: SHA-256 `file_hash`; identical bytes → 409.
- MVP limits: text-layer PDFs only, ≤ 500 pages, ≤ 100 MB. Empty-page = <50 chars; >30%
  empty pages → 422 `OCR_REQUIRED`. Encrypted → 422.

---

## 5. Retrieval Engine (retrieval.md §2–§4)

- **Tree store (SQLite):** `nodes` + `references` per architecture.md §8. Indexes:
  `(document_id, parent_id)`, `(document_id, type)`, FTS5 on `(title, text)`.
- Per-document BM25 index → `data/indexes/{doc_id}/bm25.json`.
- **Reference graph:** `references` edges from regex + RapidFuzz title fallback;
  unresolved targets stored with `target_node_id NULL`.
- **Scoring:** `score = w_lex*lex + w_ident*ident + w_title*title + w_ref*ref + w_hier*hier`,
  disjoint inputs (lex sees body text only; title sees headings only; ident sees identifier
  tokens only — a heading term fires in exactly one component).
  - `lex`: BM25 over body, min-max normalized per query → [0,1].
  - `ident`: 1.0 exact normalized article id, 0.5 chapter/title-level id, else 0.
  - `title`: heading token overlap (Jaccard) → [0,1].
  - `ref`: 1.0 if reachable within 2 hops from a pass-1 lex-top-5 node, else 0.
  - `hier`: 1.0 if sharing a parent with a pass-1 lex-top-3 node, else 0.
  - Two-pass order: pass 1 computes lex/ident/title and freezes lex-top-5/top-3; pass 2
    computes ref/hier against those frozen sets. All-zero lex → ref/hier = 0 and the
    broaden-retry fires.
- **Defaults (starting points, NOT optima):** all weights 1.0; phrase/fuzzy bonuses off by
  default. Tunable ranges: weights ∈ [0,2], fuzzy threshold ∈ [80,95]. Tuning on dev split
  only; winning config frozen into the eval run record; test split touched once.
- **Stoplist (broaden-retry):** SQLite FTS5 default English stopwords +
  `{said, provision, provides, article, section}` — versioned in code.
- **Identifier normalization (required):** lowercase → strip punctuation → canonical map
  (`article|art\.?|arts\.? → art`, `section|sec\.? → sec`, `chapter|ch\.? → ch`,
  `title → title`, `book → book`); digits preserved; ranges expanded (`1318–1320` →
  1318,1319,1320). Query and node labels both normalized before matching.
- **7 tool functions** (retrieval.md §5, the agent's ONLY tree access):
  `search_nodes` (default 10, hard max 20), `inspect_node` (single node, ≤8000 chars),
  `list_children` (max 50 rows, 200-char summaries), `get_parent` (single node),
  `expand_context` (radius ≤ 2, ≤5 nodes, ≤2000 chars/node), `follow_reference` (single
  node or typed miss: `resolved|unresolved|error`; unresolved → agent must move on, not
  invent), `get_source` (single node text + pages + path). `select_evidence` is
  retrieval-owned, called direct by the gate, not a wrapper: dedup → drop <50 chars →
  drop page-span-less → rank by score → keep top 8 → per-sub-question support check at
  threshold **0.15**.
- Flat-fallback behavior: `ident/title/hier = 0`; `lex` runs over page-node texts; `ref`
  only on resolved edges; trace carries `fallback: true`.

---

## 6. Agent (LangGraph — orchestration only, architecture.md §4)

Graph: `START → query_plan (emits 1–4 sub_questions) → search → inspect →
(relevant? expand/follow : mark irrelevant + backtrack) → select_evidence →
sufficiency_gate → (more? search again : build_context) → generate → END`.

- **7 thin wrappers** only (arg validation + call `retrieval.api` + trace logging).
- **Budget:** max 12 tool iterations per ask, **including** the one broaden-retry. Retry
  does not reset the counter.
- **Sufficiency gate (binding numbers):** node supports sub-question S iff
  `score ≥ 0.15 AND (lex > 0 OR identifier-hit)`. Gate passes iff every planned
  sub-question (planner must emit ≥1; zero is invalid) has ≥1 supporting node in the kept
  set. Else one broaden-retry (stopword-strip + identifier-only query, stoplist per
  retrieval.md §3, consumes iteration budget), then `insufficient_evidence` exit with the
  unified abstention shape (api.md §3).
  - *Who applies the threshold:* retrieval owns it. `select_evidence` returns
    `gate.per_question` (the supporter list per sub-question); the agent reads that
    verdict and decides pass/fail only. The agent never re-scores a hit — two
    implementations of `score ≥ 0.15 AND (lex > 0 OR identifier-hit)` would be two
    thresholds to drift apart.
  - *What insufficiency means:* the gate verdict, NOT "are there kept nodes". A query
    with no lexical match can still leave score-0 nodes in the kept set; answering from
    those would fabricate. The abstention therefore keys on the gate verdict.
- **Backtrack:** `tool_result` carries `relevant: bool`; irrelevant branches contribute
  zero evidence nodes, logged with branch id. Visited-set prevents re-inspect loops.
  - *What makes a branch relevant:* the node it returned could itself contribute
    evidence — ≥50 chars of text AND a page span, the same two rules
    `select_evidence` applies. A heading-only node is a dead branch, not evidence.
- Zero sub-questions from planner → invalid plan; gate fails closed → retry plan once,
  then insufficient. An invalid plan does NOT get a broaden-retry: a broaden fixes a
  retrieval miss, not an invalid plan.
- The abstention string is pinned in api.md §3; the SSE payload schemas are pinned
  there too (PLAN.md Day 5 gap #18, closed by the Day-3 slice).

---

## 7. Generation (architecture.md §6)

- `LLMInterface` (`generate` + `generate_stream`, ADR-004) receives opaque `PromptContext`;
  renders it without reordering or truncation (Context Builder's job; contract test
  catches violations).
- System constraints: answer only from evidence; every factual sentence carries `[n]`;
  refusal path when the gate fails (zero kept evidence nodes or best kept score < 0.15,
  PRD US-2) → canned abstention + `citations: []` + `insufficient: true`. Never a
  fabricated number.
- Citation assembly: `citations[] = [{n, node_id, label, page_start, page_end}]`.
- Post-generation blocklist rejects advice phrasing: "you should", "I advise",
  "your case", "you must" (single oracle, shared with architecture.md §6 and PRD US-2).
- Persistent disclaimer (answer-pane banner), worded exactly as `apps/web/src/lib/copy.ts`
  line 221: "Tuklas is a research discovery tool, not a lawyer. Verify provisions before
  relying on them." (sourced from `docs/landing-page/COPY.md`).

---

## 8. API endpoints (`/v1`, frozen v1 — additive-only, api.md)

```text
POST /v1/documents (multipart: file=*.pdf)
  → 201 { id, name, page_count, parse_confidence, tree_root }
  → 422 VALIDATION_ERROR | OCR_REQUIRED | PARSE_FAILED; 409 CONFLICT (dup hash)
GET /v1/documents?page=&pageSize= → { data: [{id,name,page_count,created_at}], pagination }
GET /v1/documents/{id} → { id, name, file_hash, page_count, parse_confidence, created_at } | 404
GET /v1/documents/{id}/tree?depth=3 → { root, children: [{id,type,title,depth,position,page_start,child_count}] } | 404
POST /v1/search { document_id, query, top_k?: int =10 (1..20) }
  → 200 { hits: [{node_id,title,snippet,score,components,page_start}], trace_id }
  → 404 | 422
POST /v1/ask { document_id, question, max_iterations?: int =12 (1..12) }
  → 200 AskResponse:
      { answer_markdown, citations: [{n,node_id,label,page_start,page_end}],
        evidence_ids, iterations, latency_ms, tokens: {in,out}, insufficient }
      # insufficient=false on success; abstention returns the SAME shape with
      # answer_markdown=canned text, citations=[], evidence_ids=[], insufficient=true
  → 404 | 422 | 503 LLM_UNAVAILABLE
POST /v1/ask/stream (same body, SSE) — frozen v1 event sequence:
  plan{sub_questions} → tool_call{tool,args} → tool_result{tool,result_ids,relevant} →
  (repeat) → evidence{evidence_ids, insufficient} → token{text}* → done{ask_response} | error{error}
  # zero-tool-call runs legal: plan → evidence(empty) → done(insufficient ask_response).
GET /v1/nodes/{id} → NodeFull + hierarchy_path + outgoing refs | 404
GET /v1/nodes/{id}/source → { text, page_start, page_end, hierarchy_path } | 404
POST /v1/evaluations { dataset_snapshot, arms: ["tuklas","vector"], k: [1,3,5,10], config: EvalConfig }
  EvalConfig { code_version, weights, normalization, fuzzy_threshold: int (80..95),
    bonus_flags: { phrase: bool, fuzzy: bool }, stoplist_version, model, backend,
    temperature: 0 (fixed), seed: int, judge_prompt_version, margins: {recall5, faithfulness, max_category_gap} }
  → 202 { evaluation_id, status: "queued" }  # queued|running|done|failed
  → 422 (unknown snapshot, arm mismatch, temperature ≠ 0) | 500 EVAL_FAILED
GET /v1/evaluations/{id} → { status, config, per_question, aggregates, system_stats } | 404
```

**Error shape (ALL endpoints, including SSE terminal errors):**
`{ "error": { "code": "...", "message": "...", "details": {} } }`.

| Code | Status | When |
|------|--------|------|
| `VALIDATION_ERROR` | 422 | bad body, non-PDF, oversize (>100MB), >500 pages |
| `OCR_REQUIRED` | 422 | >30% pages with <50 chars extracted |
| `PARSE_FAILED` | 422 | PyMuPDF cannot open/extract (corrupt, encrypted-unreadable) |
| `NOT_FOUND` | 404 | unknown document/node/evaluation id |
| `CONFLICT` | 409 | identical `file_hash` re-upload |
| `LLM_UNAVAILABLE` | 503 | backend down/timeout |
| `EVAL_FAILED` | 500 | runner crash, invalid config |

Additive-only under `/v1` (new optional fields); renames/removals → `/v2` + ADR, enforced
by the breaking-change schema-snapshot test (`test_api_v1_snapshot`, CONSTRAINTS.md).

---

## 9. Success criteria

1. Landing page at `/` builds clean and deploys as the zero-runtime static export
   (architecture.md §7 deploy boundary); the research UI at `/app/*` calls the local API
   at `NEXT_PUBLIC_API_BASE_URL` (default `http://127.0.0.1:8000`).
2. The frozen 72Q dataset (24 dev + 48 test, 7 categories, ≥3 dev + ≥6 test per category,
   ≥5 `answerable: false`, seed 42) completes for both arms; per-question rows +
   aggregates returned via `GET /v1/evaluations/{id}`.
3. `Recall@5` (primary), `MRR@10`, faithfulness, latency, tokens — measured and reported,
   never estimated. K values reported: 1/3/5/10.
4. Model parity enforced: same model + backend + temperature(0) + seed across arms
   (`scripts/check_eval_config.py`, Day 7 deliverable).
5. `scripts/check_no_vectors.py` exits 0 (quarantine holds).
6. `pytest -q` green; `mypy apps/api` zero errors; `ruff check apps/api` zero errors;
   `tsc --noEmit -p apps/web` + `eslint apps/web` zero errors; `gitleaks detect --redact
   --no-banner` zero findings; diff-cover ≥ 80% on changed lines (all per CONSTRAINTS.md).
7. No superiority claims anywhere — the outcome of the vectorless-vs-vector comparison is
   unknown until the first benchmark run; CONSTRAINTS.md "Measured, not yet enforced"
   table stays `unmeasured` until the first dev-24 run sets the ratchet.

---

## 9b. Interop readiness gates (eval)

From the eval-readiness audit (PLAN.md): a run violating any of these completes but cannot
be published honestly.

1. No corpus (D2 unresolved) → no questions, no nodes.
2. `gold_nodes` don't resolve to real ids (D1) → Recall@5 = 0 silently, everything else
   looks valid.
3. Judge not blinded / disagreement > 15% uncomputable (no prompt text) → faithfulness is
   opinion, not measurement.
4. No `evaluation_runs`/`retrieval_runs` rows → nothing to GET, nothing to audit.
5. No model parity check → invalid run (different models across arms) passes CI.
6. No quarantine scan → "vectorless" claim unverifiable; Arm A could secretly use
   embeddings.
7. No pinned dependency manifest (poetry.lock) → scorer (RapidFuzz) can't be installed; the 85-threshold is
   aspirational.
8. Node id format mismatch (D1 again — most dangerous item) → all Recall/MRR structurally
   zero.

---

## 10. Open decisions (block Day 1)

- **D1 — Node id format.** ~~`gold_nodes` in the frozen dataset (e.g. `article_1318`) must
  resolve to real node ids produced by ingestion. No doc defines the id format beyond the
  `node_<slug>` convention in api.md §1. **Consequence of not deciding:** `Recall@5 = 0`
  silently, the run "completes," and nothing fails. The most dangerous failure mode
  because it is silent.~~ **RESOLVED (2026-10-19):** `node_{doc_slug}_{type}_{locator}` — full grammar, examples and the four seed-test invariants in `architecture.md` §3.
- **D2 — Benchmark corpus.** ~~PRD §10 Q1. No public statute is selected.~~
  **RESOLVED (2026-10-19):** primary = Civil Code of the Philippines (R.A. 386, ICNL
  text-layer PDF, 167 pp, sha256 recorded); secondary = U.S. Code Title 11 (official
  GPO PDF, 334 pp). Evidence, checksums, and the rejected-candidates record in
  `docs/CORPUS.md`; dataset `document` slugs: `civil-code-public`, `usc-title-11`.
- **D3 — Arm B embedding model.** ~~ADR-004 pins the *generation* model across arms but
  names no embedding model for the dense baseline.~~ **RESOLVED (2026-10-19):**
  `BAAI/bge-small-en-v1.5` via `fastembed==0.9.0` — 384 dims, MIT, ONNX Runtime CPU
  (no torch), HF revision `5c38ec7c405ec4b44b94cc5a9bb96e735b38267a` pinned; rationale
  and runner-up comparison recorded in `docs/decisions/ADR-002` ("Arm B embedding
  model").

**Block:** nothing in Day 1–7 may begin until D1, D2, D3 are resolved.

---

## 11. Traceability

Every exam bullet maps to at least one module id, one plan task, and one acceptance
criterion. The mapping lives in `PLAN.md` (Day 0 conflict table, Day 1–7 slices with
acceptance criteria) — this file adds no requirements, it indexes the frozen ones.

---

## 12. Web conventions (landing page + app UI)

The landing page at `/` is already implemented (`apps/web/src/app/page.tsx`, marketing
components under `apps/web/src/components/marketing/`). The Day 5 three-pane research UI
lives at `/app/*`. Both must follow AGENTS.md conventions:

- **Copy source.** All user-visible marketing copy lives in `apps/web/src/lib/copy.ts` and
  nowhere else. The source of truth for that copy is `docs/landing-page/COPY.md`. The
  research disclaimer text is `copy.ts` line 221 (verified against the committed file).
- **Theme tokens.** Tailwind v4 takes its theme from CSS. There is no `tailwind.config.js`
  and there must not be one. Tokens live in an `@theme` block in
  `apps/web/src/app/globals.css`. Renaming one emits no error; it silently produces no CSS
  and breaks every consumer.
- **Fonts.** `--font-sans` (Geist) and `--font-mono` (JetBrains Mono) are webfont-first
  stacks that lead the cascade; Apple families sit behind them as graceful degradation.
  Deleting either one changes what every visitor sees — do not delete.
- **Icons.** Import from `@phosphor-icons/react/ssr`, never the package root (the root
  barrel calls `createContext` at module scope and breaks SSR page-data collection).
- **Shape system.** Radius 0 and no shadows. `text-ink-5` is for non-text decoration only
  — it fails the text contrast threshold and is banned on any readable string. Accent cyan
  (`accent-2`) is permitted in exactly one component, `comparison-arms.tsx`, never
  anywhere else.
- **Hyphens.** Zero em-dash and zero en-dash characters in any rendered string on the
  marketing route — use the ASCII hyphen.
- **Favicon.** The four assets in `apps/web/src/app/` (`icon.svg`, `favicon.ico`,
  `icon.png`, `apple-icon.png`) mirror the project mark in `brand-mark.tsx` rather than
  importing it, because a favicon loads outside the document and cannot reach the module
  graph or CSS. Gradient stops are hex, not oklch (unknown browser SVG baseline). Changing
  the mark's geometry in `brand-mark.tsx` requires re-rendering the four assets —
  hand-editing one desyncs them silently.
- **Server Components.** Most components are Server Components. Add `"use client"` only
  when the component genuinely needs an observer, a timer, or clipboard access — never to
  work around a styling problem.
- **"use client" boundary (Day 5).** The research UI at `/app/*` is a new route. The SSE
  stream consumer and disclaimer state are client components; the tree and source panes
  are server-rendered. Keep the boundary at the pane level, not the whole page.
- **Deploy boundary (architecture.md §7).** The landing page at `/` is a static export
  (`output: "export"` in `next.config.ts`) with no API calls; it deploys as a zero-runtime
  build on Vercel. The explorer at `/app/*` is a separate runtime calling the local API at
  `NEXT_PUBLIC_API_BASE_URL` (defaults to `http://127.0.0.1:8000`); if the API is
  unreachable, the explorer fails loud rather than fabricating results.
