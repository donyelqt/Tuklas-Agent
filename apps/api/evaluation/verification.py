"""The mechanical answer verification layer (evaluation.md §4, ``verification-v2``).

The v1 citation check answers one question: do the ids exist, and does each
cited node's text match the sentence that cites it? That check is necessary
and it cannot see three failures a reader would call a wrong answer:

* the answer attributes the right provision but states the WRONG QUANTITY
  ("within fifteen days" against a node that says thirty days);
* the answer names the WRONG ARTICLE while grounding the sentence in a node
  that is a different article, so the string match passes on the surrounding
  wording;
* the answer states the OPPOSITE of the cited provision ("shall not be
  liable" against "shall be liable"), which the substring rule scores as a
  near-perfect match because the negation is one token.

This module measures those three, plus claim-level attribution and the
citation set's overlap with the gold set, MECHANICALLY. It reads text, it
calls no model, and it cannot see the arm label: :func:`verify_answer` has no
arm parameter, so the verification is blinding by construction, the same way
:func:`apps.api.evaluation.judge.judge_payload` is.

**Every check is a LOWER BOUND, and an undecided check is not a failure.**
The rules only flag a quantity when the cited node carries the SAME unit
family with a different value, an article when both the sentence and the
node name one, and a polarity only when the sentence and the node's closest
sentence are near-identical. A sentence the checks cannot decide is counted
in the denominator and not in the numerator: silence is not success, and it
is not failure either.

**``semantic_uncertainty`` is a lower bound, not a verdict.** It is true
when a claim had at least one check the rules could not decide (a numeric
family with a quantity on only one side, an article named on only one side,
or a polarity comparison below the similarity floor). It does not say the
answer is wrong, only that this layer could not say it was right: a
correct answer can carry an undecided check, and the field records that
honestly rather than hiding it.

**What this module is NOT.** It does not know the law. It cannot tell a
correct answer that cites the wrong provision but states a true proposition,
and it cannot tell a wrong answer whose wording is identical to the cited
node. The gold-set overlap is reported beside the checks because the two
failures are different: an answer can be internally consistent and grounded
in the wrong provision, and the report must let a reader see that.

The measures travel in the run record under ``verification_version``, and the
version is a constant here for the same reason the judge's prompt version is:
a number produced by a different rule set is a different number.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from apps.api.evaluation.judge import split_claims
from apps.api.evaluation.report import heading_article, sentence_articles
from apps.api.evaluation.scorer import (
    DEFAULT_FUZZY_THRESHOLD,
    citation_markers,
    citation_supports,
    normalize,
    split_citation_claims,
    split_sentences,
    token_set_ratio,
)

#: The version of the rules in this module. Bump it whenever a rule changes:
#: two runs are comparable on these measures only under the same version.
VERIFICATION_VERSION = "verification-v2"

#: The minimum ``token_set_ratio`` between a sentence and a node sentence
#: before their polarity is compared. Below it the two sentences are about
#: different things, and a negation word in one says nothing about the other.
POLARITY_MIN_SIMILARITY = 90.0


# ---------------------------------------------------------------------------
# Quantities
# ---------------------------------------------------------------------------

#: The number words the corpus spells out. Legal text writes "fifteen days"
#: as often as "15 days", and a reading that only sees digits would miss the
#: majority of the Civil Code's quantities.
_ONES: dict[str, int] = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
}

_TENS: dict[str, int] = {
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
}

#: A scale word multiplies the number it follows. ``hundred`` is handled
#: separately in :func:`_spelled_value`: it scales the current run of words
#: instead of rolling it into the total, so "three hundred thousand" is
#: 300,000 and not 300 + 100,000.
_SCALE_WORDS: dict[str, int] = {
    "thousand": 1_000,
    "million": 1_000_000,
    "billion": 1_000_000_000,
}

_SPELLED_WORDS = frozenset(_ONES) | frozenset(_TENS) | {"hundred"} | frozenset(_SCALE_WORDS)

#: The unit a quantity is measured in, and the family it is compared within.
#: The factor converts the unit to the family's base unit: a duration is
#: compared in days, so a month is 30 days and a year is 365. The factors are
#: conventional, not exact, and the approximation errs toward NOT flagging:
#: "30 days" and "one month" compare equal, which is the safe direction for a
#: measure that must not accuse a correct answer.
_UNIT_GROUPS: dict[str, tuple[str, float]] = {
    "day": ("duration", 1.0),
    "week": ("duration", 7.0),
    "month": ("duration", 30.0),
    "year": ("duration", 365.0),
    "hour": ("duration", 1.0 / 24.0),
    "percent": ("percent", 1.0),
    "peso": ("peso", 1.0),
    "dollar": ("dollar", 1.0),
}

_UNIT_WORDS: dict[str, str] = {
    "day": "day",
    "days": "day",
    "week": "week",
    "weeks": "week",
    "month": "month",
    "months": "month",
    "year": "year",
    "years": "year",
    "hour": "hour",
    "hours": "hour",
    "percent": "percent",
    "%": "percent",
    "peso": "peso",
    "pesos": "peso",
    "dollar": "dollar",
    "dollars": "dollar",
}

#: A currency PREFIX names the unit of the digits that follow it.
_CURRENCY_PREFIX_UNITS: dict[str, str] = {
    "php": "peso",
    "\u20b1": "peso",
    "$": "dollar",
    "usd": "dollar",
}

_TOKEN_RE = re.compile(r"[A-Za-z]+|\d[\d,]*(?:\.\d+)?|[\u20b1$%]")
_DIGITS_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")
_WORD_RE = re.compile(r"[a-z]+")

#: A token of negation. The apostrophe stems are included because the
#: tokenizer splits "isn't" into "isn" and "t": without them a contraction
#: would read as an affirmation. "won" is deliberately absent: it is also the
#: past tense of "win", and a false negation is worse than a missed one.
_NEGATION_TOKENS = frozenset(
    {
        "not",
        "no",
        "never",
        "none",
        "nothing",
        "nobody",
        "nowhere",
        "nor",
        "neither",
        "cannot",
        "without",
        "isn",
        "aren",
        "wasn",
        "weren",
        "don",
        "doesn",
        "didn",
        "wouldn",
        "couldn",
        "shouldn",
        "hasn",
        "haven",
        "ain",
    }
)


@dataclass(frozen=True)
class NumericMention:
    """One quantity a text states, with the unit and the scale it uses.

    ``value`` is the number as written and ``scale`` is the word that follows
    it, so "2 million pesos" is ``value=2, scale=1000000`` and "fifty
    thousand pesos" is ``value=50000, scale=1``. Both are compared through
    :func:`base_value`, which also converts the unit to its family's base.
    """

    raw: str
    value: float
    unit: str
    scale: int


def base_value(mention: NumericMention) -> float:
    """The quantity in its family's base unit, as a float."""
    group = _UNIT_GROUPS.get(mention.unit)
    factor = group[1] if group is not None else 1.0
    return mention.value * mention.scale * factor


def _tokens(text: str) -> list[tuple[str, int, int]]:
    return [(match.group(0), match.start(), match.end()) for match in _TOKEN_RE.finditer(text)]


def _digits_value(token: str) -> float:
    return float(token.replace(",", ""))


def _scale_at(tokens: Sequence[tuple[str, int, int]], index: int) -> tuple[int, int]:
    """The scale word at ``index``, and the index after it."""
    if index < len(tokens):
        word = tokens[index][0].casefold()
        if word in _SCALE_WORDS:
            return _SCALE_WORDS[word], index + 1
    return 1, index


def _unit_at(tokens: Sequence[tuple[str, int, int]], index: int) -> tuple[str, int] | None:
    """The unit word at ``index``, and the index after it."""
    if index >= len(tokens):
        return None
    word = tokens[index][0].casefold()
    if word in _UNIT_WORDS:
        return _UNIT_WORDS[word], index + 1
    if word == "per" and index + 1 < len(tokens) and tokens[index + 1][0].casefold() == "cent":
        return "percent", index + 2
    return None


def _spelled_value(tokens: Sequence[tuple[str, int, int]], index: int) -> tuple[float | None, int]:
    """The value of the run of number words at ``index``, and the index after it."""
    total = 0.0
    current = 0.0
    consumed = False
    cursor = index
    limit = min(len(tokens), index + 8)
    while cursor < limit:
        word = tokens[cursor][0].casefold()
        if word in _ONES:
            current += _ONES[word]
        elif word in _TENS:
            current += _TENS[word]
        elif word == "hundred":
            current = max(current, 1.0) * 100
        elif word in _SCALE_WORDS:
            total += max(current, 1.0) * _SCALE_WORDS[word]
            current = 0.0
        elif (
            word == "a"
            and cursor + 1 < len(tokens)
            and tokens[cursor + 1][0].casefold() in ({"hundred"} | set(_SCALE_WORDS))
        ):
            current = max(current, 1.0)
        elif word == "and" and consumed:
            pass
        else:
            break
        consumed = True
        cursor += 1
    if not consumed:
        return None, index
    return total + current, cursor


def _currency_mention(
    text: str,
    tokens: Sequence[tuple[str, int, int]],
    index: int,
    unit: str,
) -> tuple[NumericMention, int] | None:
    """The quantity a currency prefix at ``index`` introduces, if it has digits."""
    if index + 1 >= len(tokens):
        return None
    token = tokens[index + 1][0]
    if not _DIGITS_RE.fullmatch(token):
        return None
    scale, after_scale = _scale_at(tokens, index + 2)
    raw = text[tokens[index][1] : tokens[after_scale - 1][2]]
    return NumericMention(raw=raw, value=_digits_value(token), unit=unit, scale=scale), after_scale


def numeric_mentions(text: str) -> list[NumericMention]:
    """Every quantity a text states, in order.

    A number counts as a quantity only when a UNIT follows it: an amount of
    time, a percentage, or a currency. That is what keeps an article number,
    a list index, a page number and a footnote marker out of the readings:
    "Article 1318" and "(2)" are numbers with no unit, so they are not
    quantities and they never take part in a comparison.

    The parenthetical duplicate the corpus writes ("thirty (30) days") yields
    ONE mention, not two: the number words are followed by digits instead of
    a unit, so the word run stops, and the digits that follow are the one
    quantity with the unit. The two readings never disagree in the corpus,
    and if they did the digit form is the one the comparison would use.
    """
    tokens = _tokens(text)
    mentions: list[NumericMention] = []
    index = 0
    while index < len(tokens):
        token, start, _end = tokens[index]
        folded = token.casefold()

        prefix_unit = _CURRENCY_PREFIX_UNITS.get(folded)
        if prefix_unit is not None:
            outcome = _currency_mention(text, tokens, index, prefix_unit)
            if outcome is not None:
                mention, index = outcome
                mentions.append(mention)
                continue

        if _DIGITS_RE.fullmatch(token):
            value = _digits_value(token)
            scale, after_scale = _scale_at(tokens, index + 1)
            unit_outcome = _unit_at(tokens, after_scale)
            if unit_outcome is not None:
                unit, after_unit = unit_outcome
                mentions.append(
                    NumericMention(
                        raw=text[start : tokens[after_unit - 1][2]],
                        value=value,
                        unit=unit,
                        scale=scale,
                    )
                )
                index = after_unit
                continue
            index += 1
            continue

        if folded in _SPELLED_WORDS or (
            folded == "a"
            and index + 1 < len(tokens)
            and tokens[index + 1][0].casefold() in ({"hundred"} | set(_SCALE_WORDS))
        ):
            spelled_value, after_number = _spelled_value(tokens, index)
            if spelled_value is None:
                index += 1
                continue
            unit_outcome = _unit_at(tokens, after_number)
            if unit_outcome is not None:
                unit, after_unit = unit_outcome
                mentions.append(
                    NumericMention(
                        raw=text[start : tokens[after_unit - 1][2]],
                        value=spelled_value,
                        unit=unit,
                        scale=1,
                    )
                )
                index = after_unit
                continue
            index = after_number
            continue

        index += 1
    return mentions


def _comparable_values(text: str) -> dict[str, set[float]]:
    """The text's quantities, grouped by unit family and reduced to base units."""
    grouped: dict[str, set[float]] = {}
    for mention in numeric_mentions(text):
        entry = _UNIT_GROUPS.get(mention.unit)
        if entry is None:
            continue
        grouped.setdefault(entry[0], set()).add(base_value(mention))
    return grouped


def numeric_consistency(sentence: str, node_text: str) -> tuple[int, int]:
    """(checked, mismatched) unit families between a sentence and its node.

    A family is CHECKED only when both sides state a quantity in it, and it
    is MISMATCHED only when no value the sentence states equals a value the
    node states in that family. A family the node says nothing about is
    undecided: the node cannot contradict a quantity it never mentions.
    """
    stated = _comparable_values(sentence)
    supported = _comparable_values(node_text)
    checked = 0
    mismatched = 0
    for group, values in stated.items():
        node_values = supported.get(group)
        if not node_values:
            continue
        checked += 1
        if not any(
            math.isclose(value, node_value, rel_tol=1e-9, abs_tol=1e-9)
            for value in values
            for node_value in node_values
        ):
            mismatched += 1
    return checked, mismatched


def _answer_consistency(claims: Sequence[ClaimVerification]) -> tuple[int, int]:
    """(checked, mismatched) unit families across the whole answer.

    A family is CHECKED when two or more claims state a quantity in it, and
    MISMATCHED when no single value is shared across all of them. A family
    with a quantity in only one claim is undecided: one claim cannot
    contradict itself, so it contributes neither to the check nor to the
    mismatch count.
    """
    by_family: dict[str, list[frozenset[float]]] = {}
    for claim in claims:
        for group, values in claim.values.items():
            by_family.setdefault(group, []).append(values)
    checked = 0
    mismatched = 0
    for sets in by_family.values():
        if len(sets) < 2:
            continue
        checked += 1
        shared = sets[0]
        for values in sets[1:]:
            shared = shared & values
        if not shared:
            mismatched += 1
    return checked, mismatched


# ---------------------------------------------------------------------------
# Article identifiers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EvidenceNode:
    """A cited node as the verification reads it: its own heading and text."""

    node_id: str
    title: str
    text: str


def article_consistency(sentence: str, node: EvidenceNode) -> tuple[int, int]:
    """(checked, mismatched): does the sentence's article name this node?

    The check is decided only when BOTH sides name an article: the sentence
    writes one ("Article 1318", "Articles 55, 61", "Art. 1318"), and the
    node's own heading names one. A sentence that names no article makes no
    identifier claim to contradict, and a node with no article heading (a
    chapter, a book) has no article number to compare.

    A sentence that names SEVERAL articles passes for each of its cited
    nodes whose own article is among them, which is what a cross-reference
    sentence does: "Article 1624 refers to article 1475" cites both, and each
    citation is supported by the sentence naming its article.
    """
    named = sentence_articles(sentence)
    own = heading_article(node.title)
    if own is None:
        own = heading_article(node.text)
    if not named or own is None:
        return 0, 0
    return 1, 0 if own in named else 1


# ---------------------------------------------------------------------------
# Polarity
# ---------------------------------------------------------------------------


def is_negated(text: str) -> bool:
    """Whether a text carries a negation token."""
    return any(word in _NEGATION_TOKENS for word in _WORD_RE.findall(text.casefold()))


def polarity_consistency(sentence: str, node_text: str) -> tuple[int, int]:
    """(checked, mismatched): does the node's closest sentence agree on polarity?

    The node is read sentence by sentence, and the comparison uses the
    sentence closest to the claim. If no node sentence reaches
    :data:`POLARITY_MIN_SIMILARITY`, the two texts are not about the same
    proposition and the check is undecided rather than failed.
    """
    best_score = 0.0
    best_sentence = ""
    for candidate in split_sentences(node_text):
        score = token_set_ratio(sentence, candidate)
        if score > best_score:
            best_score = score
            best_sentence = candidate
    if not best_sentence or best_score < POLARITY_MIN_SIMILARITY:
        return 0, 0
    return 1, 0 if is_negated(sentence) == is_negated(best_sentence) else 1


# ---------------------------------------------------------------------------
# The claim and the answer
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ClaimVerification:
    """One cited claim, checked against the node its marker names."""

    marker: int
    node_id: str
    sentence: str
    supported: bool
    numeric_checks: int
    numeric_mismatches: int
    article_checks: int
    article_mismatches: int
    polarity_checks: int
    polarity_mismatches: int
    values: dict[str, frozenset[float]]

    @property
    def consistent(self) -> bool:
        """True when the claim is supported and no check found a mismatch."""
        return (
            self.supported
            and self.numeric_mismatches == 0
            and self.article_mismatches == 0
            and self.polarity_mismatches == 0
        )

    @property
    def undecided(self) -> bool:
        """True when at least one check could not be decided.

        A check is undecided when it returned ``checked == 0``: the two
        sides had nothing to compare (no quantity in the family, no article
        named on either side, no node sentence above the polarity floor).
        """
        return self.numeric_checks == 0 or self.article_checks == 0 or self.polarity_checks == 0


@dataclass(frozen=True)
class AnswerVerification:
    """One answer's verification record, as the run stores and aggregates it."""

    answerable: bool
    insufficient: bool
    n_citations: int
    n_cited_claim_checks: int
    n_cited_claims_supported: int
    n_uncited_claims: int
    n_numeric_checks: int
    n_numeric_mismatches: int
    n_article_checks: int
    n_article_mismatches: int
    n_polarity_checks: int
    n_polarity_mismatches: int
    n_gold_nodes: int
    n_gold_nodes_cited: int
    claims: tuple[ClaimVerification, ...]
    verified: bool
    consistency: tuple[int, int]
    n_citations_unreferenced: int
    semantic_uncertainty: bool

    @property
    def claim_citation_coverage(self) -> float:
        """The fraction of cited claim checks the cited nodes support."""
        if not self.n_cited_claim_checks:
            return 0.0
        return self.n_cited_claims_supported / self.n_cited_claim_checks

    @property
    def gold_citation_recall(self) -> float | None:
        """The fraction of gold nodes the answer cites, or None without golds."""
        if not self.n_gold_nodes:
            return None
        return self.n_gold_nodes_cited / self.n_gold_nodes

    @property
    def gold_citation_precision(self) -> float | None:
        """The fraction of cited nodes that are gold, or None without citations."""
        if not self.n_citations:
            return None
        return self.n_gold_nodes_cited / self.n_citations

    def to_record(self) -> dict[str, Any]:
        """The flat record the run writes beside the v1 fields."""
        return {
            "verification_version": VERIFICATION_VERSION,
            "answer_verified": self.verified,
            "cited_claim_checks": self.n_cited_claim_checks,
            "cited_claims_supported": self.n_cited_claims_supported,
            "uncited_claims": self.n_uncited_claims,
            "numeric_checks": self.n_numeric_checks,
            "numeric_mismatches": self.n_numeric_mismatches,
            "article_checks": self.n_article_checks,
            "article_mismatches": self.n_article_mismatches,
            "polarity_checks": self.n_polarity_checks,
            "polarity_mismatches": self.n_polarity_mismatches,
            "gold_nodes_total": self.n_gold_nodes,
            "gold_nodes_cited": self.n_gold_nodes_cited,
            "answer_claim_checks": self.n_cited_claim_checks,
            "answer_claims_supported": self.n_cited_claims_supported,
            "answer_claims_unsupported": self.n_cited_claim_checks - self.n_cited_claims_supported,
            "answer_consistency_checks": self.consistency[0],
            "answer_consistency_mismatches": self.consistency[1],
            "answer_citations_unreferenced": self.n_citations_unreferenced,
            "answer_semantic_uncertainty": self.semantic_uncertainty,
        }


def verify_claim(
    marker: int,
    sentence: str,
    node: EvidenceNode | None,
    *,
    threshold: int = DEFAULT_FUZZY_THRESHOLD,
) -> ClaimVerification:
    """Check one cited claim against the node its marker names.

    A marker that names no citation, or a citation to a node the store does
    not have, arrives here as ``node=None``: the claim is unsupported and no
    comparison is attempted, because there is nothing to compare it with.
    """
    if node is None:
        return ClaimVerification(
            marker=marker,
            node_id="",
            sentence=sentence,
            supported=False,
            numeric_checks=0,
            numeric_mismatches=0,
            article_checks=0,
            article_mismatches=0,
            polarity_checks=0,
            polarity_mismatches=0,
            values={},
        )
    supported = citation_supports(sentence, node.text, threshold=threshold)
    numeric_checks, numeric_mismatches = numeric_consistency(sentence, node.text)
    article_checks, article_mismatches = article_consistency(sentence, node)
    polarity_checks, polarity_mismatches = polarity_consistency(sentence, node.text)
    return ClaimVerification(
        marker=marker,
        node_id=node.node_id,
        sentence=sentence,
        supported=supported,
        numeric_checks=numeric_checks,
        numeric_mismatches=numeric_mismatches,
        article_checks=article_checks,
        article_mismatches=article_mismatches,
        polarity_checks=polarity_checks,
        polarity_mismatches=polarity_mismatches,
        values={group: frozenset(values) for group, values in _comparable_values(sentence).items()},
    )


def verify_answer(
    *,
    answerable: bool,
    insufficient: bool,
    answer_markdown: str,
    citations: Sequence[Mapping[str, Any]],
    evidence: Mapping[str, EvidenceNode],
    gold_nodes: Sequence[str] = (),
    threshold: int = DEFAULT_FUZZY_THRESHOLD,
) -> AnswerVerification:
    """Verify one answer against the nodes it cites.

    The inputs are exactly what the run already holds: the answer markdown,
    the citation list the answer was assembled with, the text and title of
    the cited nodes, the question's gold set, and the answerable flag. There
    is NO arm parameter, which is the blinding.

    ``verified`` is the strict v2 gate:

    * an UNANSWERABLE row passes only as a refusal that cites nothing;
    * an ANSWERABLE row passes only when it is not a refusal, it cites
      something, every distinct cited claim is supported, no numeric,
      article or polarity check mismatched, and no factual sentence is left
      uncited.

    The gate deliberately does NOT require a gold node to be cited. Whether
    the right provision was retrieved is a retrieval question, and it is
    reported beside the gate as ``gold_citation_recall``. Folding it into
    ``verified`` would make one number answer two questions, and a reader
    could no longer tell a consistent answer from a well-retrieved one.

    Distinct (marker, sentence) pairs are checked once. The v1 answers repeat
    sentences, and a repeated sentence is one claim stated twice, not two
    claims: counting the repetition would inflate the denominator with
    checks that cannot disagree.
    """
    by_number: dict[int, str] = {}
    cited_ids: list[str] = []
    for citation in citations:
        node_id = citation.get("node_id")
        if not isinstance(node_id, str) or not node_id:
            continue
        if node_id not in cited_ids:
            cited_ids.append(node_id)
        number = citation.get("n")
        if isinstance(number, int) and not isinstance(number, bool):
            by_number[number] = node_id

    claims: list[ClaimVerification] = []
    seen: set[tuple[int, str]] = set()
    for claim in split_citation_claims(answer_markdown):
        key = (claim.marker, normalize(claim.sentence))
        if key in seen:
            continue
        seen.add(key)
        node_id = by_number.get(claim.marker)
        node = evidence.get(node_id) if node_id else None
        claims.append(verify_claim(claim.marker, claim.sentence, node, threshold=threshold))

    uncited = 0
    seen_uncited: set[str] = set()
    for sentence in split_claims(answer_markdown):
        if citation_markers(sentence):
            continue
        uncited_key = normalize(sentence)
        if not uncited_key or uncited_key in seen_uncited:
            continue
        seen_uncited.add(uncited_key)
        uncited += 1

    gold = [node_id for node_id in gold_nodes if node_id]
    gold_cited = sum(1 for node_id in gold if node_id in cited_ids)

    n_checks = len(claims)
    n_supported = sum(1 for claim in claims if claim.supported)
    n_numeric_checks = sum(claim.numeric_checks for claim in claims)
    n_numeric_mismatches = sum(claim.numeric_mismatches for claim in claims)
    n_article_checks = sum(claim.article_checks for claim in claims)
    n_article_mismatches = sum(claim.article_mismatches for claim in claims)
    n_polarity_checks = sum(claim.polarity_checks for claim in claims)
    n_polarity_mismatches = sum(claim.polarity_mismatches for claim in claims)

    consistency = _answer_consistency(claims)
    unreferenced = sum(
        1 for node_id in cited_ids if not any(claim.node_id == node_id for claim in claims)
    )

    if answerable:
        verified = (
            not insufficient
            and bool(cited_ids)
            and n_checks > 0
            and n_supported == n_checks
            and n_numeric_mismatches == 0
            and n_article_mismatches == 0
            and n_polarity_mismatches == 0
            and uncited == 0
            and consistency[1] == 0
            and unreferenced == 0
        )
    else:
        verified = insufficient and not cited_ids

    return AnswerVerification(
        answerable=answerable,
        insufficient=insufficient,
        n_citations=len(cited_ids),
        n_cited_claim_checks=n_checks,
        n_cited_claims_supported=n_supported,
        n_uncited_claims=uncited,
        n_numeric_checks=n_numeric_checks,
        n_numeric_mismatches=n_numeric_mismatches,
        n_article_checks=n_article_checks,
        n_article_mismatches=n_article_mismatches,
        n_polarity_checks=n_polarity_checks,
        n_polarity_mismatches=n_polarity_mismatches,
        n_gold_nodes=len(gold),
        n_gold_nodes_cited=gold_cited,
        claims=tuple(claims),
        verified=verified,
        consistency=consistency,
        n_citations_unreferenced=unreferenced,
        semantic_uncertainty=any(claim.undecided for claim in claims),
    )


__all__ = [
    "POLARITY_MIN_SIMILARITY",
    "VERIFICATION_VERSION",
    "AnswerVerification",
    "ClaimVerification",
    "EvidenceNode",
    "NumericMention",
    "article_consistency",
    "base_value",
    "is_negated",
    "numeric_consistency",
    "numeric_mentions",
    "polarity_consistency",
    "verify_answer",
    "verify_claim",
]
