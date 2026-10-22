# Evaluation run reports

The reports of the evaluation runs, one file per run.

**CURRENT**

- `v1-test.md` - the REPORTED test run: the test-48 split, both arms, run
  `eval_447127eb`, config `cfg_e86e8bfbc803c860`, split seed 42, model
  `qwen2.5:3b` on ollama at temperature 0, seed 42, judge prompt
  `judge-v1-batch`. `v1-test.json` is the same run's aggregates in
  machine-readable form.
- `v1-dev.md` - the dev run: the dev-24 split, both arms, run
  `eval_ff373231`, config `cfg_0f73ff26c0d38d54`, split seed 42, model
  `qwen2.5:3b` on ollama at temperature 0, seed 42, judge prompt
  `judge-v1-batch`. `v1-dev.json` is the same run's aggregates in
  machine-readable form.

**PRESERVED AND QUARANTINED - not a baseline, not comparable, not reproducible**

- `v1-test-OLD-INSTRUMENT-AND-STORE.md` - the SUPERSEDED test report, run
  `eval_a80167ab`, config `cfg_e86e8bfbc803c860`, judge prompt
  `judge-v1-batch`. Recovered from git (`eb36a52:data/eval-reports/v1-test.md`).
- `v1-dev-OLD-INSTRUMENT-AND-STORE.md` - the SUPERSEDED dev report, run
  `eval_083a4c15`, config `cfg_5a3d6973caf7bd43`, judge prompt `judge-v1`.
  RECONSTRUCTED by rendering the stored run with the OLD renderer
  (`eb36a52:apps/api/evaluation/report.py`); the method was validated by
  reproducing the published test file byte-for-byte.

Both carry a header that says what produced them and why their numbers must
not be diffed against the current ones.

A report publishes both arms' numbers and NO verdict. It states what a
reader needs BEFORE it prints a number:

The two arms, side by side, before the detail below:

| Dimension | Vectorless arm (Tuklas) | Dense baseline arm |
| --- | --- | --- |
| Ranking signal | five components: `lex`, `ident`, `title`, `ref`, `hier` (`docs/retrieval.md` §3) | one min-max-normalized cosine (`experiments/vector/index.py`) |
| Identifier precedence | yes: a query that names a provision puts that provision first (`docs/decisions/ADR-005-identifier-precedence-and-own-identity.md`) | none (`docs/decisions/ADR-006-evaluation-framework-arms-and-dataset.md` §1) |
| Candidate set | the whole 2,579-node tree, over a 2,392-node lexical corpus | the 2,392 lexical nodes |
| Lexical score | per-document BM25 over leaf-node bodies and section-intro nodes | none: the cosine occupies the `lex` slot (`experiments/vector/index.py`) |
| Agent | the same LangGraph graph and the same seven tools | the same graph and the same seven tools; only `search_nodes` is substituted (`apps/api/evaluation/arms.py`) |
| Generation and judge | the same model, backend, temperature 0, seed 42, and the same arm-blinded judge (`apps/api/evaluation/judge.py`) | the same model, backend, temperature 0, seed 42, and the same arm-blinded judge |
| Metric | the same instrument (`answer-node-union-v1`), over the node ids the answer was produced from (`apps/api/evaluation/arms.py`) | the same instrument (`answer-node-union-v1`), over the node ids the answer was produced from |

Note on the channel count: `ref` is 0 for every node in BOTH arms on this
corpus - the vectorless arm's `ref` is 0 because ingestion does not populate
the `references_` table, and the dense arm's `ref` is 0.0 by construction -
so the channels the dense arm structurally cannot have are THREE (`ident`,
`title`, `hier`), as the detail below states.

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
  - the two arms' abstention scores are MEASURED COUNTS of the unanswerable
    questions each arm refused, not constants of the arm - the report
    prints the count beside the ratio. The dense arm's gate cannot fail on
    the LEXICAL condition, so most of the difference between the two arms'
    scores is the gate's TRIGGER rather than the model's willingness to
    answer; but the dense arm's gate is not unconditional, and it can fail
    on a non-lexical one - the evidence gate's 50-character length floor
    (which empties the kept set when the cosine ranks short SECTION nodes
    at the top) and the top-8 evidence window (which can evict every
    supporter of one sub-question);
  - `citation_accuracy` is a MEASUREMENT OF A PAIR - the model's citations
    AND the support rule's sensitivity - and the report carries the
    MEASURED BREAKDOWN of its failures, because the ratio alone is
    misleading. The breakdown is under ONE UNIFORM RULE (R9), applied
    identically to every (run, arm) pair, and the rule is stated in full
    so a reader can reproduce it from the stored run alone. Measured: on
    test-48 the vectorless arm's 56 failing claims are 3 MARKER, 7 MODEL
    (13%) and 46 REMAINDER; the dense arm's 62 are 4 marker, 11 model
    (18%) and 47 remainder. On dev-24: vectorless 45 failures = 2 marker,
    12 model (27%), 31 remainder; dense 52 failures = 0 marker, 13 model
    (25%), 39 remainder. So the number is DOMINATED BY THE SCORER'S
    STRICTNESS, and reading 0.542 and 0.458 as "the model cites badly 54%
    and 46% of the time" would be WRONG. The number is a LOWER BOUND on
    citation quality, not a measurement of it, and that lower bound is
    loose by DIFFERENT AMOUNTS for the two arms, so the two arms' numbers
    are not comparable with each other either. A PREVIOUS version of the
    report published a different breakdown (52/1/3 and 34/24/4 on test-48)
    and the sentence "the dense arm is the one with a real miscitation
    problem, at 39% of its failures"; both are WITHDRAWN, because the
    two published numbers came from two DIFFERENT rules. A search of
    768,848 rule variants found NO rule reproducing all four published
    splits, and NO rule reproducing the two arms' published splits of the
    same split - 3,144 rules reproduce both vectorless published splits and
    336 reproduce both dense published splits, and the two sets do not
    intersect. The dense split could not have come from the rule the
    previous note stated at all: that rule required the SENTENCE to name an
    article, and only 6 of the dense arm's 58 resolvable failing rows on
    test-48 have a sentence naming a different article, so any rule of that
    shape reaches at most 6 - the published split said 24;
  - the GENERATION half of the reproducibility acceptance ("generation
    within ±5pp", `evaluation.md` §8) is UNCHECKED by anything in this
    repository: it needs a live local model backend, and the report says
    so rather than letting the reader assume the whole acceptance was met.

**The outcome of the vectorless-versus-dense comparison is not claimed
here.** The numbers are what was measured; what they mean is a reading.

  **A report here is a MEASUREMENT OF A RUN, and a run is only
  comparable with another run when the INSTRUMENT and the STORE STATE
  are the same.** Both are properties of the harness and the working
  tree, not of the questions, and a reader cannot see either from the
  run's row. So every report states, in its run identity block, which
  instrument produced its retrieval metric and which lex path produced
  its scores, and a report that replaced an earlier one states what
  changed and what a reader may not conclude. The
  `-OLD-INSTRUMENT-AND-STORE` files are the worked example: preserved,
  quarantined, and not comparable with the current ones.

## Reproducing a report

```
.venv/Scripts/python.exe -m apps.api.evaluation.run_eval \
  --config apps/api/evaluation/fixtures/example-run-config.json \
  --snapshot v1-test --db data/tuklas.sqlite \
  --report data/eval-reports/v1-test.md --json data/eval-reports/v1-test.json
```

The same config id does **not** yield the same retrieval metrics. The two test
runs share `cfg_e86e8bfbc803c860` and differ by 0.061 on recall@5, because
`code_version` is the constant `day6-eval-v1` and the config id does not pin the
harness. evaluation.md §6 states the stronger claim; it holds for the retrieval
*ranking* (a pure function of corpus, query, weights, which is deterministic) but
not for the *instrument* that produced these numbers. Which run is which, and
what the difference is made of, is in `docs/VERSIONS.md`. The run's own rows are
in the `evaluation_runs` and `retrieval_runs` tables of the store, joinable on
`(evaluation_id, question_id, arm)`. The store is a local artifact and is not
committed.
