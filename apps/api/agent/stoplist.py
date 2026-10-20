"""Stoplist for the broaden-retry (retrieval.md §3, architecture.md §4).

The list is versioned in code, not ad hoc. Two sources:

1. SQLite FTS5 default English stopwords — verbatim from the FTS5 docs so the
   prefilter and the broaden pass agree on what is noise.
2. The label/filler words ``{said, provision, provides, article, section}`` —
   these carry no lexical signal for BM25 (they name the *kind* of thing, not
   what the thing says) and are the words that survive a first pass only as
   scaffolding.

``broaden`` is deterministic: same query in, same reduced query out. No RNG, no
frequency tuning — the retry is a single fixed transformation, and the budget
counter still increments so it cannot loop.
"""

from __future__ import annotations

#: FTS5 default English stopwords (sqlite fts5 docs, verbatim).
_FTS5_DEFAULT_STOPWORDS: frozenset[str] = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "in",
        "is",
        "it",
        "of",
        "on",
        "or",
        "the",
        "to",
        "was",
        "were",
        "will",
        "with",
    }
)

#: Label/filler words that name a construct rather than describe it.
_LABEL_WORDS: frozenset[str] = frozenset(
    {
        "said",
        "provision",
        "provides",
        "article",
        "section",
    }
)

#: Binding stoplist (retrieval.md §3): FTS5 defaults + label words.
STOPLIST: frozenset[str] = _FTS5_DEFAULT_STOPWORDS | _LABEL_WORDS


def _tokens(query: str) -> list[str]:
    """Whitespace split, lowercased, punctuation stripped per token."""
    out: list[str] = []
    for raw in query.split():
        token = "".join(ch for ch in raw.lower() if ch.isalnum())
        if token:
            out.append(token)
    return out


def broaden(query: str) -> str:
    """Drop the stoplist words; keep identifier tokens (digits, roman numerals,
    quoted phrases survive as ordinary tokens — the normalizer handles them at
    score time, not here).

    If nothing survives, return the longest original token rather than an empty
    string: an empty query would match everything and is worse than no retry.
    Deterministic.
    """
    tokens = _tokens(query)
    kept = [tok for tok in tokens if tok not in STOPLIST]
    if kept:
        return " ".join(kept)
    if tokens:
        return max(tokens, key=len)
    # Empty input: nothing to broaden to. Return the original so the caller's
    # budget still accounts for the attempt rather than silently skipping.
    return query
