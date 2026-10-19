# Tuklas Agent — Architecture (MVP v1.0)

**Status:** Draft for review (rev 2 — post adversarial review). Companion to `PRD.md`, `retrieval.md`, `evaluation.md`, `api.md`.
**Load-bearing rule:** LangGraph is the agent framework. The hierarchical/vectorless retrieval engine is ours. Never blur this line.

## 1. System overview

```text
Next.js (apps/web) ──REST/SSE──▶ FastAPI (apps/api, prefix /v1)
                                      │
        ┌─────────────────────────────┼─────────────────────────────┐
        ▼                             ▼                             ▼
 Document Engine               Tuklas Agent                   LLM Engine
 PyMuPDF → pages →        LangGraph graph               LLMInterface
 structure parser →       (plan/search/inspect/         (generate + generate_stream;
 Document Tree ──┐        expand/sufficiency)            ollama default, §7)
                 │                │
                 ▼                ▼
        Custom Retrieval Engine ◀── 7 tools (ONLY tree access)
        BM25 │ structural │ reference   (ranking + select_evidence live HERE)
                 │
                 ▼
        EvidenceSet → Context Builder (owned by retrieval/)
                 → Generation (renders PromptContext opaquely) → Answer + Citations
```

Storage (MVP): **SQLite + filesystem**. Tables: `documents`, `nodes`, `references`, `queries`, `retrieval_runs`, `evaluation_runs`. Files: PDFs + extracted artifacts + BM25 indexes. No Postgres, no vector DB in v1 (ADR-002).

Repo layout: `apps/web/`, `apps/api/{api,ingestion,retrieval,agent,generation,evaluation,models}/`, `data/{documents,indexes,datasets}/`, `experiments/{vector,vectorless,results}/`, `docs/`, `scripts/`.

## 2. Module boundaries (mechanically enforced)

Deny-import list for the primary path (exact dotted paths, CI-scanned — see CONSTRAINTS.md):

```text
DENY_PRIMARY = {sentence_transformers, faiss, chromadb, qdrant_client, pgvector}
 applies to: apps/api/{ingestion,retrieval,agent,generation,api}/
+ grep rule: `cosine_similarity|cosine\s*\(` must not appear in `apps/api/retrieval/`
```

| Module | Owns | May import | MUST NOT import |
|--------|------|-----------|-----------------|
| `ingestion/` | PDF→pages→text; structure parse → tree; page spans; `parse_confidence` | `models`, PyMuPDF, regex, RapidFuzz | `retrieval`, `agent`, anything in DENY_PRIMARY |
| `retrieval/` | BM25 index, ALL scoring/ranking, `select_evidence()`, Context Builder, reference resolution | `models`, `rank-bm25`/FTS5, RapidFuzz | `agent`, `langgraph*`, DENY_PRIMARY |
| `agent/` | LangGraph graph, 7 tool *wrappers* (arg validation + call into `retrieval.api` + trace logging), sufficiency gate *evaluation* (calls `retrieval.select_evidence`) | `retrieval.api` (tool impls ONLY), `langgraph*`, pydantic, `generation` (final call only) | `sqlite3`, `sqlalchemy`, `apps.api.models`, DENY_PRIMARY — **no direct tree/DB reads; all node access via the 7 tools** |
| `generation/` | prompt rendering from opaque `PromptContext`, `LLMInterface` calls, citation assembly, advice blocklist | `models`, LLM backends | `retrieval` internals, `sqlite3`, reordering/truncating evidence |
| `evaluation/` | dataset runner, both arms, metrics, judges | everything incl. `experiments/vector/` (SOLE legal importer) | — |
| `experiments/vector/` | dense baseline | — | imported by nothing except `evaluation` |

**Context Builder** (resolves the unowned-component finding): owned by `retrieval/`. Signature `build_context(evidence: EvidenceSet) -> PromptContext` with fixed rules: order by score desc (ties: shallower depth first, then position asc); budget 12k chars total; drop lowest-score-first; required cited nodes (gate set) are never dropped — if they exceed 12k, truncate each proportionally and set `context_truncated: true` on the trace so generation hedges on granularity. `generation/` renders `PromptContext` opaquely — any reordering there is a review failure, caught by the truncation contract test (`test_context_builder_budget`, added to CONSTRAINTS.md).

## 3. Document Engine

Pipeline: `PDF → PyMuPDF pages → text+metadata → structure parser (regex + RapidFuzz heading match) → tree → SQLite nodes + references`.

- Node fields: `id, document_id, parent_id, type, title, text, page_start, page_end, depth, position`. `references(id, source_node_id, target_node_id NULL-able, reference_text)`.
- `parse_confidence` ∈ [0,1] = fraction of pages with ≥1 heading match. **Flat-fallback predicate:** `parse_confidence < 0.5` → emit flat tree (document → page nodes, depth 0/1). (10–30% empty pages with otherwise good headings still parses structurally; >30% empty is always 422 `OCR_REQUIRED` per the next bullet — the two regions never overlap.) Flag stored on document, returned by `GET /documents/{id}`, badged in UI. Never a confidently wrong deep tree.
- Dedup: SHA-256 `file_hash`; identical bytes → 409.
- MVP limits: text-layer PDFs only, ≤ 500 pages, ≤ 100 MB. Empty-page = <50 chars; >30% empty pages → 422 `OCR_REQUIRED`. Encrypted → 422.

## 4. Agent (LangGraph) — orchestration only

Graph: `START → query_plan (emits 1–4 sub_questions) → search → inspect → (relevant? expand/follow : mark irrelevant + backtrack) → select_evidence (retrieval-owned) → sufficiency_gate → (more? search again : build_context) → generate → END`.

LangGraph owns: state object, node execution, conditional edges, loop + max-iteration guard, checkpointing (SQLite checkpointer), step-event streaming.

Guards (all binding):

- **Budget:** max 12 tool iterations per ask, **including** the one broaden-retry below. Retry does not reset the counter.
- **Per-tool output caps:** `search_nodes` default 10 / hard max 20; `list_children` max 50 rows, 200-char summaries; `inspect_node` / `get_parent` / `get_source` single node, 8000-char cap; `expand_context` radius ≤ 2, ≤2000 chars/node, ≤5 nodes; `follow_reference` single node or typed miss.
- **Sufficiency gate (binding numbers):** node *supports* sub-question S iff `score ≥ 0.15 AND (lex > 0 OR identifier-hit)`. Gate passes iff every planned sub-question (planner must emit ≥1; zero is invalid) has ≥1 supporting node in the kept set. Else one broaden-retry (stopword-strip + identifier-only query, stoplist versioned in `retrieval.md §3`, consumes iteration budget), then `insufficient_evidence` exit with the unified abstention shape (`api.md §3`).
- **Backtrack:** `tool_result` carries `relevant: bool`; irrelevant branches contribute zero evidence nodes and are logged with branch id. Visited-set prevents re-inspect loops.

## 5. Retrieval Engine (contract in retrieval.md)

Two-pass scoring (pass 1: lex/struct/title; pass 2: ref/hier off lex-top-5), weights **uniform defaults, ranges declared, tuned on dev** — never asserted optimal. Identifier normalizer runs before scoring. No dense embeddings, ever.

## 6. Generation

`LLMInterface` (`generate` + `generate_stream`, ADR-004) receives opaque `PromptContext`. System constraints: answer only from evidence; every factual sentence carries `[n]`; refusal path when gate fails; post-generation blocklist rejects advice phrasing ("you should", "I advise", "your case", "you must"). Output: markdown + `citations[]` + `insufficient: bool`.

## 7. Web (apps/web)

 Next.js + React + TypeScript + Tailwind + shadcn/ui. Three panes: tree / source / answer. `POST /v1/ask/stream` SSE (schemas in `api.md`). Persistent disclaimer banner (wording in PRD US-2).

 **Local-first / deploy boundary:** the landing page at `/` is a static export (`output: "export"` in `next.config.ts`) with no API calls; it deploys as a zero-runtime build on Vercel. The document explorer at `/app/*` is a separate runtime that calls the local API at `NEXT_PUBLIC_API_BASE_URL` (defaults to `http://127.0.0.1:8000`); if the API is unreachable, the explorer fails loud rather than fabricating results. `apps/web/src/lib/site.ts` reads the base URL from that environment variable. Vercel auto-detects the framework at `apps/web/`; no `vercel.json` is required for the marketing route.

## 8. Data model (DDL sketch — weights columns included)

```sql
documents(id TEXT PK, name TEXT, file_path TEXT, file_hash TEXT UNIQUE,
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

## 9. Cross-cutting

- Errors: single `APIError` shape; codes/statuses per `api.md §1`.
- Observability: structured ask log (question hash, iterations, evidence ids, weights snapshot id, latency, tokens); one `retrieval_runs` row per question per arm from the eval runner.
- Security: local-first; upload validation; no secrets in source; advice blocklist.
- Performance posture: BM25 in-process; indexed tree reads; SSE for long asks. Budgets set by first-run ratchet.

## 10. Build order (vertical slices)

1. Ingestion (PDF→pages→tree→GET tree) → 2. Retrieval (`search/inspect/expand` + `select_evidence`, no LLM) → 3. Agent loop (LangGraph + gate, stub generation) → 4. Generation (interface + citations + refusal) → 5. Web (3-pane + SSE) → 6. Evaluation (dataset + both arms + metrics). Each slice independently verifiable; `tasks/` planning starts after doc approval.
