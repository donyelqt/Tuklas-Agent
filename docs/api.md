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
  | `LLM_UNAVAILABLE` | 503 | backend down/timeout. Raised by `apps/api/generation/interface.py` when the local LLM backend cannot be reached, times out, or returns an unusable body (ADR-004: LLM-down degrades to this 503, never to a fake answer). `details` carries the reason, never a traceback (api.md §1: "no traceback leaks"). |
  | `EVAL_FAILED` | 500 | runner crash, invalid config |
  | `INTERNAL_ERROR` | 500 | unhandled server fault. `details` is always EMPTY: the internal exception is logged, never returned (api.md §1: "no traceback leaks"). The app-level handler in `apps/api/api/app.py` emits this code too, so the table lists it rather than leaving a code clients have no arm for. |
- Pagination: `GET /v1/documents?page=1&pageSize=20` (defaults 1/20, max `pageSize` 100, 1-indexed) → `{ data, pagination: { page, pageSize, totalItems, totalPages } }`.
- A malformed request body answers with the SAME error body as every other failure: `422 VALIDATION_ERROR`, `details` naming the offending field and the problem type. The framework's default `{"detail": [...]}` shape is NOT used — a client has no arm for it, and the table above lists `VALIDATION_ERROR` for exactly this case.
- Cross-origin: the explorer at `/app/*` (architecture.md §7) is a separate runtime on another origin, so the API answers the browser's CORS preflight. The allowed origins come from `TUKLAS_CORS_ORIGINS` (comma-separated); the default is `http://localhost:3000,http://127.0.0.1:3000`.

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
        tokens_source?: "estimated"|"provider", insufficient: bool }
      # insufficient=false on success; abstention returns the SAME shape with
      # answer_markdown=canned text, citations=[], evidence_ids=[], insufficient=true
      # tokens_source says how tokens was produced, and is OPTIONAL: §5
      # allows additive changes under /v1 only for optional fields, so a
      # required new key would break a client built from the 7-key shape.
      # "estimated" = a character-count heuristic (chars//4) over the
      # evidence and the rendered answer, NOT a provider token count — the
      # Day-3 generator calls no model, so no prompt was ever sent.
      # "provider" = the LLM backend reported the counts (Day 4). A consumer
      # must not publish "estimated" numbers as measured token usage.
  → 404 | 422 | 503 LLM_UNAVAILABLE
POST /v1/ask/stream (same body, SSE) — frozen v1 event sequence:
  plan{sub_questions} → tool_call{tool,args} → tool_result{tool,result_ids,relevant} →
  (repeat) → evidence{evidence_ids, insufficient} → token{text}* → done{ask_response} | error{error}
  # zero-tool-call runs legal: plan → evidence(empty) → done(insufficient ask_response).
  # error carries the §1 error body. Contract test pins this order.
  # The wire format is `event: <name>\ndata: <json>\n\n` per frame. The data
  # payload is the event's SCHEMA OBJECT (the table below), NOT a wrapper
  # that repeats the event name: the name travels in the `event:` line.
  # Response headers: Content-Type: text/event-stream, Cache-Control:
  # no-cache, X-Accel-Buffering: no.
  # A bad body is a 422 RESPONSE, not an SSE error event: the request never
  # reached a run, so there is no run to report an error about. The error
  # event is for a failure DURING the run.
GET /v1/nodes/{id} → NodeFull + hierarchy_path + outgoing refs | 404
  # Flat: the node's own fields, then the two additions.
  # { node_id, document_id, parent_id, type, title, text, page_start,
  #   page_end, depth, position,
  #   hierarchy_path: string[],
  #   references: [{ id, target_node_id: string|null, reference_text }] }
  # target_node_id is null for an UNRESOLVED edge: the target was not
  # identified, and it is never guessed.
GET /v1/nodes/{id}/source → { text, page_start, page_end, hierarchy_path } | 404
  # node_id?: string is an OPTIONAL addition (§5): it names the node the
  # source came from, so a client that fetched it from a citation can check
  # that the citation and the source agree. The four contract keys are
  # always present.
```

The canned abstention string is pinned — the wording is part of the contract, so
a client can match on it:

    The retrieved evidence is insufficient to answer this question from the documents.

SSE payload schemas (frozen; PLAN.md Day 5 gap #18 — the web client builds
against exactly these):

```text
plan         { sub_questions: string[] }
tool_call    { tool: string, args: object }
tool_result  { tool: string, result_ids: string[], relevant: boolean }
evidence     { evidence_ids: string[], insufficient: boolean }
token        { text: string }
done         { ask_response: AskResponse }
error        { error: { code: string, message: string, details: object } }
```

Every `tool_call` is immediately followed by its `tool_result`, in the order the
tools ran. `token` events appear only when the gate passed; an abstention
streams `plan → … → evidence → done` with no `token` event. The `error` event
is terminal: it is the last event of the stream.

The `token` events are a REPLAY of the model's deltas, not a live feed: the
advice oracle refuses the WHOLE answer, so no text can be released before the
whole of it has been checked. The deltas are the model's own, in the model's
order, so a client that joins them receives exactly the answer — but they
arrive in a burst once the model has finished. The `plan` / `tool_call` /
`tool_result` / `evidence` events ARE live.

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

- [x] Pydantic v2 request/response models committed with implementation (field types, optionality, bounds: `top_k 1..20`, `max_iterations 1..12`, `pageSize 1..100`). **Done (2026-10-20, Day-5):** `apps/api/models/schemas.py` holds the request and response models for every route. The bounds are ENFORCED (a value outside the range is a 422, never a clamped value), and the string fields carry no length bound, because the contract declares bounds only for the numeric fields — a bound the contract does not declare would reject a request the contract declares legal (an empty question is the legal zero-tool-call run).
- [x] Contract test `test_ask_stream_order` (incl. zero-tool-call + error-terminal cases). **Done (2026-10-20, Day-3):** `apps/api/tests/test_agent.py` — `test_ask_stream_order` asserts the WHOLE event sequence, not membership: `plan → (tool_call, tool_result)* → evidence → token* → done`. The earlier form (first is plan, last is done, evidence between them, tool_call adjacent to its tool_result) still accepted `evidence` in the middle of the tool stream and `token` before `evidence`; verified by injecting that reorder and watching the test fail. Two companion cases: `test_ask_stream_order_zero_tool_call` (`plan → evidence(empty) → done`) and `test_ask_stream_order_error_is_terminal` (one terminal `error` carrying the §1 body, no `done` after it).
- [x] The §3 routes are exposed over HTTP and pinned at the transport layer. **Done (2026-10-20, Day-5):** `apps/api/tests/test_api_routes.py` — the five §3 routes, their response shapes, the §1 error body on every failure path (404 unknown document, 404 unknown node, 422 malformed body, 422 `top_k` outside 1..20, 422 `max_iterations` outside 1..12, 503 LLM-down), and the SSE wire format. `test_ask_stream_event_order_is_frozen` asserts the whole sequence at the HTTP level; `test_ask_stream_rejects_a_batch_replay_implementation` states the discriminating check once and applies it to BOTH the route's streaming body and a batch/replay body, so the check must accept the first and reject the second — a replay produces the same events in the same order, so the order alone cannot tell them apart.
- [x] Error-body shape asserted on all failure paths (upload 422s, dup-hash 409, unknown ids 404, LLM-down 503). **Done (2026-10-20, Day-5):** every failure path now answers with the §1 body. The upload paths (422s, dup-hash 409) are covered by `tests/test_ingest.py`; the retrieval/asking paths are covered by `tests/test_api_routes.py` — 404 unknown document, 404 unknown node, 422 malformed body, 422 `top_k` outside 1..20, 422 `max_iterations` outside 1..12, 503 LLM-down (a dead backend, no live model needed), and the terminal SSE `error` carrying the §1 body. `test_api_v1_snapshot.py` asserts the error body is exactly `{code, message, details}` for every code the agent can emit. A malformed body is no longer the framework's default `{"detail": [...]}`: `apps/api/api/app.py` maps `RequestValidationError` onto `422 VALIDATION_ERROR` in the §1 shape.
- [x] Changes additive-only under `/v1` (new optional fields); renames/removals → `/v2` + ADR. Enforced by breaking-change test (snapshot response schemas). **Done (2026-10-20, Day-3):** `apps/api/tests/test_api_v1_snapshot.py` — the test was named in CONSTRAINTS.md and api.md §5 but did not exist, so the rule was asserted and not enforced. It pins the frozen AskResponse shape and the frozen error table, and it enforces the additive-only rule mechanically: no frozen key may be removed or renamed, the code and api.md must agree on the key set, and a key added after the freeze must be declared OPTIONAL (a required new key is a strict-schema break for a client built from the 7-key shape). `tokens_source` is therefore optional — it is present exactly when it is needed, and absent otherwise.
