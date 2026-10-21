# ADR-007: Disjoint inputs are a QUERY-side rule, and the heading line is part of the body

## Status
Accepted

## Date
2026-10-21

## Context

`docs/retrieval.md` §3 stated, and `docs/SPEC.md` §5 repeated, a rule
about the scoring components:

> **Disjoint inputs (no triple-counting):** `lex` sees body text only
> (titles stripped before BM25); `title` sees title/heading only; `ident`
> sees identifier tokens only (removed from both other inputs). A heading
> term fires in exactly one component.

The rule has two halves, and the two halves have different truth values.

**The QUERY half is true and enforced.** `apps/api/retrieval/scoring.py`
strips the query's identifier tokens from the lex input and from the title
input (`normalizer.label_tokens`), so an IDENTIFIER term fires only in the
`ident` component.

**The DOCUMENT half is false.** The rule says "titles stripped before
BM25", and the titles are not stripped. The parser's `slice_text()` starts
at the heading's OWN line, so a node's stored body text BEGINS with the
node's title. The BM25 corpus is built from `node.text`
(`apps/api/retrieval/index.py`), so the heading's words are BM25 terms.

Measured on the live store (`data/tuklas.sqlite`, both ingested
documents):

| Measurement | Civil Code | U.S. Code Title 11 |
|---|---|---|
| Nodes whose text starts with their title | 2578 / 2579 | 985 / 986 |
| Title tokens in the BM25 corpus | 41,673 | 3,255 |
| Title tokens that are ALSO BM25 terms | 41,673 (100%) | 3,255 (100%) |
| Corpus nodes with ≥1 title token that is a BM25 term | 2392 / 2392 (100%) | 810 / 810 (100%) |
| Corpus nodes whose FIRST LINE equals the title | 2392 / 2392 (100%) | 810 / 810 (100%) |

So a heading's ordinary word fires in `lex` AND in `title`, and the §3
formula sums them. The documentation stated a rule the implementation
violated.

## Decision

**Resolution (b): the DOCUMENTATION is corrected to match the
implementation, and the implementation is left alone.** The rule is
restated as what it is and has always been — a QUERY-side rule about
IDENTIFIER terms. The document-side behaviour is stated explicitly, and
the reason it is not changed is measured and recorded below.

The corrected rule, in `docs/retrieval.md` §3 and `docs/SPEC.md` §5:

* an **identifier** term fires in exactly one component (`ident`), because
  the query's identifier tokens are stripped from the other two; and
* a **heading's ordinary words** may fire in two (`lex` and `title`),
  because the heading line is part of the node's stored body text.

## The blast radius of the alternative, measured

The alternative — resolution (a), stripping the heading line from the
body before BM25 — was implemented and measured before being rejected.
Three variants were measured over the Civil Code's dev split (the 23
answerable dev questions), each with a fresh persisted BM25 corpus and the
runtime scoring path:

| Corpus variant | dev Recall@5 | dev MRR@10 |
|---|---|---|
| Current (heading line kept) | **0.7174** | **0.7083** |
| Heading line removed from the body | 0.4348 | 0.4070 |
| Only the identifier label prefix removed ("Article 1318.") | 0.7174 | 0.7072 |

The current figures are the ratchet values in `CONSTRAINTS.md` (Recall@5
0.717, MRR@10 0.708), so the middle row is a **-0.283 Recall@5 collapse**
against the ratchet, and it moves 11 of 23 dev questions' per-question
metrics. The third row is the honest finding that the collapse comes from
the heading's ORDINARY WORDS, not from the identifier label: removing the
label prefix alone costs essentially nothing, and removing the whole
heading line costs a third of the metric.

Two further measurements say why removing the heading line is not a
cosmetic change:

* **37.9% of the Civil Code's lexical tokens ARE the heading line**
  (47,825 of 126,123). For the U.S. Code Title 11 the figure is 0.9%
  (3,326 of 350,627), so the collapse is a property of the CORPUS, not a
  universal constant — a corpus whose headings are short and whose bodies
  are long barely notices, and a corpus of short provisions is destroyed
  by it.
* **317 of the 2,392 Civil Code corpus nodes have NO body text left once
  the heading line is removed** (19 of 810 for Title 11). For those nodes
  the heading IS the provision: "Article 1433. Estoppel may in pais or by
  deed." is a complete provision, and stripping the heading deletes the
  provision's only text from the corpus. 728 corpus nodes (Civil Code)
  would fall below the 50-character evidence floor, so the evidence
  pipeline would drop them before they could be selected.

So the heading line is not decoration sitting on top of a body. On a
statute, the heading line is very often the provision.

A second consideration is the one the spec's own §3 already fixes: the
five components and the formula are FROZEN, and "any number reported
without this record is invalid" (`docs/retrieval.md` §3, "Defaults").
Resolution (a) changes every `lex` value and therefore every published
score, on the strength of no tuning run — which §3 forbids. Resolution
(b) changes no number at all.

## Consequences

* `docs/retrieval.md` §3 and `docs/SPEC.md` §5 now state the rule the
  code implements. The heading of §3 changed from "disjoint components"
  to "query-side disjointness" for the same reason.
* `apps/api/retrieval/index.py`'s module docstring and the corpus-build
  comment state that the corpus carries the heading line DELIBERATELY, and
  point here.
* `apps/api/retrieval/normalizer.py`'s `label_tokens` docstring now says
  the IDENTIFIER term is the disjoint one.
* `apps/api/retrieval/scoring.py`'s module docstring and `_FTS_WEIGHTS`
  comment say the same (edited by the agent that owns that file).
* `experiments/vector/index.py`'s `_embeddable_text` docstring no longer
  claims the vectorless arm keeps a heading out of the lexical surface
  (edited by the agent that owns that file).
* **No score, no metric and no published number changes.** The BM25
  corpus is built exactly as before. The ratchet values in
  `CONSTRAINTS.md` are untouched, and the reported test-split numbers in
  `data/eval-reports/v1-test.md` remain the numbers the current code
  produces.
* **The title component is not redundant with lex.** It is a bounded
  heading-overlap measure (Jaccard, [0,1]) while `lex` is a min-maxed
  BM25 over a document's corpus; the two rank differently, and the
  `title` channel is one of the two vectorless-only channels the report
  now discloses (the other is `hier`).

## Alternatives Considered

### Strip the heading line from the BM25 corpus (resolution (a))
- Pros: the documentation's original sentence becomes literally true.
- Cons: measured above. It drops dev Recall@5 from 0.717 to 0.435
  against a ratchet, empties 317 corpus nodes, and changes every published
  `lex` value with no tuning run to justify it.
- Rejected: the measurement is the decision, and it is on the side of the
  implementation.

### Strip the heading line in the PARSER, so `node.text` never contains it
- Pros: the same rule holds at the source, and every consumer of the body
  text (BM25, the evidence floor, the judge) sees the same surface.
- Cons: strictly worse than stripping it only in the BM25 corpus, and for
  the same reason. The provision's text disappears from the store itself:
  317 of the Civil Code's corpus nodes would have an EMPTY body, and 728
  would fall below the 50-character evidence floor and be dropped from the
  evidence window before the model could read them. The judge reads the
  node text too, so the citation-support check would be reading an empty
  evidence string for those nodes.
- Rejected: it moves the same loss from the score to the data.

### Strip only the IDENTIFIER label from the body ("Article 1318.")
- Pros: it makes the `ident` half of the rule true on the document side as
  well, and it is nearly free: dev Recall@5 is unchanged at 0.7174 and
  MRR@10 moves by 0.0011.
- Cons: it is NOT the rule the documentation states, and it does not make
  the "a heading term fires in exactly one component" sentence true — the
  heading's ordinary words would still be in both `lex` and `title`. It
  would be a second, smaller version of the same change, with its own
  re-measurement and its own effect on the published scores, in exchange
  for a property nobody asked for.
- Rejected: it answers a different question than the one the
  documentation asked. It is worth recording that it is nearly free, so
  that a future decision to pursue document-side disjointness on the
  identifier half starts from a measurement rather than from a guess.

### Leave the documentation as it is
- Pros: no work.
- Cons: the artifact's stated job is to let a reader judge whether the
  numbers mean what they appear to mean, and a spec that states a rule the
  code violates is a spec that cannot be relied on. A reader who trusted
  the sentence would mis-model every score in the report.
- Rejected: this is the defect.

## The measurement that a future change would have to beat

Any change to the document side of this rule is a change to the `lex`
component, and therefore to every published score. `docs/retrieval.md` §3
requires a recorded tuning run on the dev split for that, and
`CONSTRAINTS.md` fixes the ratchet at Recall@5 0.717 and MRR@10 0.708.
The measurements above are the baseline such a run would be measured
against.
