# Evaluation run reports

The reports of the evaluation runs, one file per run.

- `v1-test.md` - the REPORTED run: the test-48 split, both arms, config
  `cfg_e86e8bfbc803c860`, split seed 42, model `qwen2.5:3b` on ollama at
  temperature 0, seed 42, judge prompt `judge-v1-batch`. `v1-test.json`
  is the same run's aggregates in machine-readable form.

A report publishes both arms' numbers and NO verdict. It states the arms'
asymmetry before it prints a number - the vectorless arm applies
identifier precedence (ADR-005) and has an `ident` scoring component,
and the dense arm has neither, so the `direct-lookup` category is not a
like-for-like result - and it names the measured limitations, including
the three that change what the numbers mean:

- the `ref` scoring component is 0 for every node on this corpus, because
  ingestion does not populate the `references_` table;
- the abstention score measures the ability to refuse a question whose
  VOCABULARY is absent, which is a lower bound rather than a measurement
  of refusal quality;
- the dense arm's sufficiency gate cannot fail on lexical grounds, so its
  abstention score is STRUCTURALLY 0 - a property of the arm's ranking,
  not a measurement of it.

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
