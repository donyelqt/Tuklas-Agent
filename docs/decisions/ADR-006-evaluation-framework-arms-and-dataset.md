# ADR-006: The evaluation framework's arms, its dataset, and what it measures

## Status
Accepted

## Date
2026-10-21

## Context

Day 6 delivers the evaluation framework: `apps/api/evaluation/`,
`experiments/vector/`, the frozen 72-question dataset, and the two
`/v1/evaluations` routes. The framework has to answer one question —
what does the vectorless retrieval do against a dense baseline on the
same questions, with the same model — and it has to answer it in a way
that survives a reader who did not run it.

Four decisions had to be made, and each of them is a place where the
obvious implementation would have produced a number that means less than
it appears to. They are recorded here because each of them is a choice,
not a consequence, and because a later reader needs to know what the
numbers in the run report are and are not.

## Decision

### 1. Arm B is genuinely dense, and the arms differ in more than retrieval

`experiments/vector/` embeds with `BAAI/bge-small-en-v1.5` through
`fastembed==0.9.0` (384 dims, CPU ONNX Runtime, HF revision
`5c38ec7c405ec4b44b94cc5a9bb96e735b38267a`) — the model ADR-002 pins. The
vectors live in a NumPy array and the search is a brute-force cosine:
ADR-002 forbids a vector DATABASE in v1, and a matrix of 2,579 unit
vectors against one query vector is 2,579 dot products, which needs no
database and is EXACT — there is no approximation error to explain away.

**The arms do not differ only in the retrieval mechanism.** Arm A applies
identifier precedence (ADR-005): a query naming a provision puts that
provision first, and Arm A has an `ident` scoring component. Arm B has
NO ident component at all — it embeds the query, so it can match a
provision semantically, but it has no symbolic label channel. The
`direct-lookup` category is therefore **not a like-for-like result**, and
every report says so before it prints a number. The outcome of the
comparison is UNKNOWN, and the framework emits no verdict field, no
"winner" and no blended score: the aggregates are per arm, side by side.

**Arm B's answer path is the SAME agent.** The only substitution is the
`search_nodes` tool, which returns the dense ranking instead of the
vectorless one. The planner, the inspector, the evidence gate, the
context builder and the generation model are the product's own code with
the product's own parameters. A second agent for Arm B would have
differed in more than retrieval, which is the confound the experiment
forbids.

**The dense score is min-max normalized per query**, exactly as the
vectorless arm's `lex` component is (retrieval.md §3). The min-max is
monotonic, so the ranking is the raw ranking; what it buys is a common
SCALE, so the sufficiency gate's 0.15 threshold means the same thing in
both arms. Without it the dense arm's gate would sit on a cosine scale
and the vectorless arm's on a five-component sum scale, and the
abstention metric would compare two different questions.

### 2. The `ref` component is 0 by construction on this corpus, and no category depends on it

The `references_` table is EMPTY on both corpora: ingestion does not
parse reference edges (`apps/api/ingestion/service.py`: "Day 1: no
reference parsing; references_ table stays empty"). The `ref` component's
only edge source is that table, so `ref` is 0 for every node on every
query, and retrieval.md §3's formula runs as `lex + ident + title + 0 +
hier`.

A category that measured the `ref` component would have been
structurally zero for BOTH arms, and the metric would have carried no
information. The `cross-reference` category is therefore built against
the IDENT channel and the lexical co-occurrence instead: a question that
names a provision which another provision cites, where answering it
needs the cited text. The run report states the `ref` = 0 fact
explicitly, because a component breakdown that omits it would leave the
reader to discover it.

### 3. The unanswerable rows are built out of vocabulary the corpus does not contain AT ALL

The Day-6 measured finding is that the abstention gate is LEXICAL: the
`lex` component is min-max normalized per query, so a SINGLE matching
token gives `lex = 1.0` and the sufficiency gate passes. Measured on the
Civil Code:

- "Explain zeppelin airship quantum observatory" → refused
  (`insufficient: true`, `citations: []`)
- "How does a zeppelin airship quantum observatory work?" → **ANSWERED,
  8 citations**

The difference is entirely the scaffolding. "How", "does" and "work"
occur in the corpus; "explain", "zeppelin", "airship", "quantum" and
"observatory" do not. So the constraint is stronger than "every CONTENT
token must be absent": **every token of the question, including the
verb, must be absent from the corpus.**

The dataset's generator MEASURES this against a real ingest of the
corpus PDF and refuses to write a split that fails it. The check is
per-document (retrieval scores one document's nodes), and the runner
applies the stricter whole-store check, so a row cannot become
answerable if the dataset were ever pointed at another document. The
verb matters: "define" occurs in the U.S. Code Title 11 corpus, so a row
that used it would be answerable by vocabulary against that document.

**The honesty this costs.** Refusing a question whose vocabulary is
ABSENT is a WEAKER test than refusing a question that is out-of-domain
in SUBSTANCE. The abstention score the split produces is a lower bound
on the system's refusal quality, not a measurement of it. The split's
own notes say so, and the run report repeats it.

### 4. The dataset is generated by a committed, deterministic script, and the validator is LOUD

`apps/api/evaluation/generate_dataset.py` produces
`data/datasets/v1/{dev,test}.json` from a question pool that is written
out in full in the script, with the split seed 42 and a stratified
per-category shuffle. The script ingests the corpus PDF itself rather
than reading the live store, so the split is reproducible from the PDF
and not from a mutable local artefact.

The validator is LOUD, and it is the thing that would have caught a
silent failure:

- **D1 invariant 2** — every gold resolves to a real ingested node id
  AND starts with `node_{question.document}_`. A gold that does not
  resolve yields `Recall@5 = 0` for every question while the run
  "completes" and nothing fails, which is what PLAN.md calls "the most
  dangerous item". The validator raises and names the offending
  question; it does not warn.
- **D1 invariant 3** — every `question.document` resolves to exactly
  ONE ingested document. A missing slug is a loud failure, never an
  empty gold set.
- the split sizes, the stratification floors, the no-overlap rule, the
  unanswerable count, and the corpus-absence rule.

A validator that warns cannot prevent the failure it was written for.

### 5. The judge scores an answer's claims in ONE model call, and the rows are written as they are produced

Two decisions about the cost of a run, both forced by measurement rather
than chosen for elegance.

**The judge batches.** The single-claim judge asks the model one question
per claim, so an answer of 5 claims costs 5 model calls, a 72-question
split over two arms costs about 700, and on the local CPU model
(`qwen2.5:3b`) that is hours of wall clock for a metric one call can
produce. Measured on the dev split: the single-claim judge took ~150s
per question, and the first attempt at a full run reached question 23 of
24 before it was killed for time.

The batched prompt asks for every claim's verdict in one answer, one
line per claim. The verdicts are still PER CLAIM, against the same
evidence, and a claim the model does not answer for scores 0 — the same
way a parse failure of the single-claim prompt scores 0, because a
dropped claim would RAISE the faithfulness score, which is the wrong
direction for a failure. The batched prompt has its OWN version
(`judge-v1-batch`), because the two prompts are two instruments and a
score produced with one is not comparable with a score produced with the
other.

**The embedder sets its batch size explicitly.** fastembed's default is
256, and at that batch size the ONNX Runtime session tries to allocate a
~2.9 GB attention buffer and dies:
``BFCArena::AllocateRawInternal Failed to allocate memory for requested
buffer of size 2938318848``. The corpus's texts are not the cause — the
mean node text is 292 characters and the longest is 2,870 — so the
allocation is the BATCH, not the input. The embedder uses 32, which
completes on a machine without a GPU.

**The rows are written as they are produced.** The run row and its
metrics are written at the END, so a run killed half way through left
nothing in the store at all. The per-question rows are now appended one
question at a time, so an interrupted run keeps the rows it did produce,
and they are joinable on the same ``(evaluation_id, question_id, arm)``
key either way.

**The generator's absence check reached BOTH corpora.** The check was
already there - the unanswerable rows were checked against the vocabulary
of every OTHER ingested document - but it could never fire, because the
generator ingested ONLY the Civil Code into its temporary store, so
"every other document" was always the empty set. Measured: "tell" (q031),
"explain" (q046) and "enumerate" (q059) are absent from the Civil Code
and PRESENT in the U.S. Code Title 11, so the generated dataset passed
the generator's check and was then REFUSED by the runner, which checks
the whole live store.

The generator now ingests both PDFs, and the check it performs is the
same check the runner performs. The three rows were rewritten against
words neither corpus contains - "narrate", "elucidate", "explicate" - and
the check was NOT weakened to make them pass. The check is by slug
rather than by the primary's document id, because the id is derived from
the file's hash and the primary's id in a freshly built temporary store
is not something to hard-code.

## Alternatives Considered

### Use a lexical fallback for Arm B because `fastembed` was not installed
**Pros:** no model weights to fetch; deterministic; no ONNX Runtime.
**Cons:** the arm would not be dense, and the whole report would be a
comparison between a vectorless system and a lexical system wearing the
label "vector".
**Rejected:** `fastembed==0.9.0` was installed and verified end to end
(384 dims, L2 norm 1.0, revision pinned), and the ADR-002 model decision
was already made. A mislabelled arm is a research-credibility failure,
and the fallback was never needed.

### Build the cross-reference category on the `ref` component
**Pros:** it would have measured the component the spec names.
**Cons:** `ref` is structurally 0 on this corpus, so the category would
have been a constant.
**Rejected:** the category is built on the ident channel instead, and
the report says why. The alternative would have produced a metric that
looked like a result and was a constant.

### Put the unanswerable rows' tokens in the "content" set only
**Pros:** the rows would read like natural questions ("How is a
zeppelin airship registered?").
**Cons:** measured, they are ANSWERED with 8 citations, and the
abstention metric would measure the dataset rather than the system.
**Rejected:** the rows are built out of vocabulary the corpus does not
contain at all, and the dataset's notes state what that weakens.

### Reuse the golden 10Q wholesale as the dataset
**Pros:** the golds are already verified to resolve and to land in the
top-5.
**Cons:** 10 questions cannot cover 7 categories at the required
stratification, and the golden set is a smoke gate, not a benchmark.
**Rejected:** the golden 10Q's questions and golds are REUSED where they
fit, inside a 72-question dataset with the required stratification.

### Derive the config id from a named field list rather than the whole file
**Pros:** a shorter, more readable derivation.
**Cons:** a field the list forgot would change the run without changing
the id, which is exactly what a config id exists to prevent.
**Rejected:** the id is derived over the WHOLE config minus the id
itself, so it changes when anything that could affect the run changes.
`scripts/check_eval_config.py` uses the same derivation, so the two
agree by construction rather than by a shared list that could drift.

## Consequences

- The run report names the run's identity (n, the split seed, the model
  and backend, the judge prompt version, the config id), the arms'
  asymmetry, both arms' numbers, and the measured limitations. It states
  no verdict on the comparison, and the framework exposes no field that
  could carry one.
- The `direct-lookup` category's per-arm numbers are not a like-for-like
  result, and the report says so before it prints them.
- The abstention score is a lower bound on refusal quality, not a
  measurement of it.
- The `ref` component is 0 on this corpus, so the reported component
  breakdown is `lex + ident + title + 0 + hier`.
- The dataset's unanswerable rows are deliberately unusual in their
  wording. That is the cost of a metric that measures the system, and
  the split says so in its own artefact.
- `fastembed`, `onnxruntime` and `numpy` are imported ONLY under
  `experiments/vector/`, which only `apps/api/evaluation/` may import.
  `scripts/check_no_vectors.py` enforces both directions. The `numpy`
  ban in the quarantined directories is a TIGHTENING of CONSTRAINTS.md's
  named deny list: the deny list names LIBRARIES, and a cosine over two
  normalised vectors is `np.dot(a, b) / (norm(a) * norm(b))`, which the
  existing `cosine_similarity` grep rule cannot see. The tightening is
  the same rule in a different spelling, and it is recorded here rather
  than applied silently.
