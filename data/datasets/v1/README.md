# The frozen 72-question benchmark (evaluation.md §1-§2)

`dev.json` (24 questions) and `test.json` (48 questions) are the frozen
split. Schema per `evaluation.md` §2: `id`, `question`, `document`,
`category`, `gold_nodes`, `expected_claims`, `answerable`, `notes`.

- **Split seed 42**, stratified by category. Every category has at least 3
  dev and 6 test rows. No question is in both splits.
- **The dev split is the tuning split.** A weight tuned on it may not be
  reported on it. The test split is the reported one and is touched once
  per frozen config.
- **Regenerate with:**
  ```
  .venv/Scripts/python.exe -m apps.api.evaluation.generate_dataset
  ```
  The generator is committed beside the split so the split is
  reproducible and auditable, and it regenerates the files byte for byte.
  It ingests both corpus PDFs itself, so the split is a function of the
  PDFs and not of a mutable local store.
- **The validator is loud.** A gold node that does not resolve to a real
  ingested node, a document slug that resolves to zero or two documents, a
  category floor that is not met, or an unanswerable row whose vocabulary
  occurs in either corpus all STOP the generator before it writes
  anything. A gold that does not resolve makes `Recall@5` structurally 0
  for every question while the run "completes" and nothing fails, which
  is what `PLAN.md` calls the most dangerous failure mode.

## The unanswerable rows

Eight rows carry `answerable: false`. Every token of every one of them -
including the verb - is absent from BOTH corpora, measured against the
corpus's own BM25 tokenizer. The abstention gate is lexical and the
`lex` component is min-max normalized per query, so a single matching
token gives `lex = 1.0` and the gate passes.

**What this weakens.** Refusing a question whose vocabulary is absent is
a WEAKER test than refusing a question that is out-of-domain in
substance. The abstention score this split produces is a lower bound on
the system's refusal quality, not a measurement of it. The split's own
`notes` say so on every row, and the run report repeats it.

## The reported run

`data/eval-reports/v1-test.md` is the reported test-48 report. It
publishes both arms' numbers and no verdict, and it states the arms'
asymmetry before it prints them: the vectorless arm applies identifier
precedence and has an `ident` component, and the dense arm has neither,
so the `direct-lookup` category is not a like-for-like result.
