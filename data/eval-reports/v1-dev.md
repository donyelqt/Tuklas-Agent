==============================================================================
Evaluation report eval_ff373231
==============================================================================

THE RUN
  n questions          : 24
  n distinct golds     : 30 (the effective sample size; 24 questions share some provisions)
  split                : dev (snapshot v1-dev)
  split seed           : 42
  model / backend      : qwen2.5:3b / ollama
  temperature / seed   : 0 / 42
  judge prompt version : judge-v1-batch
  code version         : day6-eval-v1
  harness as of        : the code version above (day6-eval-v1); the numbers below are what THAT harness measured, and a later change to the harness does not change them
  config id            : cfg_0f73ff26c0d38d54
  weights              : {"hier": 1.0, "ident": 1.0, "lex": 1.0, "ref": 1.0, "title": 1.0}
  normalization        : fold-alnum-v2
  fuzzy threshold      : 85
  stoplist version     : fts5-english-v1
  K values             : [1, 3, 5, 10]
  arms                 : ['tuklas', 'vector']
  vectorless arm       : vectorless-five-component, identifier_precedence=True, dense=False
  dense arm            : BAAI/bge-small-en-v1.5 (fastembed), dim=384, revision=5c38ec7c405ec4b44b94cc5a9bb96e735b38267a, dense=True, identifier_precedence=False
  vectorless arm scope         : ranks 2579 nodes, lex corpus 2392 nodes, surface: per-document BM25 over leaf-node bodies and section-intro nodes, with the query's identifier tokens stripped from the lexical input
  dense arm scope         : ranks 2392 nodes, lex corpus 2392 nodes, surface: one dense embedding per node over the node's own text (which begins with its heading line), scored by cosine similarity over the same lexical node set the vectorless arm's BM25 corpus covers
  the two arms score DIFFERENT surfaces over the corpus, and the vectorless arm's lexical corpus is a strict subset of the 2579 nodes it ranks (2392 of them), so a per-category difference between them is a difference of surface and channel as well as of mechanism.

THE ARMS ARE NOT LIKE-FOR-LIKE (read before the numbers)
  The two arms differ in more than the retrieval MECHANISM. They differ in
  the CHANNELS they can use, and three of the vectorless arm's five scoring
  components are channels the dense arm structurally CANNOT have. (1)
  'ident': the vectorless arm applies identifier precedence (ADR-005) - a
  query that names a provision puts that provision first, and the arm has
  an 'ident' scoring component. The dense arm has NO ident component at all
  - it embeds the query, so it can match a provision semantically, but it
  has no symbolic label channel. (2) 'title': the vectorless arm scores
  Jaccard overlap between the query's tokens and the node's HEADING; the
  dense arm has no heading column and scores one embedding per node, so it
  has no title channel either. (3) 'hier': the vectorless arm gives 1.0 to
  a node that shares a parent with a lex-top-3 node, so a provision's
  SIBLINGS rise with it; the dense arm's 'hier' component is 0.0 for every
  node, always. These are not the same three numbers the two arms would
  have produced with the same mechanism - they are channels one arm has and
  the other does not. The 'direct-lookup' category is therefore not a
  like-for-like result, and the arms' per-category numbers must be read
  with that in mind. The outcome of the comparison is UNKNOWN; this report
  publishes both arms' numbers and no verdict.

AND THE 'title' AND 'hier' CHANNELS ARE VECTORLESS-ONLY
  The 'title' and 'hier' components are VECTORLESS-ONLY channels, and they
  are not a corner case the way 'ident' is. The dense arm's 'title' and
  'hier' components are 0.0 for every node by construction, while the
  vectorless arm's 'title' fires on 96.5% of its hits (Jaccard overlap
  between the query's tokens and the node's heading) and its 'hier' fires
  on 97.8% of them (the node shares a parent with a lex-top-3 node, so a
  provision's siblings rise with it). Measured on the Civil Code, zeroing
  'title' and 'hier' changes the top-10 ORDER on 21 of 23 dev questions and
  on 40 of 41 test questions. So the per-category numbers are not two
  mechanisms ranking the same nodes: they are two mechanisms with different
  CHANNELS, and the categories where the channels bite hardest are the ones
  a reader must treat as NOT like-for-like.

THE TWO LATENCIES ARE NOT THE SAME UNIT OF WORK
  The two arms' retrieval latencies are NOT the same unit of work, so they
  are not a like-for-like cost comparison. Both numbers are the sum of the
  agent's own search_nodes calls - the calls that produced the ranking the
  retrieval metric is measured over - so the two are comparable as the cost
  of the agent's retrieval, and the difference between them is the
  difference between the arms' per-query work. That work is not the same:
  the dense arm's is a matrix product over a PRE-BUILT index (the corpus is
  embedded once, about 4.5 minutes for the Civil Code's 2,579 nodes, and
  each query is one query vector against that matrix - 0.36 ms for the
  2,579 x 384 product, measured), while the vectorless arm's is a
  full-document BM25 RE-SCORE per query (141 ms of its 153 ms is the lex
  layer alone, plus 72 ms for the title channel and 17 ms to load the
  candidates, measured on the Civil Code). Neither number includes the
  build of its own index, and the dense arm's build is the more expensive
  of the two by three orders of magnitude. Read the two latencies as 'a
  query against a pre-built artifact' and 'a query against a re-score', not
  as 'the cost of the mechanism'.

THE NUMBERS (per arm; no comparison verdict)
  arm tuklas
    retrieval (n=23): recall@5=0.674 recall@1=0.500 recall@3=0.674 recall@10=0.848
    retrieval measured over: the node ids the ANSWER was produced from (the union of the agent's per-sub-question search_nodes results), not a separate whole-question search
    precision: @1=0.609 @3=0.275 @5=0.165 @10=0.119
    mrr@10=0.711
    generation: correctness=0.721 faithfulness=0.745 citation_accuracy=0.542 abstention=1.000
    abstention: 1 of 1 unanswerable questions refused (a MEASURED COUNT, not a constant of the arm; see the note below)
    judge: n_judged=24 claims (the faithfulness above is the mean over these; a refusal carries no claim and is not judged)
    system: total p50=3376.0ms p95=6833.4ms | retrieval p50=344.0ms p95=1144.7ms | generation p50=2692.5ms p95=6490.1ms
    tokens: in mean=1142.8 out mean=145.8 | tool iterations mean=6.88
  arm vector
    retrieval (n=23): recall@5=0.761 recall@1=0.457 recall@3=0.696 recall@10=0.826
    retrieval measured over: the node ids the ANSWER was produced from (the union of the agent's per-sub-question search_nodes results), not a separate whole-question search
    precision: @1=0.522 @3=0.304 @5=0.200 @10=0.113
    mrr@10=0.661
    generation: correctness=0.767 faithfulness=0.696 citation_accuracy=0.458 abstention=0.000
    abstention: 0 of 1 unanswerable questions refused (a MEASURED COUNT, not a constant of the arm; see the note below)
    judge: n_judged=24 claims (the faithfulness above is the mean over these; a refusal carries no claim and is not judged)
    system: total p50=3383.0ms p95=8669.5ms | retrieval p50=210.0ms p95=554.6ms | generation p50=3017.0ms p95=8375.4ms
    tokens: in mean=1080.5 out mean=174.5 | tool iterations mean=6.96

  per category (recall@5 / mrr@10):
    comparison: tuklas 0.333 / 0.704 (n=3) | vector 0.667 / 0.556 (n=3)
    cross-reference: tuklas 1.000 / 0.833 (n=3) | vector 0.667 / 0.500 (n=3)
    definition: tuklas 0.667 / 0.667 (n=3) | vector 1.000 / 0.778 (n=3)
    direct-lookup: tuklas 1.000 / 0.900 (n=5) | vector 1.000 / 1.000 (n=5)
    exception: tuklas 1.000 / 0.750 (n=2) | vector 1.000 / 0.600 (n=2)
    multi-hop: tuklas 0.500 / 0.667 (n=3) | vector 0.500 / 0.778 (n=3)
    multi-section: tuklas 0.250 / 0.434 (n=4) | vector 0.500 / 0.292 (n=4)

THE JUDGE SPOT CHECK (evaluation.md §4)
  method: mechanical-rejudge (judge self-consistency)
  sample: 10 answers, 59 claims re-scored
  disagreement: 0.000 (threshold 0.15) - WITHIN the threshold

  The spot check's METHOD matters as much as its number, and the method is
  printed beside the disagreement above. 'mechanical-rejudge (judge
  self-consistency)' means the SAME versioned prompt was re-sent to the
  SAME backend at the same temperature and seed over the SAME evidence, and
  the number is the fraction of claims on which the judge disagreed with
  ITSELF. It is NOT the builder's human re-score that evaluation.md section
  4 asks for: a human re-score is a DIFFERENT measurement, and it answers a
  different question ('does the judge agree with a careful human?' rather
  than 'is the judge stable?'). A reader who takes the number above as a
  builder's validation of the judge is reading a measurement that was not
  taken.

MEASURED LIMITATIONS
  The INSTRUMENT changed for two of the numbers in this report, so they are
  NOT comparable with an earlier report of the same split. (1) The
  RETRIEVAL METRIC is now computed over the node ids the ANSWER was
  produced from - the union of the agent's per-sub-question search_nodes
  results - because the earlier report computed it over a separate one-off
  search over the whole question, a ranking the model never saw. The
  earlier report's Recall@K and MRR@10 therefore measure a different
  retrieval and are not comparable with this report's. (2) latency_ms is
  now the SUM of the agent's own search_nodes calls for the question, where
  the earlier report's figure was a one-off whole-question probe; the two
  arms are comparable with each other on this measure, and neither is
  comparable with the earlier report's figure. The judge's scores, the
  abstention score and the token counts were not redefined.

  The citation-accuracy number is a MEASUREMENT OF A PAIR: the model's
  citations AND the support rule's sensitivity. The check is mechanical and
  arm-blind - the answer is split into (marker, sentence) pairs and the
  cited node's text is compared with the sentence by the shared scorer's
  rule, the normalized sentence being a substring of the node's text or the
  RapidFuzz token_set_ratio reaching the run's own fuzzy_threshold. A score
  below 1.0 therefore has two possible causes that this number does not
  separate. (1) The SCORER is strict: the cited node is on the sentence's
  topic and the sentence's content words ARE in the node, but the model
  wrote legalese and no string rule can match a paraphrase to a provision.
  (2) The MODEL cited badly: the cited node is not on the sentence's topic
  at all. A third mode is neither: the model wrote a STATUTORY ARTICLE
  NUMBER in square brackets, the marker convention reads it as a citation
  marker, and no such citation exists. The run records the node ids but not
  the citation list's marker numbers, so the report does not put a number
  on the split between those causes. A reader must therefore not read this
  number as 'the model cites badly' without knowing how much of it is the
  string rule's strictness, and the number is a LOWER BOUND on citation
  quality rather than a measurement of it.

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

  The two arms' abstention scores are MEASURED COUNTS of the unanswerable
  questions each arm refused - not constants of the arm - and most of the
  difference between them is structural. The support rule is 'the node's
  lex component is above 0'. The vectorless arm's lex layer is ALL-ZERO
  when no query term matches any node, so its gate fails CLOSED on that
  condition and the run abstains. The dense arm's score is a cosine
  similarity, which is non-zero for every node against every query; the
  min-max normalization then gives the nearest neighbour a lex of 1.0, so
  the dense arm's gate CANNOT FAIL ON THE LEXICAL CONDITION AT ALL. It
  therefore refuses far less often, and that difference is a property of
  the gate's TRIGGER, not of the model's willingness to answer. The dense
  arm's score is not a structural constant, though, and an earlier version
  of this note said it was: it claimed the score was STRUCTURALLY 0, and
  the measured run contradicts that. The dense arm's gate can fail on a
  NON-LEXICAL condition, and two such conditions are measured here. (1) The
  EVIDENCE LENGTH FLOOR: the evidence gate keeps a node only when its text
  is at least 50 characters, and the dense arm's cosine ranks a cluster of
  short SECTION nodes (11 to 40 characters of text) at the very top for an
  out-of-vocabulary question; every one of them is dropped by the floor,
  the kept set is EMPTY, and the gate fails. (2) The TOP-8 EVIDENCE WINDOW:
  the agent merges every sub-question's supporters, sorts them by score and
  keeps the top 8, so a sub-question whose supporters all score below the
  other sub-questions' supporters loses every one of them to that cut, is
  left with NO supporter in the kept set, and the gate fails closed; the
  kept set is NOT empty in this case, which is what distinguishes it from
  the length floor. The number above is therefore a measured count of
  refusals, and the count beside it is how many unanswerable questions
  there were to refuse.

  The reproducibility acceptance has TWO halves, and only one of them is
  checked here. evaluation.md section 8 asks for 'Re-run with same config
  id reproduces retrieval exactly, generation within +/-5pp'. The RETRIEVAL
  half is checked by scripts/check_reproducibility.py: it runs the
  retrieval pass twice over two fresh ingests and two fresh index builds,
  through BOTH arms, and compares the per-question node id lists and the
  macro-averaged metrics element for element. The GENERATION half is NOT
  checked by anything in this repository: it is a property of a local model
  call, so measuring it needs a live model backend and a run that costs
  minutes per question, and no script here can carry that. The +/-5pp
  generation tolerance is therefore an UNCHECKED acceptance, and a reader
  must not treat this run's generation numbers as a reproduced measurement.

  The dev and test splits SHARE 30 gold provision(s) (100.0% of the test
  split's distinct gold provisions), and 23 test question(s) have their
  ENTIRE gold set inside the dev split's. evaluation.md sec 1 makes the dev
  split the tuning split and the test split the one touched once per frozen
  config, so for those provisions the tuning has already seen the test
  split's answer key. The per-category numbers below move in steps of 1/n,
  and one question can move several rows at once where a provision is
  reused.

The outcome of the vectorless-versus-dense comparison is not
claimed here. The numbers above are what was measured; what
they mean is a reading, and this report does not make it.
==============================================================================
