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

Repo layout: `apps/web/`, `apps/api/{api,ingestion,retrieval,agent,generation,evaluation,models,tests}/` (the vectorless retrieval engine is `apps/api/retrieval/`), `data/{datasets,eval-reports}/` (tracked) and `data/{documents,indexes}/` (local and gitignored), `experiments/vector/` (the dense baseline arm), `docs/`, `scripts/`.

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

**Context Builder** (resolves the unowned-component finding): owned by `retrieval/`. Signature `build_context(evidence: EvidenceSet) -> PromptContext` with fixed rules: order by score desc (ties: shallower depth first, then position asc); budget 12k chars total; drop lowest-score-first; required cited nodes (gate set) are never dropped — if they exceed 12k, truncate each proportionally and set `context_truncated: true` on the trace so generation hedges on granularity. The identified node (ADR-005) is never dropped either: the identifier precedence is an ORDER, so the provision the user named often has the lowest score in the set, and dropping it first would silently undo the precedence exactly when the prompt is too large to hold everything. `generation/` renders `PromptContext` opaquely — any reordering there is a review failure, caught by the truncation contract test (`test_context_builder_budget`, added to CONSTRAINTS.md).

## 3. Document Engine

Pipeline: `PDF → PyMuPDF pages → text+metadata → structure parser (regex + RapidFuzz heading match) → tree → SQLite nodes + references`.

- Node fields: `id, document_id, parent_id, type, title, text, page_start, page_end, depth, position`. `references(id, source_node_id, target_node_id NULL-able, reference_text)`.
**Node ids (D1 — resolved).** A node id is `node_{doc_slug}_{type}_{locator}` — three to five underscore-separated tokens, tokens 2 and 3 always the document slug and the type. `doc_slug` is the owning document's registry slug (`documents.slug`): lowercase `[a-z0-9]` with internal single hyphens and **no underscores**, required unique per document, assigned at `POST /v1/documents` from the optional `slug` form field and defaulted by slugifying the file-name stem (empty result → 422 `VALIDATION_ERROR`; duplicate slug → 409 `CONFLICT`), so two statutes in one database can never collide on `nodes.id`. `type` is the node's `nodes.type` value verbatim, lowercase, from {document, book, title, part, division, chapter, section, article, subsection, page}. `locator` is the node's printed statutory number canonicalized **losslessly** — lowercase, keep `[a-z0-9]`, collapse each run of any other character to a single `-`, trim leading/trailing `-`; no roman→arabic conversion, no range expansion (both are §4 label-matching rules, never id rules) — or `u{k}` (k = 1-based ordinal among same-type nodes carrying no printed number, in document order) when the heading carries none; the single `document` node carries no locator. Page nodes are `node_{doc_slug}_page_{n}`, where n is the 1-based physical PDF page index and is the same integer stored in `page_start`/`page_end` (printed folio numbers are never used — front matter and roman pages are neither unique nor contiguous). If two nodes in one document share `(doc_slug, type, locator)`, document order resolves it: the first keeps the bare id, each later one appends `_d{k}` (its occurrence index, k ≥ 2); `_` is reserved for that, and a locator never contains `_`. Ids are a pure function of `(doc_slug, parser output)` — no RNG, rowid, counter, timestamp or content hash — so re-ingesting the same PDF at the same `code_version` reproduces the id set exactly. Ids are opaque handles: the §4 normalizer runs on `title` labels only, never on ids, and `ident`'s article-vs-chapter level is read from `nodes.type`, not parsed out of the id.

```
node_civil-code-public_document        # the one document root; carries no locator
node_civil-code-public_book_iv         # printed "BOOK IV" — roman kept; "4"≡"iv" is a §4 label rule
node_civil-code-public_article_1318    # printed "Art. 1318" — Art./Article/§ equivalence at label level only
node_civil-code-public_article_2000a   # printed "Art. 2000a"
node_civil-code-public_article_2000-a  # printed "Art. 2000(a)" — two distinct nodes, never merged
node_civil-code-public_chapter_1       # first "CHAPTER 1" in document order (bare)
node_civil-code-public_chapter_1_d2    # the next "CHAPTER 1", under another Title
node_civil-code-public_title_u1        # unnumbered "PRELIMINARY TITLE"
node_civil-code-public_page_1743       # flat fallback; page_start = page_end = 1743
```

Invariants (the strengthened seed test):
1. ingest: `COUNT(*) = COUNT(DISTINCT id)`; every id's token 2 equals its `document_id`'s `documents.slug`; token 3 equals `nodes.type`
2. seed data: every `gold_node` ∈ `nodes.id` AND starts with `node_{question.document}_`
3. every `question.document` resolves to exactly one ingested document (missing slug → loud 422, never an empty gold set)
4. rebuilding the tree twice from the same fixture yields identical id sets

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

 Next.js + React + TypeScript + Tailwind + shadcn/ui. Two views over one ask runtime, switched by a tablist in the header: the three-pane explorer (tree / source / answer) and a full-screen chat. `POST /v1/ask/stream` SSE (schemas in `api.md`). Persistent disclaimer banner (wording in PRD US-2).

 **The two views share one runtime, not one copy of it.** The ask runtime lives in `explorer.tsx` and produces a list of turns; the explorer renders the latest turn in its answer pane, and the chat renders the whole transcript. A citation in the chat opens the cited node inline, through the same `GET /v1/nodes/{id}/source` call the source pane uses, so the two views cannot disagree about what a citation refers to. The explorer's question field keeps the question after a run; the chat's own field clears on send, because there the transcript is the record. A `done` payload that is not an AskResponse is reported as a contract violation rather than rendered as a partial answer.

 **Each failure renders where it happened, exactly once.** The ask failure belongs to the turn, so the chat shows it on the failed turn (with Retry) and the explorer's answer pane shows it in place of the answer. The source-fetch failure belongs to the source pane, so it renders there and in the chat's inline source. Neither is also rendered in a shared banner: one failure shown twice reads as two failures. A failed answer carries no citations, no metrics and no token note, because a failure is not an answer. A run that has been asked but has not streamed its first step renders a pending state, which is distinct from the idle state of a pane that has never been used.

 **Local-first / deploy boundary:** the landing page at `/` is a static export (`output: "export"` in `next.config.ts`) with no API calls; it deploys as a zero-runtime build on Vercel. The document explorer at `/app/*` is a separate runtime that calls the local API at `NEXT_PUBLIC_API_BASE_URL` (defaults to `http://127.0.0.1:8000`); if the API is unreachable, the explorer fails loud rather than fabricating results. `apps/web/src/lib/site.ts` reads the base URL from that environment variable. Vercel auto-detects the framework at `apps/web/`; no `vercel.json` is required for the marketing route.

## 8. Data model (DDL sketch — weights columns included)

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

## 9. Cross-cutting

- Errors: single `APIError` shape; codes/statuses per `api.md §1`.
- Observability: structured ask log (question hash, iterations, evidence ids, weights snapshot id, latency, tokens); one `retrieval_runs` row per question per arm from the eval runner.
- Security: local-first; upload validation; no secrets in source; advice blocklist.
- Performance posture: BM25 in-process; indexed tree reads; SSE for long asks. Budgets set by first-run ratchet.

## 10. Build order (vertical slices)

1. Ingestion (PDF→pages→tree→GET tree) → 2. Retrieval (`search/inspect/expand` + `select_evidence`, no LLM) → 3. Agent loop (LangGraph + gate, stub generation) → 4. Generation (interface + citations + refusal) → 5. Web (3-pane + SSE) → 6. Evaluation (dataset + both arms + metrics). Each slice independently verifiable; `tasks/` planning starts after doc approval.
