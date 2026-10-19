# Tuklas Agent — Evaluation Framework (F7, rev 2)

**Status:** Draft for review (rev 2 — post adversarial review). Answers PRD US-4.
**Principle:** same dataset, same questions, same golds, same generation model, same judges → both arms → reported metrics. Anything else is a demo, not an experiment.

## 1. Design

```text
Benchmark dataset v1 (72Q: 24 dev + 48 test; 7 categories; fixed gold_nodes +
expected_claims + answerable flag) — frozen snapshot id per run
        │
        ├── Arm A: Tuklas Agent (vectorless: BM25 + structural + reference)
        └── Arm B: Vector RAG (dense baseline, experiments/vector/ ONLY)
        │   identical generation model + backend + temperature 0 + seed (ADR-004)
        ▼
Metrics: retrieval (Recall@K, Precision@K, MRR@10) │ generation
(faithfulness blinded, correctness, citation accuracy) │ system
(latency p50/p95 split, tokens) — evaluation_runs + per-question retrieval_runs
```

- Arms differ **only** in retrieval. Different models across arms = invalid run (CI check on run config).
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

`answerable: false` marks planted unanswerable questions — correctly abstaining counts as correct (scored separately). Corpus = public/legal-safe docs only.

Golds carry the full D1 node id including the document slug; a question's golds must all start with `node_{question.document}_`, and golds prefer leaf provisions (containers are the only ids whose form can move if container numbering is revised).

## 3. Retrieval metrics (macro-averaged over test set)

- **Recall@K** = |retrieved ∩ gold| / |gold|. **Primary metric: Recall@5.**
- **Precision@K** reported at K ∈ {1,3,5,10}.
- **MRR@10** = 1 / rank(first gold hit within top 10), else 0. Secondary by construction.
- **Decision rule:** no win/tie ceremony — publish both numbers per category + overall. "Competitive" reading follows the PRD §6 provisional margins; a uniform Tuklas sweep is investigated as leakage, not celebrated.
- Correctness scorer (shared): case-folded, punctuation-stripped substring **or** RapidFuzz `token_set_ratio` ≥ 85 (`rapidfuzz` pinned in requirements).

## 4. Generation metrics (all arm-blinded)

- **Answer correctness:** fraction of `expected_claims` matched by the shared scorer.
- **Faithfulness:** fixed versioned judge prompt scores each sentence-level factual claim 0/1 against cited nodes; judges never see the arm label. Spot check: stratified 20% of test answers (min 10) re-scored by the builder; disagreement > 15% invalidates the judge run (remedy: fix prompt, new run id, re-score all).
- **Citation accuracy:** cited ids exist + support the attached claim; dangling id = failed answer.
- **Abstention score:** on `answerable: false` questions, correct refusal = 1, any cited answer = 0.

## 5. System metrics (per ask; p50/p95; retrieval vs. generation split)

Retrieval latency, generation latency, total latency, tokens in/out, tool iterations. A more accurate but 10× slower arm is a tradeoff, reported as one.

## 6. Running it (storage reconciled)

- The eval runner writes **both**: `evaluation_runs` (run-level: `evaluation_id`, dataset snapshot id, code_version, weights, normalization id, fuzzy threshold, bonus flags, stoplist version, model+backend, temperature 0, seed, judge_prompt_version, margins, K) **and** one `retrieval_runs` row per question per arm carrying `(evaluation_id, question_id, arm)` join keys plus node ids, weights snapshot, latency. Same config → same retrieval metrics exactly; generation within ±5pp.
- **Refusal detection (binding):** refusal = response `insufficient: true` with `citations: []`. Abstention score: on `answerable: false` questions, refusal = 1, any cited answer = 0; uncited non-refusal prose = 0 with `citations_valid: false`. Smoke gate (dev-10: completes, 100% rows with node ids except `insufficient: true` rows which carry `[]` + judge scores, zero refusals on `answerable: true`) is consistent: abstentions carry empty id lists by contract.

## 7. Reading the results honestly

- Report n, split seed, model/backend, judge prompt version with every number.
- Expected pattern: Tuklas competitive on cross-reference/multi-hop, weaker-or-even on paraphrase-heavy definitions (no semantic match). Uniform win = leakage smell.
- Publish tuned weights + dev-vs-test delta; test collapse = overfit, reported as such.
- Docs claim "competitive on X, weaker on Y, measured on Z (n=48)" — never "superior".

## 8. Verification

- [ ] Schema validates; 7 categories non-empty (≥3 dev + ≥6 test each); `answerable: false` subset ≥ 5 Qs.
- [ ] One command runs both arms on the same snapshot; model-parity check green.
- [ ] `retrieval_runs` + `evaluation_runs` rows joinable per question per arm.
- [ ] Judge blinded; spot-check disagreement computed and ≤ 15%.
- [ ] Re-run with same config id reproduces retrieval exactly, generation within ±5pp.
