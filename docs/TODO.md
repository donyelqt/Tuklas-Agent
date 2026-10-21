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
  - *Acceptance:* corpus PDFs fetched into `data/documents/` (local, gitignored per
    ADR-002); `data/datasets/v1/` structure initialized. ✔ verified locally: 167/334
    pages, text layer extracted, `Article 1318` seed example located verbatim.
- [x] **D3 — Arm B embedding model.** ~~Pin a CPU-runnable dense embedding model for the
  vector baseline; record it in `docs/decisions/ADR-002`.~~ **DONE (2026-10-19):**
  `BAAI/bge-small-en-v1.5` via `fastembed==0.9.0` (384 dims, MIT, ONNX Runtime CPU, HF
  revision `5c38ec7c405ec4b44b94cc5a9bb96e735b38267a`), recorded in ADR-002.
  - *Consequence if skipped:* Arm B cannot be built; the "arms differ only in retrieval"
    parity claim is unenforceable.
  - *Acceptance:* embedding model named in ADR-002; the package lands in the pinned
    pinned manifest (Day 7, poetry.lock). ✔ model recorded; package pin carried to Day 7.
- [x] Confirm the 6 reconciliation edits from `PLAN.md` Day 0 are present on main
  (threshold 0.15, quarantine scope incl. `api/`, cosine grep, COPY.md backends label,
  DESIGN.md sources, dead design.ts ref) — committed in `97d862d`/`5e16463`.
  - *Acceptance:* spot-check each of the 6 against its file; any regression re-fixed here.

**Block condition:** D1 + D2 + D3 all `[x]`. No slice starts until then.

---

## Day 1 — Slice 1: Ingestion (PDF → pages → tree)

**Targets:** `apps/api/ingestion/`, `apps/api/api/` (route handlers), `data/` layout,
`scripts/check_no_vectors.py`. **Depends on:** D1, D2.

- [x] `POST /v1/documents` returns 201 / 422 / 409 per `api.md` §2 with the frozen error
  shape (`VALIDATION_ERROR | OCR_REQUIRED | PARSE_FAILED`, dup-hash `CONFLICT`).
- [x] PyMuPDF pipeline: PDF → pages → text + page_start/end. Page < 50 chars = empty;
  doc > 30% empty → 422 `OCR_REQUIRED`; encrypted/unreadable → 422 `PARSE_FAILED`;
  SHA-256 dedup → 409.
- [x] Structure parser (regex + RapidFuzz heading match) → `nodes` + `references` tables
  per `SPEC.md` §3 DDL, indexes included.
- [x] `parse_confidence ∈ [0,1]`; `< 0.5` → flat fallback (document → page nodes, depth
  0/1), flag stored on document + returned by `GET /documents/{id}` + badged in UI.
- [x] `scripts/check_no_vectors.py` written and green — the gate that lets slice 2
  proceed (quarantine scope + agent-thinness + cosine grep, recursive scan).
  - *Acceptance (PLAN.md Day 1):* `pytest -k ingest` on the 2 chosen statutes asserts
    `page_count` correct, `root.type` correct, `child_count > 0`, 100% leaves carry
    `page_start`/`page_end`, flat fixture gets `parse_confidence < 0.5` + fallback flag.

---

## Day 2 — Slice 2: Retrieval engine (BM25 + structural + reference, no LLM)

**Targets:** `apps/api/retrieval/`. **Depends on:** Day 1 + `check_no_vectors.py` green.

- [x] SQLite tree store: `nodes` + `references`, indexes `(document_id, parent_id)`,
  `(document_id, type)`, FTS5 on `(title, text)`.
- [x] Per-document BM25 index → `data/indexes/{doc_id}/bm25.json`.
- [x] Identifier index: normalized labels → node ids (normalizer per `retrieval.md` §4;
  ranges expanded, canonical map, both sides normalized).
- [x] Two-pass scoring: `lex`/`ident`/`title` (pass 1, freezes lex-top-5/top-3) →
  `ref`/`hier` (pass 2, computed against the frozen sets). Weights 1.0 defaults, range
  [0,2]; disjoint inputs enforced.
- [x] 7 tool functions per `retrieval.md` §5 with binding caps: `search_nodes` (default
  10, hard max 20), `inspect_node` (≤8000 chars), `list_children` (max 50 rows, 200-char
  summaries), `get_parent`, `expand_context` (radius ≤ 2, ≤5 nodes, ≤2000 chars/node),
  `follow_reference` (typed `resolved|unresolved|error`; unresolved → move on, never
  invent), `get_source`. `select_evidence` retrieval-owned, called direct by the gate.
- [x] `select_evidence()`: dedup → drop <50 chars → drop page-span-less → rank by score →
  keep top 8 → per-sub-question support check at **0.15**.
- [x] Context Builder: `build_context(EvidenceSet) -> PromptContext`, 12k char budget,
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

- [x] LangGraph graph: `START → query_plan → search → inspect → (relevant? expand/follow :
  mark irrelevant + backtrack) → select_evidence → sufficiency_gate → (more? search again
  : build_context) → generate → END`. Built in `apps/api/agent/graph.py`
  (langgraph 0.2.76, `StateGraph` + `START`/`END`); 9 nodes — the 8 in the topology
  plus `broaden_search`, the retry branch.
- [x] **7 thin wrappers** only (arg validation + call `retrieval.api` + trace logging) —
  `apps/api/agent/tools.py`, one function per tool plus `TOOL_TABLE`; the graph reaches
  the tree through that table and nowhere else.
- [x] Max 12 tool iterations **including** the broaden-retry; retry does not reset.
  Every wrapper call ticks the counter; the retry spends from it.
- [x] Sufficiency gate (binding): per-sub-question `score ≥ 0.15 AND (lex > 0 OR
  identifier-hit)`; every planned sub-question needs ≥1 supporting node (planner emitting
  0 is invalid → gate fails closed → retry plan once, then insufficient). One
  broaden-retry (stopword-strip + identifier-only, stoplist per `retrieval.md` §3),
  then `insufficient_evidence` exit with the unified abstention shape.
  - *Implementation note:* the threshold check stays retrieval-owned — `select_evidence`
    returns `gate.per_question` (the supporter lists) and the agent decides pass/fail
    from it. The agent never re-scores a hit. Insufficiency is decided by that verdict,
    NOT by "are there kept nodes": a query with no lexical match can still leave
    score-0 nodes in the kept set, and answering from those would fabricate.
- [x] Backtrack: `relevant: bool` on every `tool_result`; irrelevant branches contribute
  zero evidence, logged with branch id; visited-set prevents re-inspect loops.
  - *Implementation note:* a branch is relevant iff the node it returned could itself
    contribute evidence (≥50 chars of text AND a page span — the same two rules
    `select_evidence` applies). A heading-only node is a dead branch, not evidence.
  - *Acceptance (PLAN.md Day 3):* multi-hop fixture trace shows ≥ 1 abandoned branch
    (relevant=false, zero evidence) + ≥ 2 inspect/expand before the answer.
    `mypy apps/api` zero errors.
  - *Verified (2026-10-20):* 50 agent tests green (plus 4 in `test_api_v1_snapshot.py`);
    full suite 87 passed; ruff clean; mypy clean on 29 files; `check_no_vectors.py` OK
    (29 files scanned); zero suppression comments in `apps/api/` or `apps/web/src/`.
    Runtime smoke:
    a two-part question streams 2 searches, 6 inspects (1 abandoned), 2 expands, then
    `evidence` + `done`; gibberish → canned abstention with `citations: []` and
    `evidence_ids: []`; empty question → the legal zero-tool-call run
    (`plan → evidence(empty) → done`); an unknown document raises `NOT_FOUND` 404 and
    the SSE path yields one terminal `error` event carrying the api.md §1 body.
  - *Gate fails closed for an unsearched sub-question (2026-10-20).* `select_evidence`
    evaluates each sub-question against the WHOLE kept set, so a sub-question that was
    never searched still collected supporters from the nodes another sub-question
    found — and the gate passed for a question the agent never looked up. Measured
    with a budget of 1 (which truncates the search loop after the first
    sub-question): both sub-questions reported 8 supporters and the gate passed.
    The agent now puts only the SEARCHED sub-questions to the gate; an unsearched
    one counts as missing and the run abstains. The retrieval contract is
    untouched — the agent simply stops asking about a question it did not look up.
    After: budget 1 → gate fails closed, `insufficient: true`, 0 citations; budget ≥ 2
    → gate passes and answers.

  - *Request validation (2026-10-20):* `max_iterations` outside api.md §3's 1..12 is
    a 422 (`VALIDATION_ERROR`), not a silent clamp — 13 used to clamp to a
    1-iteration run and 0/-1 produced a zero-iteration run that answered with the
    canned abstention as if it were a real result. The route layer validates
    `page`/`pageSize`/`depth` the same way; the agent now hands it a typed 422.

  - *An irrelevant branch's hits no longer reach the evidence pool (2026-10-20).*
    `search_nodes` returns the document's top-k nodes whether or not the query
    matched them — a non-matching query comes back with every score at 0.0.
    The wrapper fed ALL of them to `select_evidence` while marking the branch
    irrelevant, which breaks architecture.md §4 ("irrelevant branches contribute
    zero evidence nodes") twice over: the score-0 nodes could occupy kept
    slots, and `select_evidence` dedups by `node_id` keeping the FIRST
    occurrence, so a score-0 copy could shadow the same node's genuinely-scored
    copy from a later, relevant search. Measured on "article 1318" (19 of the
    20 top nodes overlap with the irrelevant branch): before, the kept set held
    `article_1318` (score 1.000) plus three score-0 nodes — a heading, a
    section and a book — that a reader would have been shown as evidence;
    after, it holds `article_1318` alone. Only the MATCHED hits feed the pool
    now.

  - *SSE streaming (2026-10-20):* the first implementation ran the whole graph
    and replayed the accumulated trace as events. The event SEQUENCE was
    correct, so a sequence assertion could not see it — but nothing was
    streamed, which PRD US-3 requires ("I want to see the agent's
    search→inspect→expand steps streamed"). Measured before: first event and
    last event both at 0.153s (one burst). After: `plan` at 0.011s, then each
    `tool_call`/`tool_result` pair as that tool completes, tokens at the end.
    The graph now runs through LangGraph's `stream(stream_mode="updates")` and
    the SSE layer emits each trace entry exactly once as it appears. The test
    discriminates instead of restating the implementation: it counts the tool
    calls and asserts none has run when the `plan` event is yielded (it fails
    against the batch implementation).

  - *Deferred to Day 4:* the `follow_reference` wrapper exists and honours the
    `resolved|unresolved|error` contract, but the `references_` table is empty —
    ingestion does not parse reference edges yet (Day 1 scope). The graph therefore
    expands rather than follows, and no reference branch is reachable until that
    parsing lands. Recording it here rather than pretending the branch is exercised.

  - *Measured finding — `expand_context` results cannot enter the evidence set
    (2026-10-20).* `select_evidence` takes the SCORED hits from `search_nodes`; a
    node discovered by expanding a hit was never scored, so it has no score and
    cannot be a supporter. The expansion is therefore a *reading* step (it tells
    the trace and the answer which neighbours surround the hit) and not a second
    source of evidence. This matches `retrieval.md` §5 — `select_evidence(hits:
    list[NodeHit], sub_questions)` — but it means the "expand" branch of the
    topology changes what the agent READS, not what it CITES. Recorded because a
    reader could otherwise assume an expanded neighbour can be cited.

  - *Measured finding — the specified gate cannot refuse an out-of-domain
    question (2026-10-20).* This is the most consequential finding of the slice
    and it blocks the Day-6 dataset, so it is recorded here in full.
    PRD US-2 and `evaluation.md` §4 make refusal binding: on an
    `answerable: false` question, "correct refusal = 1, **any cited answer = 0**".
    Measured on the Civil Code, the gate as specified passes EVERY plausible
    unanswerable question:
    | question | gate | citations |
    |---|---|---|
    | Who is the current President of the Philippines? | PASS | 8 |
    | What is the registration fee for a fishing license? | PASS | 8 |
    | How many days do I have to file an appeal to the Court of Appeals? | PASS | 8 |
    | What is the penalty for driving without a driver's license? | PASS | 8 |
    | When did the Civil Code take effect? | PASS | 8 |
    Root cause, measured (not inferred): `lex` is BM25 **min-max normalized per
    query** (`retrieval.md` §3), so the best-matching node always scores
    `lex = 1.0` whenever the query matches anything at all — the strength of the
    match is erased. The gate's rule (`score >= 0.15 AND (lex > 0 OR ident-hit)`)
    is therefore satisfied by a *single token* appearing anywhere in the
    document. "Who is the current President of the Philippines?" matches 2300 of
    2412 nodes; "What are the requisites of contracts?" matches 2302. The
    discriminating signal does not exist in the lexical layer.
    The refusal therefore triggers on TOKEN ABSENCE, not on topic. Measured
    exactly: one corpus occurrence of any query token is enough for the gate to
    pass with `lex = 1.0` — `fishing` (3 nodes), `driving` (2), `appeal` (4),
    `president` (1) all pass; `zzzznotaword` and `xylophone zygote` (0 nodes)
    fail closed and abstain correctly. So the constraint on a planted
    `answerable: false` row is stricter than "out-of-domain": every content
    token of the question must be absent from the corpus, or the question will
    be answered with citations and scored abstention = 0.

    Ruled out by measurement, so the next reader does not repeat the search:
    - **absolute BM25** instead of min-max: unanswerable scores are HIGHER
      (36.4 for the appeal question vs 15.3 for the requisites question);
    - **match breadth**: ~2300/2412 for both answerable and unanswerable;
    - **term coverage** (max content tokens of the query present in one kept
      node): answerable 0.67–1.00, unanswerable 0.33–1.00 — overlapping;
    - **raw token isolation**: `model.get_scores(["zzzznotaword"])` correctly
      returns 0 for every document, so `rank_bm25` is not the culprit — the
      normalization is.
    The agent is NOT at fault: the gate reads retrieval's own verdict and
    applies the specified threshold. The specified threshold is what cannot
    discriminate. Consequences and the decision:
    - The Day-6 dataset's `answerable: false` rows must be planted so the
      question is out-of-domain for the *corpus* in a way the lexical layer can
      see — e.g. naming a subject with no occurrence in the corpus at all
      ("fishing license") — or the abstention score will be 0 for every arm and
      the comparison will report a refusal failure that is really a dataset
      failure.
    - A refusal signal that survives a min-max-normalized lexical layer is
      needed if out-of-domain refusal is a product requirement. That is a
      retrieval-layer change (an absolute-score component, or a coverage
      requirement), which is Day-2/§3 territory and needs a recorded tuning run
      before it can change the frozen contract.
    - **Not changed here.** `retrieval.md` §3 is frozen and the weights/lex
    definition are authoritative; inventing a new signal inside the Day-3 slice
    would silently change the retrieval contract and invalidate the Day-2
    golden set. Recorded for the Day-6 dataset design and the retrieval tuning
      run instead.

  - *Measured finding — the broaden-retry is unreachable on this corpus (2026-10-20).*
    `retrieval.md` §3 says the retry fires when the lex layer is all-zero, and §7
    makes it the one recovery path before `insufficient_evidence`. On the Civil
    Code it cannot fire, and the reason is mechanical: BM25's lex layer is
    min-max normalized over the query's candidates, so a query that matches
    exactly ONE node gives that node `lex = 1.0` — which clears the 0.15
    threshold on its own. The gate therefore fails only when the query matches
    NO node at all, and such a query is built only from non-stopwords (any
    stoplist word — `the`, `of`, `article` … — occurs in 44–2570 nodes, so it
    always produces a lexical match and the gate passes). A query made only of
    non-stopwords is exactly the query the stoplist cannot reduce, so the
    "stopword-strip + identifier-only" retry would re-run the identical search.
    Measured: 0 rescue cases across every search performed (stopword+real-word
    combinations, title-only words, short-text nodes, identifier queries).
    Two consequences, both recorded rather than papered over:
    (a) the retry is skipped when the strip cannot change the query — re-running
    an identical search is provably useless because retrieval is deterministic,
    and it would spend iteration budget to learn nothing;
    (b) a query the gate fails on now goes straight to `insufficient_evidence`.
    The mechanism is implemented, and the retry's FIRING path is exercised by a
    test that stubs the first search to return nothing (so the gate fails and
    the retry runs); the retry is nonetheless unreachable on the real corpus.
    What is missing is a corpus where it can fire by itself. **Open question for
    the corpus decision (D2):**
    the retry is specified against a scoring model whose lex layer does not
    min-max-normalize a single match to 1.0. Either the corpus gains a document
    whose headings are the only match for a plausible question, or
    `retrieval.md` §3's premise is amended to match how BM25 actually scores.
    Not resolved unilaterally — the spec is authoritative and the corpus is
    already pinned.

---

## Day 4 — Slice 4: Generation (LLMInterface + citations + refusal)

  - *Wiring (2026-10-20, Day-4):* the ask surface and the graph now default
  to the REAL configured generator, not the model-free stub. The stub applies
  no advice oracle and no refusal path, so a request answered through it
  would bypass the safety machinery the generation layer exists to provide.
  The stub is explicit opt-in, for tests that want a deterministic answer.
  The backend is selected by config (`apps/api/generation/config.py`:
  `TUKLAS_LLM_BACKEND` / `_MODEL` / `_BASE_URL` / `_TEMPERATURE` / `_SEED` /
  `_TIMEOUT`), which is what ADR-004 asks for - "Backend selected by config".
  Verified through the DEFAULT path (no `generator=` argument): the real model
  answers, the provider's deltas stream (87 token events), the token counts
  are the provider's own, and the oracle is in force.
  - *Scope note (2026-10-20, Day-4):* ADR-004 names three backends
  (`ollama | llamacpp | vllm`, Ollama default). Only Ollama is implemented in
  this slice. The other two are OpenAI-compatible servers, so they are a
  base_url away, but a backend that is not implemented must not be ACCEPTED
  and then fail at the first request - the constructor rejects an unimplemented
  backend by name, and the recorded `backend` field names the one that
  actually ran, so a recorded run is never confused across backends.
  Adding the other two is a Day-6 concern (the eval runner must record the
  backend, and a two-backend parity check is a stronger test of the seam than
  one).

- [x] *Measured (2026-10-20, Day-4):* the generation layer answers the 5 gold
  questions against the REAL local model (`qwen2.5:3b`, ollama, temperature 0,
  seed 42) through the full stack - retrieval, agent loop, generation.
  **Reproducible:** `python -m scripts.measure_generation` (from the repo
  root) - the harness is committed, so this is a re-runnable record rather
  than a remembered number.
  (It needs the corpus, which is gitignored per ADR-002, and a running local
  LLM; it skips with a clear message when the corpus is absent.)
  Result: **0 unmarked claims, 0 unresolved markers, 0 advice phrasing**
  across the five. Every factual sentence carries a `[n]` marker, and every
  marker names a citation in the answer's citation list. The harness also
  reports a conservative "unsupported" signal - a content word of the sentence
  is in none of the cited nodes - which is paraphrase-sensitive, not a verdict:
  reading the sentence against the cited node shows the substance is verbatim
  (article 19: "Every person must, in the exercise of his rights ... act with
  justice", cited [1]). The token counts reported are the provider's own, not
  the Day-3 character-count estimate.
  One prompt change came out of the measurement: the citation instruction now
  shows the one-marker-per-sentence shape with an example, and the prompt
  version moved `prompt-v1` -> `prompt-v2` (the prompt is part of the recorded
  run). Measured over three questions: prompt-v1 marked 18 of 21 factual
  sentences, prompt-v2 marked 18 of 18.

  - *Measured finding (2026-10-20, Day-4):* a question that NAMES a
  provision and carries other words loses the named provision from the
  evidence window. "What does article 1318 require?" retrieves article_1318 at
  rank 9 (ident=1.00, lex=0.00) behind articles 1395/1402 (lex=1.00 on the
  word "require"), and the top-8 cut drops article_1318 entirely — so the
  model answers about the word-match ("The article 1318 requires the
  contracting party ... to ratify") and cites article_1395, a DIFFERENT
  provision. Measured across forms: "article 1318" alone -> the named article
  is rank 0; "What is article 1318?" -> rank 5, in the window; "What does
  article 1318 require?" -> rank 9, OUT of the window; "What does article 2124
  provide?" -> not retrieved at all.
  This is a RETRIEVAL-layer ranking defect, not a generation one: the
  identifier signal cannot compete with the lexical signal in the
  score-ordered top-8 cut. It directly undermines PRD US-2 ("every legal
  claim has a citation so I can verify it") — the citation is valid, but it
  points at the source of words the model was given, while the answer's
  SUBJECT is a provision that was never in the evidence.
  Not changed from the Day-4 slice: `retrieval.md` §3 is frozen, the window is
  retrieval-owned, and a fix changes the retrieval contract (it needs a
  recorded tuning run, like every other weight or normalisation change).
  Recorded against the Day-4 slice and raised for the retrieval tuning run.

**Targets:** `apps/api/generation/`. **Depends on:** Days 2 + 3.

- [x] `LLMInterface.generate()` + `.generate_stream()` (ADR-004): ollama default,
  `temperature=0`, seed pinned.
  - *Done (2026-10-20):* `apps/api/generation/interface.py`. One `_options()` builder
    feeds every call path, so the two methods cannot diverge; the HTTP layer is
    covered against a real socket (`tests/test_generation_http.py`): the seed and
    temperature go in `options` where the backend reads them, the stream flag is
    set, and the single-shot and streaming calls send identical parameters.
- [x] Renders opaque `PromptContext` — never reorders or truncates evidence (that is the
  Context Builder's job; the contract test catches violations).
  - *Done (2026-10-20):* `apps/api/generation/prompts.py`. The contract test asserts
    every part's FULL text is present, in order, including a part longer than any
    plausible cap.
- [x] Citation assembly: `citations[] = [{n, node_id, label, page_start, page_end}]`.
  - *Done (2026-10-20):* `apps/api/generation/citations.py`. The page span comes from
    the EVIDENCE (the context parts carry only {node_id,text,score}), and a node
    outside the evidence set produces no citation at all.
- [x] Advice blocklist post-generation: rejects "you should", "I advise", "your case",
  "you must" (single oracle shared with `architecture.md` §6 / PRD US-2).
  - *Done (2026-10-20):* `apps/api/generation/blocklist.py` is the single oracle; the
    prompt states the rule as framing WITHOUT the phrases (CONSTRAINTS.md forbids
    advice phrasing IN prompts) and asserts its own text against the oracle at import
    time. The matcher permits the plural and the no-apostrophe contraction, so
    "your cases" and "you shouldnt" cannot slip through.
- [x] Refusal path: gate failure → canned abstention string (pin the exact wording) +
  `citations: []` + `insufficient: true`. Never a fabricated answer.
  - *Done (2026-10-20):* `apps/api/generation/service.py`. The refusal keys on the
    GATE VERDICT, the abstention string is imported from the agent (not re-spelled),
    and a tripped advice oracle refuses the WHOLE answer with the same shape.
  - *Acceptance (PLAN.md Day 4):* 5 gold questions pass an arm-blinded LLM judge with
    100% of factual claims cited; builder spot-checks all 5. Advice blocklist unit test
    green.
  - *Acceptance status:* the CITATION half is measured and reproducible (the
    harness above). The **LLM-judge half is NOT done**: the blinded,
    versioned judge prompt is Day 6's `evaluation_runs` work, and
    `evaluation.md` §4 requires it then. The harness's claim check is
    mechanical word-overlap against the cited node, not a judge. So the
    acceptance line stays open until the judge runs.

---

## Day 5 — Slice 5: Web three-pane explorer + SSE client

**Targets:** `apps/web/src/app/app/` (new route), new components. **Depends on:** Day 4.

- [x] Three panes: tree / source / answer at `/app/*`.
- [x] SSE event payload schemas spec'd in `api.md` §3 **before** this slice (PLAN.md gap
  #18 — closed by the Day-3 slice: the schemas and the pinned abstention string are in
  api.md, with the Day-3 contract test pinning the order).
- [x] Client built against the frozen sequence: `plan → tool_call → tool_result →
  (repeat) → evidence → token* → done | error`.
- [x] Citation click → `GET /v1/nodes/{id}/source` → source pane renders
  `text + page + hierarchy_path`.
- [x] Persistent disclaimer banner (answer pane) — worded exactly as `copy.ts` line 221;
  persists across answer interactions, does not scroll away.
- [x] Explorer runtime boundary (`architecture.md` §7): calls the local API at
  `NEXT_PUBLIC_API_BASE_URL` (default `http://127.0.0.1:8000`) via `site.ts`; API
  unreachable → fail loud, never fabricate.
- [x] Web conventions (`SPEC.md` §12): `copy.ts` is the only source of marketing copy; no
  inline strings in JSX; `@theme` tokens in `globals.css`; icons from
  `@phosphor-icons/react/ssr`; radius 0 / no shadows; ASCII hyphens only on the marketing
  route; `text-ink-5` never on readable strings; `accent-2` only in
  `comparison-arms.tsx`.
- [x] `"use client"` boundary: SSE consumer + disclaimer state are client components;
  tree + source panes server-rendered. No blanket `"use client"` on the page.
  - *Acceptance (AGENTS.md pre-flight):* `npx tsc --noEmit -p tsconfig.json` clean;
    `npm run lint` zero warnings; `npm run build` clean. Browser test: SSE events render
    in order; citation click opens source; disclaimer stays visible during stream.
    Marketing-route pre-flight per `docs/landing-page/DESIGN.md` §13 if the landing page
    is touched.

  - *Done (2026-10-21), verified in a real browser against the running API:*
    the route loads at `/app`; the three panes render; the disclaimer carries
    the exact PRD US-2 wording, is visible before any question and after the
    answer arrives, and is a sibling of the scroll container rather than
    inside it (so it does not scroll away); the document picker lists both
    ingested corpora; the tree renders 1395 nodes for the Civil Code; the SSE
    steps render in the received order (plan, then each tool_call paired with
    its tool_result, then evidence); a real answer came back from the local
    model with citation buttons; clicking citation [1] fetched
    `GET /v1/nodes/{id}/source` and the source pane showed the node id, the
    page range and the hierarchy path, and the tree marked the node
    `aria-current`; with the API forced unreachable the page shows a
    `role="alert"` and a disabled Ask button, and no fake empty result. Zero
    page errors, empty console.

  - *The explorer needed the five frozen API routes, which did not exist.*
    `apps/api/api/routes.py` registered only the four document routes; the
    logic for `POST /v1/search`, `POST /v1/ask`, `POST /v1/ask/stream`,
    `GET /v1/nodes/{id}` and `GET /v1/nodes/{id}/source` already existed in
    `retrieval/api.py` and `agent/ask.py` but was never wired to HTTP. They
    are wired now, per `api.md` §3, with a malformed body mapped to the
    frozen error body (FastAPI's default `{"detail": [...]}` shape has no arm
    in a client written against `api.md` §1). 28 new tests in
    `apps/api/tests/test_api_routes.py`, including one that FAILS against a
    batch/replay SSE implementation.

  - *A pre-existing bug was found and fixed: the build emitted NO Tailwind
    utilities.* Commit `5e16463` (the static-export commit) dropped the
    `turbopack.rules` block that routes `*.css` through
    `@tailwindcss/turbopack`. There is no `postcss.config.mjs` and no
    `@tailwindcss/postcss` dependency in this package, so the loader IS the
    pipeline. `next build` still reported success, so every gate passed
    while the page rendered unstyled - including the MARKETING route, which
    had no Tailwind CSS at all. Measured: the built stylesheet was 9,413
    bytes with zero utility classes; with the loader restored it is 37,095
    bytes with them. The loader is restored, with the reason recorded in
    `next.config.ts` so the next person does not remove it again.

---

## Day 6 — Slice 6: Evaluation framework (both arms + metrics)

**Targets:** `apps/api/evaluation/`, `experiments/vector/`. **Depends on:** D2, D3,
slices 1–5.

- [ ] `POST /v1/evaluations` → 202 `{evaluation_id, status}`; `GET /v1/evaluations/{id}` →
  per-question rows + aggregates + system stats per `api.md` §4.
- [ ] Frozen 72Q dataset at `data/datasets/v1/{dev,test}.json` (24 dev + 48 test, 7
  categories, ≥3 dev + ≥6 test per category, ≥5 `answerable: false`, seed 42).
  - *Constraint from the Day-3 measured finding:* an `answerable: false` row must
    have EVERY content token absent from the corpus. The gate as specified
    passes any question where one token occurs even once in the corpus
    (`lex = 1.0` from min-max normalization), so a planted unanswerable
    question that shares ordinary vocabulary with the corpus — "fishing
    license" (3 and 18 occurrences) — is answered with 8 citations, and
    `evaluation.md` §4 scores that as abstention = 0 for BOTH arms. Design the
    rows against that constraint, or the refusal metric measures the dataset
    rather than the system. A dataset built this way also has to be honest about
    what it measures: refusing a question whose vocabulary is absent is a
    weaker test than refusing a question that is out-of-domain in substance.
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
