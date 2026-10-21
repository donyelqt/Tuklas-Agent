# Evaluation run reports

The reports of the evaluation runs, one file per run.

- `v1-test.md` - the REPORTED run: the test-48 split, both arms, config
  `cfg_e86e8bfbc803c860`, split seed 42, model `qwen2.5:3b` on ollama at
  temperature 0, seed 42, judge prompt `judge-v1-batch`. `v1-test.json`
  is the same run's aggregates in machine-readable form.

A report publishes both arms' numbers and NO verdict. It states what a
reader needs BEFORE it prints a number:

- **each arm's SCOPE** - how many nodes it ranks, how large the
  vectorless arm's lexical corpus is, and what SURFACE it scores. The
  vectorless arm ranks the document's whole node set; the dense arm
  embeds the lexical node set, and the two score DIFFERENT surfaces over
  that corpus;
- **the arms' CHANNEL asymmetry** - THREE of the vectorless arm's five
  scoring components are channels the dense arm structurally cannot have:
  `ident` (ADR-005's identifier precedence), `title` (Jaccard overlap with
  the node's heading) and `hier` (the node shares a parent with a
  lex-top-3 node). The dense arm's `title` and `hier` are 0.0 for every
  node by construction. The last two are not a corner case: the
  vectorless arm's `title` fires on 96.5% of its hits and its `hier` on
  97.8%, and zeroing both changes the top-10 order on 21 of 23 dev
  questions and 40 of 41 test questions. The `direct-lookup` category is
  therefore not a like-for-like result;
- **the units of work the two latencies measure** - the dense arm's is a
  matrix product over a PRE-BUILT index, the vectorless arm's is a
  full-document BM25 RE-SCORE per query, and neither includes the build
  of its own index.

And after the numbers:

- **the spot check's METHOD** - the harness's default is a MECHANICAL
  re-judge (the same versioned prompt, the same backend, the same
  evidence), which measures the judge's SELF-CONSISTENCY. It is NOT the
  builder's human re-score that `evaluation.md` §4 asks for, and the
  report says so in those words. A reader who saw "disagreement: 0.017"
  without the method would reasonably assume a human had validated the
  judge;
- **`n_judged`** next to `faithfulness`, because the two differ: a
  refusal carries no claim to judge;
- the measured limitations, including the ones that change what the
  numbers mean:
  - the `ref` scoring component is 0 for every node on this corpus, because
    ingestion does not populate the `references_` table;
  - the abstention score measures the ability to refuse a question whose
    VOCABULARY is absent, which is a lower bound rather than a measurement
    of refusal quality;
  - the dense arm's sufficiency gate cannot fail on LEXICAL grounds, so a
    refusal by the dense arm is not a refusal to answer; its gate can still
    fail on a non-lexical one (the evidence gate's 50-character length
    floor empties the kept set when the cosine ranks short SECTION nodes
    at the top), so its abstention score is a property of the arm's
    ranking, not a measurement of its refusal quality, and the two arms'
    abstention scores are not comparable;
  - the GENERATION half of the reproducibility acceptance ("generation
    within ±5pp", `evaluation.md` §8) is UNCHECKED by anything in this
    repository: it needs a live local model backend, and the report says
    so rather than letting the reader assume the whole acceptance was met.

**The outcome of the vectorless-versus-dense comparison is not claimed
here.** The numbers are what was measured; what they mean is a reading.

## Reproducing a report

```
.venv/Scripts/python.exe -m apps.api.evaluation.run_eval \
  --config apps/api/evaluation/fixtures/example-run-config.json \
  --snapshot v1-test --db data/tuklas.sqlite \
  --report data/eval-reports/v1-test.md --json data/eval-reports/v1-test.json
```

The same config id yields the same retrieval metrics EXACTLY
(evaluation.md §6); the generation metrics are within a few points. The
run's own rows are in the `evaluation_runs` and `retrieval_runs` tables
of the store, joinable on `(evaluation_id, question_id, arm)`. The store
is a local artifact and is not committed.
