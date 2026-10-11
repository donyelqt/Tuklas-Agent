# ADR-003: Vectorless primary path with quarantined dense baseline

## Status
Accepted

## Date
2026-10-07

## Context
The research question requires comparing structure-aware vectorless retrieval
against dense-vector RAG. The comparison is only honest if the primary path cannot
accidentally use embeddings and the baseline cannot leak into production behavior.

## Decision
1. `apps/api/{ingestion,retrieval,agent,generation}/` import no
   embedding/vector/similarity-over-vectors library — enforced by CI import scan.
2. Dense-embedding code lives only in `experiments/vector/`, callable only from
   `evaluation/` (the vector arm) — enforced by the same scan.
3. Scoring weights start uniform and are tuned on the dev split; no weight is
   presented as optimal without a tuning run on record.

## Alternatives Considered

### Hybrid primary (dense + structure fused)
- Pros: likely higher raw accuracy; fashionable.
- Cons: confounds the experiment — a hybrid win says nothing about whether
  vectorless retrieval is competitive, which is the actual question.
- Rejected for v1; legitimate follow-up experiment after the clean comparison exists.

### No baseline ("vectorless is obviously better for law")
- Pros: less work.
- Cons: an unevidenced superiority claim — the exact failure mode the spec forbids.
- Rejected: the baseline is the point.

## Consequences
- `evaluation.md` design is load-bearing, not decorative; both arms share dataset,
  golds, judges, and K values.
- A uniform Tuklas win across all categories is treated as leakage smell, not success.
- Hybrid becomes ADR-005 candidate only after v1 numbers are published.
