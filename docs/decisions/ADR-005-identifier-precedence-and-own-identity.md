# ADR-005: Identifier precedence in the ranking, and own-identity labels

## Status
Accepted

## Date
2026-10-20

## Context

`docs/TODO.md` (Day 4) recorded a measured defect: a question that NAMES a
provision and carries other content words lost that provision from the
evidence window. Measured on the Civil Code corpus:

- "What does article 1318 require?" put `article_1318` at rank 10 of the
  score-ordered list, behind `article_1395` (score 2.100), `article_1402`
  (2.037) and `article_1168` (1.978). The top-8 evidence cut
  (`EVIDENCE_KEEP_TOP = 8`) dropped `article_1318` entirely, so the model
  answered about the word-match and cited `article_1395` - a different
  provision.
- "What does article 2124 provide?" put `article_2124` at rank 15, out of the
  window.
- The golden 10-question set passed 10/10 and did not catch it: none of its
  questions names a provision in this adversarial way ("What are the
  requisites of contracts?" finds article 1318 at rank 1).

Two independent causes were measured.

**Cause 1 - the ident index could not identify anything.** `retrieval.md` §3
sums five bounded components, so a single component capped at 1.0 cannot
outrank a competitor that accumulates `lex 1.0 + title 0.1 + hier 1.0 = 2.1`.
The `ident` component (capped at 1.0) therefore lost every time. And it could
not have won anyway, because the ident index was contaminated:
`_label_index_rows` indexed every label span found anywhere in a node's title
plus every ancestor's label spans. Measured on the Civil Code, `art 57`
resolved to 404 nodes, `sec 1` to 230, `ch 2` to 790 and `book iv` to 1267,
while `art 1318` resolved to exactly 1. A label that names hundreds of nodes
identifies none of them.

The contamination had two upstream sources, both in `apps/api/ingestion/parser.py`:

- The RapidFuzz near-miss path turned BODY TEXT into headings. "party
  regarding the solemnization of the marriage in a place other than those
  mentioned in article 57 ..." became a `part` node (fuzz "party" ~ "part" =
  88.9), and the 402 real articles beneath it inherited `art 57`. 75 of the
  corpus's 2583 headings came from that path, including 28 `section` nodes
  and 2 `article` nodes ("at the expense of the usufructuary. (500)" became
  `article_500_d2`).
- The PDF's MARGINAL NOTES were read as headings. "article 765. (n)" sits in
  the margin beside a provision; read as a heading it creates a second node
  for a provision printed in full elsewhere. Five article labels (`art 765`,
  `art 895`, `art 1544`, `art 1984`, `art 2168`) each resolved to two nodes,
  so the identifier could not identify any of them either.

**Cause 2 - a cited provision was indistinguishable from an owned one.** A
node whose title merely CITES a provision ("Article 244. The provisions of
articles 226 to 228 and 235 to 236") contributed `art 226` and `art 228` to
the index. 365 of 2672 own-title label spans were not the node's own
identity.

## Decision

1. **Own identity.** `apps/api/retrieval/normalizer.py` gains
   `own_identity(node_type, title)`, which reads the label in a node's own
   HEADING: the label word must start the title, be followed by the number,
   and be terminated by a period or the end of the string, and the node type
   must match the label family. A node's own title is the only source of the
   identity it owns; an ancestor's identity is inherited, never owned.

2. **The ident index records the level.** `ident_index` gains a `level`
   column: 1.0 when the node OWNS the label, 0.5 when it inherits it. The
   effective `ident` value is the lower of the label's own level and the
   level at which the node carries it. Measured: `art 57` now resolves to 1
   node, `art 1318` to 1, `sec 1` to the 23 sections actually printed as
   "SECTION 1", and the 5 duplicated article labels are gone.

3. **Identifier precedence is an ORDER, not a weight.** The node that
   UNIQUELY owns an EXACT label the query names is marked `identified` and
   sorts FIRST; everything else keeps the §3 score order. The score value,
   the five components, and the §3 formula are UNCHANGED - no new weight, no
   tuned constant, no change to any published component value. A container
   label (chapter/title/book) never identifies, because container numbers
   repeat ("CHAPTER 2" occurs 34 times in the Civil Code).

4. **One ordering for the whole pipeline.** `scoring.evidence_rank_key` is
   the single source of the order, used by the retrieval ranking, the
   evidence-window cut, the context builder's prompt order, and the agent's
   merged evidence set. The model reads the named provision FIRST in the
   prompt, not the fifth.

5. **The parser stops promoting body text and marginal notes.** A heading's
   number must be terminated by a period or the end of the line, and a
   marginal note (a label, a period, and only a parenthesised marker) is
   never a heading. Measured over the Civil Code's 2583 headings, the
   marginal-note pattern matches exactly the 5 notes and nothing else.

6. **The `ident` component's VALUE is the label's own level; the `level`
   column decides OWNERSHIP.** These are two different questions and the
   first implementation conflated them. `retrieval.md` §3 states the
   component value as "1.0 exact normalized article id, 0.5
   chapter/title-level id, else 0" - that is a property of the LABEL, not
   of the node. So a node carrying an exact label scores 1.0 whether it
   owns the label or inherits it, and downgrading the inheritor would
   change every published ident and every score, which §3 forbids without
   a recorded tuning run. Ownership is a separate question, answered by
   the `level` column: only a node at level 1.0 may be the identified node.

   Measured: 0 of 40 source files' components differ from the pre-ADR
   values, so no published score changed.

7. **A COORDINATED list of provisions yields a label for each.** "Compare
   articles 1318 and 2124" extracted only `art 1318`, because the label
   span captured one number. The second provision was then invisible to
   the identifier channel - and the planner split the question into
   "Compare articles 1318" and "2124", where "2124" is a bare number and
   carries no label at all. `normalizer._coordinated_numbers` reads the
   list: numbers joined by a comma, "and", "or" or "to", bounded at eight.
   The list must be a real list: "and" is a separator and is never read as
   the roman numeral "d", and a word that is not a number ends the list.

8. **The identified node is never evicted for space.** The identifier
   precedence is an ORDER, so the named provision keeps whatever score its
   lexical and hierarchical components gave it - which is often the LOWEST
   in the kept set. A lowest-score-first eviction therefore dropped the
   named provision first, silently undoing the precedence exactly when the
   prompt is too large to hold everything. The context builder now
   protects the identified node alongside the gate set.

9. **The identified node is exempt from the evidence length floor.** The
   evidence pipeline drops nodes whose text is under 50 characters, so that
   page-span nodes whose text is not a provision do not reach the prompt.
   The floor was also dropping PROVISIONS: article 1433 is 48 characters
   long ("Article 1433. Estoppel may in pais or by deed."), so a query
   naming it ranked it first, identified it, and then threw it away - and
   the model answered about a DIFFERENT article. 98 of the Civil Code's
   article and section nodes are under the floor, so this is not a one-off.
   The identified node is now exempt from the floor. The floor still
   applies to every other node.

## Measured effect

- Named provision at rank 1: 47/200 (23.5%) -> 200/200 (100%). The sweep is
  40 random articles x 5 question forms ("article N", "What is article N?",
  "What does article N require?", "What does article N provide?", "What are
  the requirements of article N?").
- Named provision inside the evidence window: 200/200 (100%). Measured on a
  FRESH ingest of the corrected tree. On the tree that still carried the five
  marginal-note duplicates the window was 195/200, and every miss was
  article 1433 - the 48-character provision the length floor was dropping.
  Exempting the identified node from that floor closes all five.
- Golden 10-question smoke: 10/10 before and after (no regression).
- The 5 previously-ambiguous article labels: rank 1, identified, and first
  in the evidence window, in all 4 question forms each (20/20).
- Node census: **2579 nodes on a fresh ingest** (2268 articles, 131
  chapters, 124 sections, 51 titles, 4 books, 1 document), no `part` or
  `division` phantom containers.
- **The U.S. Code Title 11 ident channel went from 19 labels to 340.** The
  §-form headings ("§ 101") were being read as section nodes whose own
  identity was the bare "§", so almost nothing resolved. After the fix,
  "What does section 101 define?" puts 10 nodes with an ident component in
  the top 10.
- **The `ident` component's VALUE is unchanged by this ADR.** Measured
  against the pre-ADR implementation over the whole corpus: 0 of the
  components differ. Ownership moved into the `level` column, not into the
  score.
- **Coordinated provisions are all extracted.** "Compare articles 1318 and
  2124" -> `['art 1318', 'art 2124']`; "What do articles 1318, 2124 and 26
  say?" -> `['art 1318', 'art 2124', 'art 26']`; "Section 101 and 102 of
  this title" -> `['sec 101', 'sec 102']`. Before: the first label only.
- **The identified node survives eviction.** With a 250-character budget
  the identified node was evicted and the prompt led with two other nodes;
  it now survives every budget from 400 down to 150 and leads the prompt
  at each of them.

## Alternatives Considered

### Raise the `ident` weight to 2.0
- Pros: a one-line change; the same 100% on the sweep.
- Cons: it is a TUNED NUMBER, and `retrieval.md` §3 requires every weight
  change to come from a recorded tuning run on the dev split. It also
  changes the published score VALUES, so every recorded score becomes
  incomparable with the ones already in the eval history. It leaves the
  contaminated index untouched, so `art 57` would still name 404 nodes.
- Rejected: the order expresses the fact without moving a number.

### Min-max normalization is the bug; switch to absolute BM25
- Pros: it would also address the separate recorded finding that the
  min-max layer makes the sufficiency gate unable to refuse an out-of-domain
  question.
- Cons: it changes the meaning of the `lex` component for every node, and
  the repo's own Day-3 finding already recorded that ABSOLUTE BM25 scores the
  unanswerable questions HIGHER than the answerable ones, so it does not fix
  the refusal problem either. It is a §3 change of a different order of
  magnitude, and it belongs to the retrieval tuning run.
- Rejected: out of scope for this defect, and it does not address it.

### A query classifier (lookup vs comparison)
- Pros: the canonical way to route a named-provision query.
- Cons: `retrieval.md` §3 says explicitly "No depth prior, no classifier - v1
  has no query classifier; lookup-vs-comparison handling is future work, not
  a silent keyword set."
- Rejected: the spec forbids it, and the fix does not need one. The decision
  is driven by the ident component that already exists and already means
  "the user named this", not by a guess about the query's shape.

### Make the ambiguous labels decidable by rank
- Pros: it would cover the 5 duplicated article labels without a parser
  change.
- Cons: it needs a tiebreak rule that is not in the spec, and it would still
  leave the tree carrying a second node for a provision that is printed once
  - a false node in every citation and every tree view.
- Rejected: the duplicate node is a parsing defect, and it is fixed where it
  is created.

## Consequences

- `retrieval.md` §3's score formula and the five components are unchanged.
  The change is to the ORDER and to what the ident index means. §4's
  normalization rules are extended, not replaced: the canonical label
  grammar is untouched, and the new `own_identity` rule decides which label
  a node OWNS.
- `retrieval.md` §3's "no classifier" sentence still holds: the rule reads
  the ident component, not the query's shape.
- **The evaluation fairness boundary changes and must be reported.** The
  vectorless arm now has identifier precedence and the dense arm has no ident
  component at all, so the arms differ in more than "retrieval mechanism".
  This is a structural asymmetry: the dense arm embeds the query, so it can
  match "article 1318" semantically, but it has no symbolic label channel.
  `evaluation.md` §1 requires the arms to differ only in retrieval, and
  `evaluation.md` §7 requires the reported numbers to name the arms, the
  dataset and the config. Any reported comparison MUST state that the
  vectorless arm applies identifier precedence and the dense arm does not, so
  the direct-lookup category is not read as a like-for-like result. This is a
  reporting obligation, not a licence to claim a win: the outcome of the
  comparison is unknown.
- The `sec N` labels remain ambiguous by nature (a statute prints "SECTION 1"
  in every chapter), so a query naming a section identifies no node. That is
  correct: a section number alone does not name one provision.
- `docs/TODO.md` Day 4's measured finding is closed by this ADR, with the
  measurement recorded.

## Consequences for the tree

The parser change alters the node census, so every corpus-wide number
changes and is re-measured rather than carried over. A fresh ingest of the
Civil Code yields **2579 nodes: 2268 articles, 131 chapters, 124 sections,
51 titles, 4 books, 1 document**. `parse_confidence` is 0.994 (Civil Code)
and 0.991 (U.S. Code Title 11).

**The census in the live tree at the time of writing was 2584 nodes with
2273 articles, and that number was WRONG.** It was taken from a tree that
still carried the five marginal-note duplicates the fix removes
(`article_765_d2`, `article_895_d2`, `article_1544_d2`, `article_1984_d2`,
`article_2168_d2`). Re-ingesting the same PDF with the fixed parser yields
2268 articles and none of the five, which is the corrected census. The
duplicates are visible in the old numbers and in nothing else: a fresh
ingest has no article present that the old tree lacked, and the old tree
had exactly five more.

The chapter count is unchanged by this correction, and it is worth recording
why the number is what it is: the Civil Code prints "CHAPTER 1" 34 times
and "CHAPTER 2" 34 times, in every title, so 131 chapter nodes for 9
distinct chapter numbers is what the document actually contains, not a
parsing artifact. The ADR's earlier "149 chapters" figure was likewise
carried from the stale tree.

