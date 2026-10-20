"""The advice blocklist oracle (CONSTRAINTS.md, architecture.md sec 6).

ONE oracle. The banned phrases live here and nowhere else: the service
enforces them against the generated text and the tests assert against them.
A second copy of the list would be a second rule, and two rules drift.

The prompt does NOT spell the phrases out - CONSTRAINTS.md forbids advice
phrasing IN prompts, so the prompt states the rule as a framing instruction
and this module is where the phrases are known. The prompt module asks this
oracle whether its own text is clean, which keeps that rule honest.

The four phrases are the ones CONSTRAINTS.md names: "you should", "I advise",
"your case", "you must". They are matched case-insensitively and must appear
as whole words, so surrounding punctuation and quoting do not hide them
(``"you should"`` trips) and a longer word that merely contains one does not
(``yourcases`` does not).
"""

from __future__ import annotations

import re

#: The banned phrases, exactly as CONSTRAINTS.md names them. This set is the
#: only place the list exists: the service enforces it and the prompt module
#: checks itself against it.
ADVICE_PHRASES: frozenset[str] = frozenset({"you should", "I advise", "your case", "you must"})


def _pattern(phrase: str) -> re.Pattern[str]:
    """The whole-word matcher for one phrase.

    The words are joined by ``\\s+`` so a phrase split across a line break
    still trips, and anchored with word boundaries so a longer word that
    merely contains the phrase does not.
    """
    words = [re.escape(word) for word in phrase.split()]
    return re.compile(r"\b" + r"\s+".join(words) + r"\b", re.IGNORECASE)


#: (canonical phrase, matcher) pairs, ordered by the canonical phrase so the
#: matching is deterministic.
_MATCHERS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (phrase, _pattern(phrase)) for phrase in sorted(ADVICE_PHRASES)
)


def find_advice(text: str) -> list[str]:
    """The banned phrases present in ``text``, in order of first appearance.

    The canonical spelling from :data:`ADVICE_PHRASES` is returned, so the
    caller sees the phrase the oracle knows rather than the casing the model
    happened to use. Duplicates are reported once.
    """
    if not text:
        return []
    hits: list[tuple[int, str]] = []
    for phrase, pattern in _MATCHERS:
        match = pattern.search(text)
        if match is not None:
            hits.append((match.start(), phrase))
    hits.sort()
    return [phrase for _position, phrase in hits]


def contains_advice(text: str) -> bool:
    """True when ``text`` contains any banned phrase (the single oracle's verdict)."""
    return bool(find_advice(text))
