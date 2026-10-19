# Tuklas Agent — API Contract (v1, rev 2)

**Status:** Draft for review (rev 2 — post adversarial review). Contract-first: implementation follows this file.
Base path `/v1`. JSON request/response; streaming over SSE. Uniform error shape — no exceptions, no traceback leaks.

## 1. Conventions

- Ids: `doc_<hash8>`, `node_<slug>` (`<slug>` = `{doc_slug}_{type}_{locator}` — D1 format, architecture.md §3), `q_<uuid8>`, `eval_<uuid8>`.
- Errors (ALL endpoints, including SSE terminal errors): `{ "error": { "code": "...", "message": "...", "details": {} } }`.
  | Code | Status | When |
  |------|--------|------|
  | `VALIDATION_ERROR` | 422 | bad body, non-PDF, oversize (>100MB), >500 pages |
  | `OCR_REQUIRED` | 422 | >30% pages with <50 chars extracted (includes fully empty text layer) |
  | `PARSE_FAILED` | 422 | PyMuPDF cannot open/extract at all (corrupt, encrypted-unreadable) |
  | `NOT_FOUND` | 404 | unknown document/node/evaluation id |
  | `CONFLICT` | 409 | identical `file_hash` re-upload, or duplicate `slug` (hash and slug conflicts live ONLY here) |
  | `LLM_UNAVAILABLE` | 503 | backend down/timeout |
  | `EVAL_FAILED` | 500 | runner crash, invalid config |
- Pagination: `GET /v1/documents?page=1&pageSize=20` (defaults 1/20, max `pageSize` 100, 1-indexed) → `{ data, pagination: { page, pageSize, totalItems, totalPages } }`.

## 2. Documents

```text
POST /v1/documents (multipart: file=*.pdf, slug?: form field — registry slug; default: slugified file-name stem; duplicate → 409)
  → 201 { id, name, page_count, parse_confidence, tree_root }   # tree_root: {id,type,title,child_count}
  → 422 VALIDATION_ERROR | OCR_REQUIRED | PARSE_FAILED; 409 CONFLICT (dup hash); 404 n/a
GET /v1/documents?page=&pageSize= → { data: [{id,name,page_count,created_at}], pagination }
GET /v1/documents/{id} → { id, name, file_hash, page_count, parse_confidence, created_at } | 404 NOT_FOUND
GET /v1/documents/{id}/tree?depth=3 → { root, children: [{id,type,title,depth,position,page_start,child_count}] } | 404
```

## 3. Retrieval & asking (unified insufficient shape)

```text
POST /v1/search { document_id: string, query: string, top_k?: int =10 (1..20) }
  → 200 { hits: [{node_id,title,snippet,score,components,page_start}], trace_id }
  → 404 (unknown document) | 422 (bad body)
POST /v1/ask { document_id: string, question: string, max_iterations?: int =12 (1..12) }
  → 200 AskResponse:
      { answer_markdown: string, citations: [{n, node_id, label, page_start, page_end}],
        evidence_ids: string[], iterations: int, latency_ms: int, tokens: {in,out},
        insufficient: bool }
      # insufficient=false on success; abstention returns the SAME shape with
      # answer_markdown=canned text, citations=[], evidence_ids=[], insufficient=true
  → 404 | 422 | 503 LLM_UNAVAILABLE
POST /v1/ask/stream (same body, SSE) — frozen v1 event sequence:
  plan{sub_questions} → tool_call{tool,args} → tool_result{tool,result_ids,relevant} →
  (repeat) → evidence{evidence_ids, insufficient} → token{text}* → done{ask_response} | error{error}
  # zero-tool-call runs legal: plan → evidence(empty) → done(insufficient ask_response).
  # error carries the §1 error body. Contract test pins this order.
GET /v1/nodes/{id} → NodeFull + hierarchy_path + outgoing refs | 404
GET /v1/nodes/{id}/source → { text, page_start, page_end, hierarchy_path } | 404
```

## 4. Evaluation (typed)

```text
POST /v1/evaluations { dataset_snapshot: string, arms: ["tuklas","vector"],
                       k: [1,3,5,10] (default), config: EvalConfig }
  EvalConfig { code_version: string, weights: object /* full retrieval weights snapshot */,
    normalization: string /* version id */, fuzzy_threshold: int /* 80..95 */,
    bonus_flags: { phrase: bool, fuzzy: bool }, stoplist_version: string,
    model: string, backend: ollama|llamacpp|vllm, temperature: 0 (fixed),
    seed: int, judge_prompt_version: string,
    margins: { recall5: 0.10, faithfulness: 0.10, max_category_gap: 0.20 } /* provisional, versioned */ }
  → 202 { evaluation_id, status: "queued" }   # statuses: queued|running|done|failed
  → 422 (unknown snapshot, arm mismatch, temperature ≠ 0) | 500 EVAL_FAILED (runner crash)
GET /v1/evaluations/{id} → { status, config: EvalConfig, per_question: [QuestionRow],
                             aggregates: Aggregates, system_stats: SystemStats }
  QuestionRow { question_id: string, arm: "tuklas"|"vector", retrieved_node_ids: string[],
    gold_hit: bool, rank_first_hit: int|null, claims_matched: int, claims_total: int,
    faithfulness: float|null, citations_valid: bool, insufficient: bool,
    latency_ms: {retrieval, generation}, tokens: {in, out} }
  Aggregates { retrieval: {recall@{1,3,5,10}, precision@{1,5}, mrr10, by_category},
    generation: {correctness, faithfulness, citation_accuracy, abstention_score},
    system: {latency_p50, latency_p95, tokens_mean, iterations_mean} }
  SystemStats { n_questions, n_arms, split_seed, dataset_snapshot }
  → 404 NOT_FOUND
```

`EVAL_FAILED` triggers: runner exception, model-parity violation (model+backend+temperature+seed equal across arms enforced by `check_eval_config.py`), dataset snapshot missing.

## 5. Verification

- [ ] Pydantic v2 request/response models committed with implementation (field types, optionality, bounds: `top_k 1..20`, `max_iterations 1..12`, `pageSize 1..100`).
- [ ] Contract test `test_ask_stream_order` (incl. zero-tool-call + error-terminal cases).
- [ ] Error-body shape asserted on all failure paths (upload 422s, dup-hash 409, unknown ids 404, LLM-down 503).
- [ ] Changes additive-only under `/v1` (new optional fields); renames/removals → `/v2` + ADR. Enforced by breaking-change test (snapshot response schemas).
