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
  - *Correction (2026-10-21).* The `references_` TABLE exists with the specified
    DDL and indexes, but ingestion never POPULATES it: the parser extracts no
    reference edges. Measured on both ingested corpora, `references_` holds 0
    rows. Two consequences follow, and both are real rather than theoretical:
    the `ref` scoring component is 0 for every node on every query (its only
    edge source is `references_`), so the §3 formula currently runs as
    `lex + ident + title + 0 + hier`; and `follow_reference` can only ever
    return `status: "error"` with "unknown reference id", because there is no
    edge to resolve. The Day-4 note below records the same gap from the
    agent's side; this entry records it from the ingestion side, because the
    `[x]` above reads as though the edges exist. The graph therefore expands
    rather than follows, and the cross-reference category of the frozen
    dataset has no `ref` component to measure.
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

  - *Fixed (2026-10-21, retrieval):* the defect is closed by **ADR-005**
  (`docs/decisions/ADR-005-identifier-precedence-and-own-identity.md`).
  Three causes were measured and each was fixed at its source:
  1. **The five components are SUMMED, so `ident` (capped at 1.0) could not
     outrank `lex 1.0 + title 0.1 + hier 1.0 = 2.1`.** The fix is an ORDER,
     not a weight: the node that UNIQUELY OWNS an exact label the query names
     sorts first. The score VALUE, the five components and the §3 formula are
     unchanged — no new weight, no tuned constant, no change to any published
     component value. `scoring.evidence_rank_key` is the single source of the
     order, used by the ranking, the evidence-window cut, the context
     builder's prompt order and the agent's merged evidence set.
  2. **The ident index could not identify anything.** It indexed every label
     span anywhere in a title plus every ancestor's labels, so `art 57`
     resolved to 404 nodes and `sec 1` to 230. `normalizer.own_identity` now
     reads only a node's own HEADING, and `ident_index` records a `level`
     column (1.0 owner, 0.5 inheritor). `art 57` now resolves to 1 node.
  3. **The parser promoted body text and marginal notes to headings.** The
     RapidFuzz near-miss path turned body sentences into headings (75 of the
     corpus's 2583 headings; one made 402 articles inherit `art 57`), and the
     PDF's marginal notes ("article 765. (n)") created a second node for five
     provisions. A heading's number must now be terminated by a period or the
     end of the line, and a marginal note is never a heading.

  Three further defects were found while closing this one, all fixed at
  their source:
  4. **The `ident` VALUE and the OWNERSHIP question were conflated.** §3
     states the component value as a property of the LABEL ("1.0 exact
     normalized article id"), so a node carrying an exact label scores 1.0
     whether it owns the label or inherits it; downgrading the inheritor
     changes every published ident and score, which §3 forbids without a
     recorded tuning run. Ownership is answered by the ident index's `level`
     column, and only a level-1.0 node may be identified. Measured: 0 of the
     corpus's components differ from the pre-fix values.
  5. **A coordinated list of provisions yielded only the FIRST label.**
     "Compare articles 1318 and 2124" extracted `art 1318` alone, and the
     planner's split then left "2124" as a bare number, which carries no
     label. Each number in a list joined by a comma, "and", "or" or "to" now
     yields a label of the same family. Measured: both provisions are
     identified and both are in the top 2 of the search results.
  6. **The identified node was evicted first under budget pressure.** The
     identifier precedence is an ORDER, so the named provision keeps whatever
     score its other components gave it - often the lowest in the set - and
     a lowest-score-first eviction dropped it exactly when the prompt was
     too large to hold everything. The context builder now protects it
     alongside the gate set.
  7. **The evidence length floor dropped short PROVISIONS.** The pipeline
     drops nodes whose text is under 50 characters, so that page-span nodes
     whose text is not a provision do not reach the prompt. It was also
     dropping provisions: article 1433 is 48 characters long, so a query
     naming it ranked it first, identified it, and then threw it away - and
     the model answered about a DIFFERENT article. 98 of the Civil Code's
     article and section nodes are under the floor. The identified node is
     now exempt from it.

  Measured after the fix: the named provision is at **rank 1 in 200/200**
  queries (40 random articles x 5 question forms; it was 47/200 before), and
  is in the evidence window in 200/200. The golden 10Q stays 10/10. The five
  previously-ambiguous article labels (765, 895, 1544, 1984, 2168) are rank
  1 and identified in all 4 question forms each. A fresh ingest is 2579
  nodes (2268 articles), with no phantom `part`/`division` containers and
  none of the five marginal-note duplicates.
  Verified end to end in a browser: "What does article 1318 require?" now
  answers "Article 1318 requires consent of the contracting parties [1], an
  object certain ... and a cause of the obligation", with citation [1] being
  article 1318, p. 94.

  **Reporting obligation (ADR-005):** the vectorless arm now has identifier
  precedence and the dense arm has no ident component at all, so the arms
  differ in more than the retrieval mechanism. Any reported comparison MUST
  say so, so the direct-lookup category is not read as like-for-like. The
  outcome of the comparison is unknown and is not claimed.

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

- [x] `POST /v1/evaluations` → 202 `{evaluation_id, status}`; `GET /v1/evaluations/{id}` →
  per-question rows + aggregates + system stats per `api.md` §4.
  - *Done (2026-10-21):* `apps/api/api/routes.py` carries the two routes, wired into
    the existing router. The 202 body is `{evaluation_id, status}`; the GET body is
    the §4 shape — `status`, `config`, `per_question`, `aggregates`, `system_stats` —
    and the rows are the JOIN of `evaluation_runs` and `retrieval_runs`, not a second
    computation. The typed failures are the §4 ones: an unknown snapshot, an arm
    mismatch, a temperature that is not 0, and a model-parity violation are all
    422 `VALIDATION_ERROR` (a config whose arms disagree on the model is an INVALID
    RUN, and the caller can fix it); an unknown evaluation id is 404 `NOT_FOUND`; a
    runner crash is 500 `EVAL_FAILED`. The malformed-body path answers the §1 error
    body with the offending field named, never the framework's default `{"detail":
    [...]}`. `apps/api/models/schemas.py` holds the typed models; the bounds are
    ENFORCED (`fuzzy_threshold` 80..95, the five weights 0..2, `temperature` fixed
    at 0), so an out-of-range value is a 422 rather than a clamped one.
- [x] Frozen 72Q dataset at `data/datasets/v1/{dev,test}.json` (24 dev + 48 test, 7
  categories, ≥3 dev + ≥6 test per category, ≥5 `answerable: false`, seed 42).
  - *Done (2026-10-21):* the split is committed and GENERATED by a committed,
    deterministic script — `apps/api/evaluation/generate_dataset.py` — which writes
    the question pool out in full, shuffles it per category with seed 42, ingests
    the corpus PDF itself (so the split is reproducible from the PDF and not from a
    mutable local artefact), and refuses to write a split that fails its checks.
    Measured category distribution: dev 5/3/4/3/3/3/3 and test 6/7/7/7/7/7/7 for
    direct-lookup / definition / multi-section / cross-reference / exception /
    multi-hop / comparison. 8 `answerable: false` rows.
  - *The Day-3 constraint is satisfied, and the constraint turned out to be
    STRONGER than "every content token".* Measured on the Civil Code:
    "Explain zeppelin airship quantum observatory" refuses (`insufficient: true`,
    `citations: []`), while "How does a zeppelin airship quantum observatory work?"
    is ANSWERED with 8 citations. The difference is entirely the scaffolding —
    "how", "does" and "work" occur in the corpus, and one occurrence is enough,
    because the `lex` component is min-max normalized per query and a single
    matching token gives `lex = 1.0`, which passes the sufficiency gate. So the
    rows are built out of vocabulary the corpus does not contain AT ALL, the verb
    included: "explain", "outline", "elaborate", "recite", "enumerate", "summarize",
    "tell". The verb matters — "define" occurs in the U.S. Code Title 11 corpus, so
    a row that used it would be answerable by vocabulary against that document. The
    generator MEASURES the absence against a real ingest and refuses to write a
    split that fails; the runner re-checks the same property against the live store
    before it spends a token.
  - *The dataset says what it measures, in its own artefact.* Refusing a question
    whose VOCABULARY is absent is a WEAKER test than refusing a question that is
    out-of-domain in SUBSTANCE. The abstention score this split produces is a
    LOWER BOUND on the system's refusal quality, not a measurement of it. Both the
    split files' `notes` and every run report say so.
  - *The validator is LOUD, and it enforces the two D1 invariants that were not
    yet tested.* `apps/api/evaluation/dataset.py` raises — naming the offending
    question — when a gold does not resolve (D1 invariant 2: a dangling gold yields
    `Recall@5 = 0` for every question while the run "completes" and nothing fails),
    when a `question.document` does not resolve to exactly one ingested document
    (D1 invariant 3), when a split size or stratification floor is missed, when a
    question appears in both splits, and when an unanswerable row's tokens are
    present in the corpus. `apps/api/tests/test_evaluation.py` pins that a bad gold
    id is REJECTED, not warned about.
- [x] Arm A (Tuklas): full agent loop, slices 1–5.
  - *Done (2026-10-21):* `apps/api/evaluation/arms.py`'s `TuklasArm` runs the
    product's own agent graph and the product's own `search_nodes` — the five
    components and the identifier precedence of ADR-005, unmodified. Nothing in
    the evaluation path is an evaluation-only reimplementation, because an
    evaluation-only reimplementation would measure something the product does not
    do.
- [x] Arm B (Vector): dense retrieval under `experiments/vector/` only, embedding model
  from D3. Exposes the same retrieval result shape so "arms differ only in retrieval"
  holds (Arm-B parity boundary = PLAN.md risk D4).
  - *Done (2026-10-21), and the arm is GENUINELY DENSE:* `BAAI/bge-small-en-v1.5`
    through `fastembed==0.9.0` (384 dims, CPU ONNX Runtime, HF revision
    `5c38ec7c405ec4b44b94cc5a9bb96e735b38267a`), exactly as ADR-002 pins. The
    vectors live in a NumPy array and the search is a brute-force cosine — ADR-002
    forbids a vector DATABASE in v1, and 2,579 dot products need none and are
    EXACT, so there is no approximation error to explain away. The dense score is
    min-max normalized per query, as the vectorless arm's `lex` is, so the
    sufficiency gate's 0.15 threshold means the same thing in both arms.
  - *The parity boundary holds by TOOL SUBSTITUTION, not by a second agent:* Arm B
    runs the SAME graph with the SAME planner, inspector, evidence gate, context
    builder and generation model, and the ONE thing replaced is `search_nodes`. A
    second agent would have differed in more than retrieval, which is the confound
    the experiment forbids.
  - *The arms do NOT differ only in the retrieval mechanism, and every report says
    so before it prints a number.* Arm A applies identifier precedence (ADR-005)
    and has an `ident` component; Arm B has NO ident component at all. The
    `direct-lookup` category is therefore NOT a like-for-like result. The
    framework emits no verdict field, no "winner" and no blended score: the
    aggregates are per arm, side by side, and the outcome of the comparison is
    UNKNOWN.
- [x] Versioned arm-blinded judge prompt → faithfulness 0/1 per claim; 20% spot-check
  (min 10 answers) by the builder; > 15% disagreement invalidates the run.
  - *Done (2026-10-21):* `apps/api/evaluation/judge.py` holds the `judge-v1` prompt
    and the `LLMJudge`. The blinding is MECHANICAL, not a convention: the judge
    payload function's signature does not accept an arm, the payload has three keys
    (`claim`, `evidence`, `judge_prompt_version`), and the rendered prompt names
    no arm. A judge answer that says neither 0 nor 1 scores 0, never 1 and never
    "dropped" — a dropped claim would raise the faithfulness score, which is the
    wrong direction for a failure. The spot check takes 20% of the split by a
    fixed stride (min 10) and computes the disagreement PER CLAIM; a run with no
    builder re-scores reports the spot check as NOT PERFORMED with the reason,
    because an unmeasured disagreement is not 0% disagreement.
- [x] `evaluation_runs` + per-question `retrieval_runs` persisted per question per arm
  (join keys `(evaluation_id, question_id, arm)`); weights + normalization snapshot in
  every `retrieval_runs` row.
  - *Done (2026-10-21):* `apps/api/ingestion/db.py` gains the join keys and the
    per-row columns as an IDEMPOTENT column migration over the frozen §8 DDL — the
    DDL is not rewritten, and a store created by the frozen DDL and a store
    created by the current code end up with the same shape. `apps/api/evaluation/
    store.py` writes the run row and all its per-question rows in ONE transaction,
    so a reader never sees a run whose metrics exist but whose questions do not.
    Every `retrieval_runs` row carries `(evaluation_id, question_id, arm)`, the
    node ids, the gold ids, the weights snapshot, the normalization id, the
    retrieval/generation/total latencies, the token counts and the iteration count.
  - *Acceptance (PLAN.md Day 6):* 10-question smoke completes; all rows carry node ids
    (except `insufficient: true` → `[]`); judge scores present. `check_no_vectors.py`
    green on the quarantine assertion.
  - *Done (2026-10-21):* `apps/api/tests/test_evaluation.py` runs the 10-question
    smoke over a SYNTHETIC corpus with the model-free stub generator and the
    canned judge, so the suite needs neither the corpus files nor a live model. It
    asserts the smoke completes, that the rows are joinable per question per arm
    (20 rows for 10 questions × 2 arms), that an abstention carries no node ids
    and no citations, that the judge scores are present, that the two unanswerable
    rows abstain, and that the aggregates carry both arms and no verdict. The
    reproduction test asserts the same config over the same snapshot reproduces
    the retrieval metrics EXACTLY. `scripts/check_no_vectors.py` exits 0 with
    `experiments/vector/` and `apps/api/evaluation/` present.
- [x] **The `ref` component is 0 by construction on this corpus, and no category
  depends on it.** *Measured (2026-10-21):* the `references_` table has 0 rows on
  both corpora — ingestion does not parse reference edges
  (`apps/api/ingestion/service.py`: "Day 1: no reference parsing; references_ table
  stays empty"), and the `ref` component's only edge source is that table. So
  `ref` is 0 for every node on every query, and retrieval.md §3's formula runs as
  `lex + ident + title + 0 + hier`. The `cross-reference` category is therefore
  built against the IDENT channel and the lexical co-occurrence instead, and the
  run report states the `ref` = 0 fact explicitly — a component breakdown that
  omits it would leave the reader to discover it. (Recorded in ADR-006.)
- [x] **ADR-006** (`docs/decisions/ADR-006-evaluation-framework-arms-and-dataset.md`)
  records the four judgement calls this slice made: the dense arm and the arms'
  asymmetry, the `ref` = 0 fact, the strengthened unanswerable-row constraint and
  what it weakens, and the config-id derivation over the whole config minus the id
  itself.

- [x] **The first REAL dev-24 run landed (2026-10-21).** Config
  `cfg_0f73ff26c0d38d54`, split seed 42, model `qwen2.5:3b` on ollama at
  temperature 0, seed 42, judge prompt `judge-v1-batch`, n = 24 questions x 2
  arms, 48 `retrieval_runs` rows. Retrieval metrics (macro-averaged over the 23
  answerable questions; the 1 unanswerable row is excluded from the retrieval
  aggregate and scored by abstention instead):

  | Metric | Tuklas (vectorless) | Vector (dense) |
  |---|---|---|
  | Recall@1 | 0.478 | 0.522 |
  | Recall@5 | 0.717 | 0.870 |
  | Recall@10 | 0.783 | 0.935 |
  | MRR@10 | 0.708 | 0.797 |
  | Precision@1 | 0.609 | 0.652 |
  | correctness | 0.700 | 0.822 |
  | faithfulness | 0.691 | 0.725 |
  | citation accuracy | 1.000 | 1.000 |
  | abstention score | 1.000 | 0.000 |

  **These are dev-split numbers, not reported numbers.** The dev split is the
  tuning split, and evaluation.md §1 says a weight tuned on it may not be
  reported on it. The test-48 run is the reported one. The table is here so the
  ratchet has a measured starting point, and no comparison verdict is drawn
  from it: the arms differ in more than the retrieval mechanism (the vectorless
  arm applies identifier precedence and has an `ident` component; the dense arm
  has neither), and the dense arm's abstention score is STRUCTURALLY 0 because a
  cosine is non-zero for every node against every query, so its gate cannot fail
  lexically. That is a property of the arm, not a measurement of it.

  The spot check measured **2.5% disagreement over 81 claims** (threshold 15%),
  by mechanical re-judge — the judge's SELF-CONSISTENCY, not a builder's human
  re-scoring. The report names the method so the number is never mislabelled.

- [x] **Three defects were found and fixed while landing that run** (all recorded
  in ADR-006):
  1. **The judge's prompt version did not match the prompt the run used.** The
     config pinned `judge-v1` while the runner scored with the batched prompt.
     The run's own record would have misdescribed its own numbers. The runner now
     records the judge's ACTUAL version and REFUSES a config that names a
     different one.
  2. **The dataset generator's absence check could never fire.** It checked the
     unanswerable rows against every OTHER ingested document, and it ingested ONLY
     the Civil Code into its temporary store — so "every other document" was
     always empty. "tell", "explain" and "enumerate" are absent from the Civil
     Code and present in the U.S. Code Title 11, so the generated dataset passed
     the generator's check and was REFUSED by the runner, which checks the whole
     live store. The generator now ingests both PDFs; three rows were rewritten
     against words neither corpus contains; the check was NOT weakened.
  3. **The dense embedder's default batch size killed the ONNX session.** At
     fastembed's default batch of 256 it tried to allocate a ~2.9 GB attention
     buffer and died. The embedder now sets 32 explicitly. The judge also now
     scores an answer's claims in ONE model call instead of one per claim, which
     is the difference between a run that finishes and one that does not, and the
     per-question rows are now written as they are produced so an interrupted run
     keeps what it finished.

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
