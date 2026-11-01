# V2 evaluation improvement plan

Source: the reviewer feedback on the v1 submission. Two asks, both about
measurement, not about the arm.

1. **Numerical accuracy and citation consistency.** The verification
   system needs more rigorous checks that numerical answers reference
   the correct metrics, scales and time periods, and that citations
   consistently support what the generated answers say.
2. **Evaluation quality.** The same rigor applied to retrieval and
   prompting was not applied to evaluating response quality. The
   evaluations should catch factually incorrect or mismatched answers,
   including answers that appear grounded in the retrieved context.

This document is a plan, not a result. Every number below either comes
from a run that was actually taken or is marked as not yet measured.
Where a proposed experiment could settle a question, the question is
named and the measurement that would settle it is stated.

---

## 1. What the v2 verification layer already measures

`apps/api/evaluation/verification.py` (`verification-v2`) checks each
cited sentence against the node its own marker names. All checks are
mechanical and arm-blind by construction: `verify_answer` has no arm
parameter.

| Check | Catches | Limit |
|---|---|---|
| `numeric_consistency` | a quantity in the sentence whose value differs from the cited node's quantity in the same unit family | needs a quantity on both sides; a node that states none is undecided |
| `article_consistency` | a sentence that names an article the cited node's heading does not | needs both sides to name one |
| `polarity_consistency` | a near-identical node sentence with the opposite negation | needs a node sentence above `POLARITY_MIN_SIMILARITY` |
| `citation_supports` | a sentence the cited node's text does not support | a string rule; a proposition ABOUT a provision cannot match the provision's words |
| answer consistency | two claims in one answer stating different values for the same family | needs two claims in one family |
| unreferenced citations | a node the answer's citation list names but no claim references | mechanical |
| uncited claims | a factual sentence with no marker | mechanical |

The measures travel in the run record under `verification_version`, and
the version is a constant: a number produced by a different rule set is
a different number.

---

## 2. Measured, on the published v2 runs

`data/eval-reports/v2-dev.md` and `v2-test.md`, answerable rows only.
Retrieval metrics are the run's own and are listed for context; the
verification columns are the v2 layer.

**v2-test** (41 answerable rows per arm)

| arm | recall@5 | verified | claim checks / supported | consistency checks / mismatches | citations unreferenced | semantic uncertainty |
|---|---|---|---|---|---|---|
| tuklas | 0.732 | 0.000 (0/41) | 98 / 67 | 2 / 2 | 254 | 0.927 (38/41) |
| vector | 0.707 | 0.000 (0/41) | 121 / 86 | 3 / 2 | 227 | 0.902 (37/41) |

**v2-dev** (23 answerable rows per arm)

| arm | recall@5 | verified | claim checks / supported | consistency checks / mismatches | citations unreferenced | semantic uncertainty |
|---|---|---|---|---|---|---|
| tuklas | 0.732 | 0.000 (0/23) | 64 / 39 | 1 / 0 | 134 | 0.957 (22/23) |
| vector | 0.671 | 0.043 (1/23) | 99 / 56 | 1 / 0 | 108 | 1.000 (23/23) |

### What these numbers do and do not support

**They do not support an arms comparison.** Three reasons, each
sufficient on its own.

1. The instrument abstains on 90-96% of rows. `semantic_uncertainty` is
   0.902-0.957 on test and 0.957-1.000 on dev. For the rows it can
   decide (3 of 41 on test, tuklas), a comparison is a comparison of
   decidable-ness, not of answer quality.
2. The support check is a string rule. The v1 report already states that
   no string rule can match a proposition ABOUT a provision to the
   provision's own words, so "supported" counts scorer strictness as
   much as answer quality.
3. The two arms score different surfaces. The v1 report already states
   that the vectorless arm's lexical corpus is a strict subset of the
   2,579 nodes it ranks, and that three of its five channels are ones
   the dense arm structurally cannot have.

The v1 report withdrew one comparative citation claim after searching
768,848 rule variants and finding no rule reproducing both arms on both
splits. That correction, R9, is the precedent: a comparative citation
verdict here needs a rule that reproduces both arms on both splits, and
no such rule was found. This plan does not make that claim.

**They do support a finding about the instrument.** Both arms fail the
strict gate essentially everywhere. The v2 layer is finding
mechanically-decidable defects in both: unsupported claims, unreferenced
citations, and on test, consistency mismatches in both arms. That is
the layer working.

---

## 3. Reviewer ask 1: numerical accuracy and citation consistency

### 3.1 What is already rigorous

The quantity reader handles the forms the corpus actually uses:
spelled numbers (`fifteen`), scale words (`hundred`, `thousand`,
`million`), the parenthetical duplicate (`thirty (30) days` reads as one
quantity), percent, currency, and cross-unit comparison within a family
(a duration is compared in days, so a month is 30 days). Identifiers are
excluded by requiring a unit: `Article 1318` and `(2)` are numbers with
no unit, so they never take part in a comparison.

### 3.2 The three gaps, each with the measurement that would settle it

**Gap A: the same number under a different trigger.** A node that says
"within fifteen days following publication" and an answer that says
"within fifteen days of receipt" carry the same quantity under a
different condition. The unit-family rule sees the same value and
passes. Deciding this is semantic, not mechanical: no string rule knows
that "following publication" and "of receipt" are different triggers.

*Not solvable inside the verification layer.* The honest measurement is
how often it happens, and the instrument already reports it as
`semantic_uncertainty`. Counting it would need a judge over the
corpus's trigger phrases, which is a model call, not a rule.

**Gap B: the answer invents a number the node never states.** A
sentence stating "fifteen hours" against a node that says nothing about
hours is currently undecided, because the node states no quantity in
that family. The answer is asserting a number the citation cannot
support, which is exactly the feedback's "numerical answers reference
the correct metrics".

*Experiment:* a new check `numeric_unsupported`, which flags a quantity
stated in a sentence in a family where the cited node states no
quantity at all. Settle it by measuring the false-positive rate on the
two published runs: if the flag fires on sentences whose number is
legitimately from elsewhere in the answer, it is a scorer bug and not
a check. That measurement has not been taken.

**Gap C: the citation list names nodes the prose never references.**
This is measured, and it is large: 254 on tuklas test, 227 on vector
test, 134 / 108 on dev. That is cited node ids with no claim in the
prose referencing them.

*Experiment:* split the count by cause. A node is unreferenced because
the prose cites by article name instead of by `[n]` marker (a notation
difference the check already distinguishes elsewhere), or because the
model emitted a citation it did not use (a real defect). The two have
different fixes and the same number. The split has not been measured.

---

## 4. Reviewer ask 2: evaluation quality

The v2 layer added four measures the v1 citation check could not see:
cross-node contradiction, unreferenced citations, per-answer summary
counts, and semantic uncertainty. All four are mechanical and all four
are lower bounds.

### What would strengthen it next

**The `semantic_uncertainty` rate is itself the finding.** At 0.902 to
1.000, the instrument is abstaining on essentially every row. A
verification layer that cannot decide has no power to catch a wrong
answer. Any work that lowers that rate is worth more than any work that
tightens a rule that already fires.

*Experiment:* measure which check is undecided most often, per row, on
the published runs. The record carries the per-check counts; the
aggregate does not break them down by reason. Settle it by aggregating
the existing per-row records by which counter was zero. That
measurement has not been taken.

**A judge over the rows the instrument cannot decide.** The v1 spot
check measures judge self-consistency (same prompt, same backend, same
temperature, disagreement with itself, 0.073 against a 0.15 threshold).
It is not a human re-score and the report says so. The rows the v2
instrument abstains on are the rows a careful human re-score would
settle. That is a measurement worth taking and it is a model call.

---

## 5. The comparison question, and why this plan does not answer it

"Which arm is better" is not answerable from these runs, for the three
reasons in section 2. A decidable version of it exists and it is a
different experiment:

**Freeze the surface, vary one mechanism component at a time.** Score
both arms over the identical node set with the identical channels,
then change one component (identifier precedence, the `ref` channel,
the hierarchy bonus) and re-measure on that same set. A difference then
attributes to the component, not to surface. Each run is one branch and
one PR, and the first one should be the zero-change baseline to prove
the harness is stable before any lever is pulled.

The levers, in the order the evidence supports funding them:

1. The `ref` channel is 0 across the current corpus because ingestion
   does not parse reference edges. That is a known, named gap with a
   known fix location (the ingestion parser). It is the only channel
   with a measured zero and an identified cause.
2. Identifier precedence already fires on 96.5% of tuklas hits and the
   dense arm has no equivalent. Whether removing it hurts is decidable
   by the frozen-surface experiment above.
3. The hierarchy bonus fires on 97.8% of hits. Same experiment.

---

## 6. Status

- The four v2 checks are merged (`f20d778`) and wired through the store
  and report (`ddfe16f`).
- The v2 dev and test reports are published with the verification
  section.
- Not measured: the numeric false-positive rate (3.2B), the
  unreferenced-citation split by cause (3.2C), the undecided-check
  breakdown (4), and any frozen-surface mechanism experiment (5).

Nothing in this document claims one arm outperforms the other.
