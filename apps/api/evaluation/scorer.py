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
"""

from __future__ import annotations

import re

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


__all__ = [
    "DEFAULT_FUZZY_THRESHOLD",
    "claim_fraction",
    "claim_matches",
    "matched_claims",
    "normalize",
    "token_set_ratio",
]
