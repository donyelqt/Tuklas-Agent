==============================================================================
SUPERSEDED REPORT - NOT A BASELINE, NOT COMPARABLE, NOT REPRODUCIBLE
==============================================================================
This report is preserved because it is a measurement that was actually
taken, and because its run's per-question rows live in a gitignored store
that will not survive. It is NOT a baseline for the current report and its
numbers MUST NOT be diffed against them.

WHAT PRODUCED IT
  run id            : eval_083a4c15
  config id         : cfg_5a3d6973caf7bd43
  judge prompt      : judge-v1
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

THIS FILE IS A RECONSTRUCTION, NOT A RECOVERY. The dev-24 report of this
run did NOT exist in git, so it is RE-RENDERED from the stored run row
(run eval_083a4c15, config cfg_5a3d6973caf7bd43, judge prompt judge-v1) with
the OLD renderer, recovered from commit eb36a52
(apps/api/evaluation/report.py). The METHOD WAS VALIDATED on the test
report before it was trusted here: rendering run eval_a80167ab with the old
renderer reproduces the published PR#31 test file byte-for-byte (sha256
db8a4c2b75b8b9e205db2fdb075ce74f6fb88022debe602f3297d5c612c81db4, 5308
bytes). A reconstruction validated on one run is evidence about the METHOD,
not a claim that this file is byte-identical to a file that never existed
in git; the numbers in it come from the stored run row, and they are what
that run measured.

==============================================================================
Evaluation report eval_083a4c15
==============================================================================

THE RUN
  n questions          : 24
  split                : dev (snapshot v1-dev)
  split seed           : 42
  model / backend      : qwen2.5:3b / ollama
  temperature / seed   : 0 / 42
  judge prompt version : judge-v1
  code version         : day6-eval-v1
  config id            : cfg_5a3d6973caf7bd43
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
    retrieval (n=23): recall@5=0.717 recall@1=0.478 recall@3=0.717 recall@10=0.783
    precision: @1=0.609 @3=0.304 @5=0.183 @10=0.104
    mrr@10=0.708
    generation: correctness=0.700 faithfulness=0.691 citation_accuracy=1.000 abstention=1.000
    system: total p50=2225.5ms p95=608259.1ms | retrieval p50=169.5ms p95=309.7ms | generation p50=1996.0ms p95=608057.4ms
    tokens: in mean=1003.3 out mean=5230.1 | tool iterations mean=6.92
  arm vector
    retrieval (n=23): recall@5=0.870 recall@1=0.522 recall@3=0.761 recall@10=0.935
    precision: @1=0.652 @3=0.333 @5=0.235 @10=0.130
    mrr@10=0.797
    generation: correctness=0.822 faithfulness=0.725 citation_accuracy=1.000 abstention=0.000
    system: total p50=2496.0ms p95=10021.9ms | retrieval p50=98.0ms p95=166.2ms | generation p50=2342.5ms p95=9883.1ms
    tokens: in mean=1040.0 out mean=1879.9 | tool iterations mean=6.96

  per category (recall@5 / mrr@10):
    comparison: tuklas 0.500 / 0.500 (n=3) | vector 0.667 / 0.583 (n=3)
    cross-reference: tuklas 1.000 / 0.833 (n=3) | vector 1.000 / 0.667 (n=3)
    definition: tuklas 0.667 / 0.444 (n=3) | vector 1.000 / 0.750 (n=3)
    direct-lookup: tuklas 1.000 / 0.867 (n=5) | vector 1.000 / 1.000 (n=5)
    exception: tuklas 1.000 / 0.750 (n=2) | vector 1.000 / 0.750 (n=2)
    multi-hop: tuklas 0.667 / 1.000 (n=3) | vector 0.667 / 1.000 (n=3)
    multi-section: tuklas 0.250 / 0.531 (n=4) | vector 0.750 / 0.708 (n=4)

THE JUDGE SPOT CHECK (evaluation.md §4)
  sample: 10 answers, 81 claims re-scored
  disagreement: 0.025 (threshold 0.15) - WITHIN the threshold

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
