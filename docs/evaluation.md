# Tuklas Agent - Evaluation Framework (F7, rev 2)

**Status:** Draft for review (rev 2 - post adversarial review). Answers PRD US-4.
**Principle:** same dataset, same questions, same golds, same generation model, same judges → both arms → reported metrics. Anything else is a demo, not an experiment.

## 1. Design

```text
Benchmark dataset v1 (72Q: 24 dev + 48 test; 7 categories; fixed gold_nodes +
expected_claims + answerable flag) - frozen snapshot id per run
        │
        ├── Arm A: Tuklas Agent (vectorless retrieval layer: `lex` + `ident` +
            `title` + `ref` + `hier`, with ADR-005's identifier precedence)
        └── Arm B: dense-cosine-only retrieval layer (experiments/vector/ ONLY)
            behind the SAME agent and the same 7 tools, `search_nodes` swapped
        │   identical generation model + backend + temperature 0 + seed (ADR-004)
        ▼
Metrics: retrieval (Recall@K, Precision@K, MRR@10) │ generation
(faithfulness blinded, correctness, citation accuracy) │ system
(latency p50/p95 split, tokens) - evaluation_runs + per-question retrieval_runs
```

- The arms SHARE the agent (same graph, same seven tools, only `search_nodes` swapped), the generation model + backend + temperature + seed, the arm-blinded judge, the retrieval-metric instrument (`answer-node-union-v1`) and the corpus-membership rule (`in_lex_corpus`). They DIFFER in the SIGNAL SET the retrieval layer can use: Arm A scores the five components of retrieval.md §3 (`lex`, `ident`, `title`, `ref`, `hier`) and applies ADR-005's identifier precedence; Arm B scores one min-max-normalized cosine, with `ident`/`title`/`ref`/`hier` at 0.0 and no identifier precedence. The arms therefore differ in more than the retrieval mechanism, so a per-category difference is a difference of surface and channel as well as of mechanism (ADR-006 §1). The scope and channel asymmetry a report publishes beside the numbers is specified in §7.
- Different models across arms = invalid run (CI check on run config).
- Dev (24Q): weight tuning + iteration. Test (48Q): reported numbers, touched once per frozen config. Tuning on test invalidates the run.
- Frozen split methodology: stratified by category (≥3 dev + ≥6 test per category), seeded shuffle (seed 42, recorded), no question in both splits, split file committed (`data/datasets/v1/{dev,test}.json`).

## 2. Dataset schema

```json
{
  "id": "q013",
  "question": "...",
  "document": "civil-code-public",  // document slug (runner resolves slug → documents.id)
  "category": "direct-lookup | definition | multi-section | cross-reference | exception | multi-hop | comparison",
  "gold_nodes": ["node_civil-code-public_article_1318"],
  "expected_claims": ["consent", "object", "cause"],
  "answerable": true,
  "notes": "Why these golds; known distractors (1319/1320)"
}
```

`answerable: false` marks planted unanswerable questions - correctly abstaining counts as correct (scored separately). Corpus = public/legal-safe docs only. **D2 resolved (2026-10-19):** primary = Civil Code of the Philippines (R.A. 386, slug `civil-code-public`), secondary = U.S. Code Title 11 (slug `usc-title-11`); artifacts + sha256 + evidence in `docs/CORPUS.md`.

Golds carry the full D1 node id including the document slug; a question's golds must all start with `node_{question.document}_`, and golds prefer leaf provisions (containers are the only ids whose form can move if container numbering is revised).

## 3. Retrieval metrics (macro-averaged over test set)

- **Recall@K** = |retrieved ∩ gold| / |gold|. **Primary metric: Recall@5.**
- **Precision@K** reported at K ∈ {1,3,5,10}.
- **MRR@10** = 1 / rank(first gold hit within top 10), else 0. Secondary by construction.
- **Decision rule:** no win/tie ceremony - publish both numbers per category + overall. "Competitive" reading follows the PRD §6 provisional margins; a uniform Tuklas sweep is investigated as leakage, not celebrated.
- Correctness scorer (shared): case-folded, punctuation-stripped substring **or** RapidFuzz `token_set_ratio` ≥ 85 (`rapidfuzz` pinned in requirements).

## 4. Generation metrics (all arm-blinded)

- **Answer correctness:** fraction of `expected_claims` matched by the shared scorer.
- **Faithfulness:** fixed versioned judge prompt scores each sentence-level factual claim 0/1 against cited nodes; judges never see the arm label. Spot check: stratified 20% of test answers (min 10) re-scored by the builder; disagreement > 15% invalidates the judge run (remedy: fix prompt, new run id, re-score all).
- **Citation accuracy:** cited ids exist + support the attached claim; dangling id = failed answer.
  The support half is MECHANICAL and arm-blind: the answer is split into (marker, sentence) pairs, and the cited node's text is checked against the sentence its marker attaches it to with the shared scorer's rule (substring **or** RapidFuzz `token_set_ratio` ≥ the run's own `fuzzy_threshold`). A sentence with no marker is not a citation claim; a citation whose node does not support its sentence fails the answer. The LLM judge is unchanged and still measures the other half of the same definition.
- **Abstention score:** on `answerable: false` questions, correct refusal = 1, any cited answer = 0.

## 5. System metrics (per ask; p50/p95; retrieval vs. generation split)

Retrieval latency, generation latency, total latency, tokens in/out, tool iterations. A more accurate but 10× slower arm is a tradeoff, reported as one.

## 6. Running it (storage reconciled)

- The eval runner writes **both**: `evaluation_runs` (run-level: `evaluation_id`, dataset snapshot id, code_version, weights, normalization id, fuzzy threshold, bonus flags, stoplist version, model+backend, temperature 0, seed, judge_prompt_version, margins, K) **and** one `retrieval_runs` row per question per arm carrying `(evaluation_id, question_id, arm)` join keys plus node ids, weights snapshot, latency. Same config → same retrieval metrics exactly; generation within ±5pp.
- **Every pin in the run record is the one that was APPLIED (binding).** A recorded value the run did not apply is a record of what was asked for, not of what happened, so the config checker (`scripts/check_eval_config.py`) and the runner's own validator (`apps/api/evaluation/config.py`) both REFUSE a config whose pins do not match the code's state:
  - the **weights** must reach the scorer: the run config's weight vector is handed to the retrieval layer, the agent's own retrieval is scored with it, and the row records the vector that produced the ranking;
  - the **bonus flags** must equal the retrieval layer's applied state (`retrieval.scoring.APPLIED_BONUS_FLAGS`). The layer applies no phrase or fuzzy bonus - retrieval.md §3 names them and gives no magnitude, no component and no place in the five-term formula, so a config that pins one is refused rather than recorded;
  - the **stoplist version** must be the version the code has (`agent.stoplist.STOPLIST_VERSION`). A config naming a version the code does not have is refused, because a run record that pins a stoplist nobody can identify describes nothing.
- **Refusal detection (binding):** refusal = response `insufficient: true` with `citations: []`. Abstention score: on `answerable: false` questions, refusal = 1, any cited answer = 0; uncited non-refusal prose = 0 with `citations_valid: false`. Smoke gate (dev-10: completes, 100% rows with node ids except `insufficient: true` rows which carry `[]` + judge scores, zero refusals on `answerable: true`) is consistent: abstentions carry empty id lists by contract.

## 7. Reading the results honestly

- Report n, split seed, model/backend, judge prompt version with every number.
- Expected pattern: Tuklas competitive on cross-reference/multi-hop, weaker-or-even on paraphrase-heavy definitions (no semantic match). Uniform win = leakage smell.
- Publish tuned weights + dev-vs-test delta; test collapse = overfit, reported as such.
- Docs claim "competitive on X, weaker on Y, measured on Z (n=48)" - never "superior".

**What the report must publish BEFORE the numbers it qualifies (binding).** A
number without its scope is a number a reader cannot interpret, and the
report's one job is to let a reader who did not run it judge what the
numbers mean. So the report states, in this order: the run's identity; the
arms' SCOPE (how many nodes each arm ranks, how large the vectorless arm's
lexical corpus is, and what SURFACE each arm scores); the arms' CHANNEL
asymmetry; the units of work the two latencies measure; and then the
numbers. After the numbers, the spot check's METHOD and the measured
limitations.

- **The channel asymmetry names all three vectorless-only channels**, not
  one: `ident` (ADR-005's identifier precedence), `title` (Jaccard overlap
  with the node's heading) and `hier` (the node shares a parent with a
  lex-top-3 node). The dense arm's `title` and `hier` are 0.0 for every
  node by construction, so it has neither. The last two are not a corner
  case: on the Civil Code the vectorless arm's `title` fires on 96.5% of
  its hits and its `hier` on 97.8%, and zeroing both changes the top-10
  order on 21 of 23 dev questions and 40 of 41 test questions. A report
  that names only `ident` leaves a reader thinking the arms differ only on
  direct lookups.
- **The two latencies are not the same unit of work.** The dense arm's is
  a matrix product over a PRE-BUILT index; the vectorless arm's is a
  full-document BM25 RE-SCORE per query. Neither includes the build of
  its own index.
- **The spot check's METHOD is printed beside its number.** The harness's
  default is a MECHANICAL re-judge - the same versioned prompt, the same
  backend, the same evidence - which measures the judge's
  SELF-CONSISTENCY. It is NOT the builder's human re-score that §4 asks
  for, and the report says so in those words. A reader who sees
  "disagreement: 0.017" without the method would reasonably assume a human
  validated the judge.
- **`n_judged` is published next to `faithfulness`**, because the two
  differ: a refusal carries no claim to judge, so n_judged is smaller than
  the row count.

## 8. Verification

- [ ] Schema validates; 7 categories non-empty (≥3 dev + ≥6 test each); `answerable: false` subset ≥ 5 Qs.
- [ ] One command runs both arms on the same snapshot; model-parity check green.
- [ ] `retrieval_runs` + `evaluation_runs` rows joinable per question per arm.
- [ ] Judge blinded; spot-check disagreement computed and ≤ 15%.
- [ ] Re-run with same config id reproduces retrieval exactly, generation within ±5pp.
  - *The retrieval half is checked (2026-10-21):* `scripts/check_reproducibility.py`
    runs the pass twice over two fresh ingests and two fresh index builds, over
    BOTH arms - the vectorless arm and the dense arm, each over its own
    ranking - and compares the per-arm macro-averaged metrics AND the
    per-question node id lists, element for element. The arms pass runs over
    a synthetic statute, because the dense arm embeds the whole corpus and
    over the real Civil Code that is a 2,579-node embed (about 4.5 minutes
    on CPU) per pass, twice.
  - *The generation half is NOT checked by anything in this repository, and
    the report says so.* It is a property of a local model call, so
    measuring it needs a live model backend and a run that costs minutes
    per question; a CI step that silently skipped when no model was present
    would prove nothing. The ±5pp generation tolerance is an UNCHECKED
    acceptance, and it is published as one rather than left to be assumed.
