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

#: The version id of the list above.
#:
#: The list is "versioned in code, not ad hoc" (retrieval.md §3), and a
#: list that is versioned in code but not NAMED is not versioned at all:
#: a run record that pins a "stoplist version" has to name a version the
#: code actually has, or the record describes a list nobody can identify.
#:
#: The id covers the WHOLE list - the FTS5 default stopwords AND the
#: label/filler words - because both are in :data:`STOPLIST` and the
#: broaden-retry drops both. A change to either part changes what the
#: retry drops, so it changes the version.
#:
#: The name is a label, not an enumeration: it says which list the code
#: has, and the constant's definition says what that list is. The run
#: config checker compares the config's pinned version against THIS
#: constant, so a config naming a version the code does not have is
#: refused rather than recorded.
STOPLIST_VERSION: str = "fts5-english-v1"


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
        # Every token was a stopword. Returning the longest one would hand the
        # caller a stopword-only query, which is WORSE than the original: a
        # stopword like "the" occurs in ~2268 of the corpus nodes, so the
        # min-max-normalized lex component gives the top hit lex=1.0 and the
        # gate passes on a query that means nothing. The original query is the
        # honest answer here - it is what the caller already tried.
        return query
    # Empty input: nothing to broaden to. Return the original so the caller's
    # budget still accounts for the attempt rather than silently skipping.
    return query


def identifier_query(query: str) -> str:
    """The identifier-only query of the broaden-retry (retrieval.md sec 3).

    The contract names the retry as "stopword-strip + identifier-only query".
    The stopword strip is :func:`broaden`; this is the second half: the
    canonical labels the query carries, and nothing else.

    "article 1318" -> "art 1318"; "the said provision about 1318" -> "1318"
    (a bare number is not a label, so it does not survive as one).

    Retrieval resolves the labels through the identifier index, so a query that
    carries only its identifiers hits the provisions the query is ABOUT, and
    not the provisions that merely share a word with it. That is a different
    neighbourhood from the stopword-stripped text, which is why the contract
    asks for both.

    Returns "" when the query carries no identifier - the caller then keeps
    the stopword-stripped form, because an empty query would match everything.
    """
    from apps.api.retrieval.api import identifier_labels

    labels = identifier_labels(query)
    if not labels:
        return ""
    # Order is stable (extract_labels preserves it) and duplicates are gone.
    return " ".join(labels)
