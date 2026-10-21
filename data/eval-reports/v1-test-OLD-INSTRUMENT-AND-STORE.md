==============================================================================
SUPERSEDED REPORT - NOT A BASELINE, NOT COMPARABLE, NOT REPRODUCIBLE
==============================================================================
This report is preserved because it is a measurement that was actually
taken, and because its run's per-question rows live in a gitignored store
that will not survive. It is NOT a baseline for the current report and its
numbers MUST NOT be diffed against them.

WHAT PRODUCED IT
  run id            : eval_a80167ab
  config id         : cfg_e86e8bfbc803c860
  judge prompt      : judge-v1-batch
  retrieval metric  : a ONE-OFF search over the WHOLE question - a ranking
                       the model never saw. The current report measures the
                       union of the agent's per-sub-question searches.
  latency_ms        : a ONE-OFF whole-question probe. The current report's
                       is the sum of the agent's own search_nodes calls.
  citation_accuracy : checked only that the cited ids EXIST. Existence was
                       tautological - the ids come from the nodes table - so
                       the number was 1.000 by construction and measured
                       nothing. The current report implements the "support
                       the attached claim" half as well.
  lex path          : the per-document BM25 artifact data/indexes/<doc>/
                       bm25.json was PRESENT, so the vectorless arm's lex
                       component came from that document's own rank-bm25
                       model. It is ABSENT now, and the retrieval layer
                       falls back to FTS5, whose N and average document
                       length are table-global. The run row records
                       NEITHER, so a reader of the run row cannot tell.

The difference between this report and the current one is attributable to
the INSTRUMENT and the STORE STATE, and to nothing in the arm's ranking
code - measured, the retrieval layer's tree is BYTE-IDENTICAL across the
two runs: this run was measured at the tree whose root tree object is
4d8ad7e6, the current run at 04f6dcae, and both carry the same git tree
object for apps/api/retrieval (d19c4365), so nothing in the ranking code
changed between them.
Neither report says the arm got better or worse. See the current report's
MEASURED LIMITATIONS for the per-cause measurement.

==============================================================================
Evaluation report eval_a80167ab
==============================================================================

THE RUN
  n questions          : 48
  split                : test (snapshot v1-test)
  split seed           : 42
  model / backend      : qwen2.5:3b / ollama
  temperature / seed   : 0 / 42
  judge prompt version : judge-v1-batch
  code version         : day6-eval-v1
  config id            : cfg_e86e8bfbc803c860
  weights              : {"hier": 1.0, "ident": 1.0, "lex": 1.0, "ref": 1.0, "title": 1.0}
  normalization        : fold-alnum-v2
  fuzzy threshold      : 85
  stoplist version     : fts5-english-v1
  K values             : [1, 3, 5, 10]
  arms                 : ['tuklas', 'vector']
  vectorless arm       : vectorless-five-component, identifier_precedence=True, dense=False
  dense arm            : BAAI/bge-small-en-v1.5 (fastembed), dim=384, revision=5c38ec7c405ec4b44b94cc5a9bb96e735b38267a, dense=True, identifier_precedence=False

THE ARMS ARE NOT LIKE-FOR-LIKE (read before the numbers)
  The two arms differ in more than the retrieval MECHANISM. The vectorless
  arm applies identifier precedence (ADR-005): a query that names a
  provision puts that provision first, and the arm has an 'ident' scoring
  component. The dense arm has NO ident component at all - it embeds the
  query, so it can match a provision semantically, but it has no symbolic
  label channel. The 'direct-lookup' category is therefore not a
  like-for-like result, and the arms' per-category numbers must be read
  with that in mind. The outcome of the comparison is UNKNOWN; this report
  publishes both arms' numbers and no verdict.

THE NUMBERS (per arm; no comparison verdict)
  arm tuklas
    retrieval (n=41): recall@5=0.793 recall@1=0.476 recall@3=0.695 recall@10=0.902
    precision: @1=0.659 @3=0.317 @5=0.224 @10=0.132
    mrr@10=0.769
    generation: correctness=0.627 faithfulness=0.647 citation_accuracy=1.000 abstention=1.000
    system: total p50=1670.5ms p95=5888.3ms | retrieval p50=170.0ms p95=257.3ms | generation p50=1496.5ms p95=5681.3ms
    tokens: in mean=959.5 out mean=938.5 | tool iterations mean=6.65
  arm vector
    retrieval (n=41): recall@5=0.793 recall@1=0.512 recall@3=0.707 recall@10=0.866
    precision: @1=0.683 @3=0.325 @5=0.224 @10=0.124
    mrr@10=0.775
    generation: correctness=0.686 faithfulness=0.579 citation_accuracy=1.000 abstention=0.286
    system: total p50=1922.0ms p95=6540.0ms | retrieval p50=64.0ms p95=168.9ms | generation p50=1770.0ms p95=6479.9ms
    tokens: in mean=992.9 out mean=1818.9 | tool iterations mean=7.12

  per category (recall@5 / mrr@10):
    comparison: tuklas 0.750 / 0.764 (n=6) | vector 0.750 / 0.667 (n=6)
    cross-reference: tuklas 0.833 / 0.694 (n=6) | vector 0.667 / 0.517 (n=6)
    definition: tuklas 1.000 / 0.597 (n=6) | vector 0.917 / 0.917 (n=6)
    direct-lookup: tuklas 1.000 / 1.000 (n=4) | vector 1.000 / 1.000 (n=4)
    exception: tuklas 0.857 / 0.786 (n=7) | vector 1.000 / 0.798 (n=7)
    multi-hop: tuklas 0.583 / 0.854 (n=6) | vector 0.667 / 0.875 (n=6)
    multi-section: tuklas 0.583 / 0.764 (n=6) | vector 0.583 / 0.722 (n=6)

THE JUDGE SPOT CHECK (evaluation.md §4)
  sample: 10 answers, 58 claims re-scored
  disagreement: 0.017 (threshold 0.15) - WITHIN the threshold

MEASURED LIMITATIONS
  The 'ref' scoring component is 0 for every node on this corpus. Its only
  edge source is the references_ table, and ingestion does not populate it
  (apps/api/ingestion/service.py: 'Day 1: no reference parsing; references_
  table stays empty'), so the section 3 formula runs as lex + ident + title
  + 0 + hier. A component breakdown that does not say so would leave the
  reader to discover it.

  The abstention score measures the system's ability to refuse a question
  whose VOCABULARY is absent from the corpus. That is a weaker test than
  refusing a question that is out-of-domain in substance: the dataset's
  unanswerable rows are built out of a vocabulary the corpus does not
  contain at all, because the abstention gate is lexical and a single
  matching token gives lex = 1.0. The score is a lower bound on refusal
  quality, not a measurement of it.

  The dense arm's sufficiency gate CANNOT FAIL on lexical grounds, and this
  is structural rather than a defect in the run. The support rule is 'the
  node's lex component is above 0'. The vectorless arm's lex layer is
  ALL-ZERO when no query term matches any node, so its gate fails closed
  and the run abstains. The dense arm's score is a cosine similarity, which
  is non-zero for every node against every query; the min-max normalization
  then gives the nearest neighbour a lex of 1.0, so the support rule always
  passes. The dense arm's abstention score is therefore STRUCTURALLY 0 on
  this dataset, and that number is a property of the arm's ranking, not a
  measurement of its refusal quality. Comparing the two arms' abstention
  scores compares a refusal measurement with a constant.

The outcome of the vectorless-versus-dense comparison is not
claimed here. The numbers above are what was measured; what
they mean is a reading, and this report does not make it.
==============================================================================
