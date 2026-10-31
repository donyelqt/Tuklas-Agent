"""The shared correctness scorer (evaluation.md §3).

One scorer, used by BOTH arms, on the SAME threshold:

    a claim is matched when the case-folded, punctuation-stripped answer
    CONTAINS the claim as a substring, OR when RapidFuzz's
    ``token_set_ratio`` against the claim is >= 85.

The threshold is a CONSTANT of this module, and the run config records it
(``fuzzy_threshold``), so a run can name the number its metrics were
computed with. The scorer is deliberately the only place the string
comparison lives: a second copy in the judge, or in the report writer,
would be a second rule that could drift from this one.

Why two rules and not one. The substring rule is exact and cheap, and it
catches a claim the answer quotes verbatim. The fuzzy rule catches a claim
the answer paraphrases, which is the common case for a statute: "consent of
the contracting parties" and "the consent of the parties" are the same
claim. The fuzzy rule alone would be enough, but the substring rule makes
the common case exact, and the OR of the two is strictly more permissive
than either alone — which is the right direction for a metric that should
not punish a correct answer for a wording difference.

**The same rule is also the MECHANICAL half of the citation check**
(evaluation.md §4: "cited ids exist + support the attached claim"). The
support check asks whether a cited node's text supports the SENTENCE the
answer attached the citation to, and it asks it with exactly this rule and
this threshold. Reusing the one comparison keeps the harness's two string
rules in one place: a second, slightly different rule for the citation
check would make the two metrics disagree about what "the same words"
means, and a reader would not know which one a number came from.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from rapidfuzz import fuzz

#: The fuzzy threshold evaluation.md §3 fixes: RapidFuzz ``token_set_ratio``
#: >= 85 counts as a match.
DEFAULT_FUZZY_THRESHOLD = 85

#: Punctuation is stripped before the substring comparison, so "consent,"
#: and "consent" compare equal. The set is the ASCII punctuation plus the
#: typographic dashes and quotes the corpus and the answers actually carry.
_PUNCTUATION_RE = re.compile(r"[^\w\s]", re.UNICODE)

#: Whitespace is collapsed, so a line break inside a claim does not stop it
#: from matching.
_WHITESPACE_RE = re.compile(r"\s+")


def normalize(text: str) -> str:
    """Case-fold, strip punctuation, collapse whitespace.

    The normalization is the same for the claim and for the haystack, so a
    comparison is never decided by the casing of one side.
    """
    folded = text.casefold()
    without_punctuation = _PUNCTUATION_RE.sub(" ", folded)
    return _WHITESPACE_RE.sub(" ", without_punctuation).strip()


def token_set_ratio(claim: str, haystack: str) -> float:
    """RapidFuzz's ``token_set_ratio`` between a claim and a haystack.

    The score is 0.0 for an empty claim, so an empty claim never matches
    anything: a claim list with an empty entry would otherwise score a
    free match for every answer.
    """
    normalized_claim = normalize(claim)
    if not normalized_claim:
        return 0.0
    return float(fuzz.token_set_ratio(normalized_claim, normalize(haystack)))


def claim_matches(claim: str, haystack: str, *, threshold: int = DEFAULT_FUZZY_THRESHOLD) -> bool:
    """True when ``claim`` is matched by ``haystack``.

    The substring rule is checked on the NORMALIZED forms, so punctuation
    and casing cannot decide the match.
    """
    normalized_claim = normalize(claim)
    if not normalized_claim:
        return False
    normalized_haystack = normalize(haystack)
    if normalized_claim in normalized_haystack:
        return True
    return token_set_ratio(claim, haystack) >= threshold


def matched_claims(
    claims: list[str], haystack: str, *, threshold: int = DEFAULT_FUZZY_THRESHOLD
) -> list[str]:
    """The claims of ``claims`` that ``haystack`` matches, in order."""
    return [claim for claim in claims if claim_matches(claim, haystack, threshold=threshold)]


def claim_fraction(
    claims: list[str], haystack: str, *, threshold: int = DEFAULT_FUZZY_THRESHOLD
) -> float:
    """The fraction of ``claims`` matched by ``haystack``.

    Zero claims score 0.0, not 1.0. A question with no expected claims has
    nothing to be correct about, and scoring it as perfect would inflate
    the correctness aggregate with rows that were never scored.
    """
    if not claims:
        return 0.0
    hits = len(matched_claims(claims, haystack, threshold=threshold))
    return hits / len(claims)


# ---------------------------------------------------------------------------
# The mechanical citation-support check (evaluation.md §4)
# ---------------------------------------------------------------------------

#: A sentence splitter. It is the SAME rule the judge uses to segment an
#: answer into claims (apps/api/evaluation/judge.py), because the citation
#: check and the faithfulness check must segment the same answer the same
#: way: a sentence the judge does not see is a sentence the citation check
#: must not score either.
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9(\"'])")

#: A citation marker in an answer: ``[3]``, and the range form ``[1-5]``.
#: The range is expanded to every number it names, because a range marker
#: attaches the sentence to EVERY node in it.
_MARKER_RE = re.compile(r"\[(\d+)(?:\s*-\s*(\d+))?\]")

#: The most markers one sentence may name. A model that emits a range wider
#: than this is treated as naming no citation rather than as naming an
#: unbounded set: the bound keeps the check mechanical and prevents a
#: malformed answer from turning into thousands of checks.
MAX_MARKERS_PER_SENTENCE = 32


@dataclass(frozen=True)
class CitationClaim:
    """One citation claim: a marker and the sentence that carries it.

    ``marker`` is the citation NUMBER the answer wrote (``[3]`` -> 3), not
    a position in a list. The number is what the prompt showed the model
    (generation/prompts.py numbers the rendered evidence from 1), so the
    number is what the answer's marker means.
    """

    marker: int
    sentence: str


def split_sentences(text: str) -> list[str]:
    """The sentences of a text, by the one splitter this harness uses.

    The splitter is exported because the sentence is the unit of the judge,
    of the citation check and of the v2 verification: a second splitter
    would let the same answer be segmented two ways, and a sentence one
    measure sees would be a sentence another measure does not.
    """
    if not text:
        return []
    return [part.strip() for part in _SENTENCE_RE.split(text) if part.strip()]


def citation_markers(text: str) -> list[int]:
    """The citation numbers a text names, with a range expanded.

    ``[3]`` names 3 and ``[1-5]`` names 1, 2, 3, 4 and 5, because a range
    marker attaches the sentence to EVERY node in it.
    """
    markers: list[int] = []
    for match in _MARKER_RE.finditer(text):
        first = int(match.group(1))
        last = int(match.group(2)) if match.group(2) else first
        if last < first:
            first, last = last, first
        markers.extend(range(first, last + 1))
    return markers


def strip_citation_markers(text: str) -> str:
    """The text with every citation marker removed."""
    return _MARKER_RE.sub(" ", text).strip()


def split_citation_claims(answer_markdown: str) -> list[CitationClaim]:
    """The (marker, sentence) pairs of an answer's citation claims.

    A sentence with NO marker is not a citation claim: it makes no
    assertion about any node, so there is nothing to check. A sentence
    with markers is a claim about each node it names.

    The markers are stripped from the sentence before it is returned: the
    marker is the reference, not part of the proposition, and leaving it
    in would let the checker's comparison be helped by the very number it
    is supposed to be checking.
    """
    claims: list[CitationClaim] = []
    for sentence in split_sentences(answer_markdown):
        markers = citation_markers(sentence)
        if not markers:
            continue
        if len(markers) > MAX_MARKERS_PER_SENTENCE:
            continue
        text = strip_citation_markers(sentence)
        if not text:
            continue
        for marker in markers:
            claims.append(CitationClaim(marker=marker, sentence=text))
    return claims


def citation_supports(
    sentence: str, node_text: str, *, threshold: int = DEFAULT_FUZZY_THRESHOLD
) -> bool:
    """Whether a node's text supports the sentence that cites it.

    The rule is the SHARED scorer's rule, at the shared threshold: the
    sentence is matched by the node's text when the normalized sentence is
    a substring of the normalized node text, or when the RapidFuzz
    ``token_set_ratio`` between them is at least the threshold.

    The check is MECHANICAL and ARM-BLIND: it sees a sentence and a node's
    text, and it cannot tell which arm produced them. It is therefore the
    half of the citation metric that does not depend on a model, and it is
    the half that can be reproduced exactly.
    """
    if not node_text.strip():
        return False
    return claim_matches(sentence, node_text, threshold=threshold)


__all__ = [
    "DEFAULT_FUZZY_THRESHOLD",
    "CitationClaim",
    "MAX_MARKERS_PER_SENTENCE",
    "citation_markers",
    "citation_supports",
    "claim_fraction",
    "claim_matches",
    "matched_claims",
    "normalize",
    "split_citation_claims",
    "split_sentences",
    "strip_citation_markers",
    "token_set_ratio",
]
