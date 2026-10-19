"""Label normalization and range expansion (retrieval.md §4).

Identifiers are matched on LABELS, never on ids: node ids are opaque D1 handles
(``apps.api.ingestion.ids``), so parsing an id for semantics is forbidden. Both
the query side ("Art. 1318", "§1318") and the node-title side ("Article 1318")
reduce to the same canonical label families before any comparison: a bare
§ sign emits both ``art N`` and ``sec N`` (retrieval.md §4), and the
canonical map includes the 0.5-level container word ``subch``.
"""

from __future__ import annotations

import re

#: Word pattern → canonical word, longest alternatives first so "articles" is
#: consumed before the bare "art" prefix could win.
_CANON: tuple[tuple[str, str], ...] = (
    (r"articles?", "art"),
    (r"arts?", "art"),
    (r"sections?", "sec"),
    (r"secs?", "sec"),
    (r"subchapters?", "subch"),
    (r"subchs?", "subch"),
    (r"chapters?", "ch"),
    (r"chs?", "ch"),
    (r"titles?", "title"),
    (r"books?", "book"),
)

#: One alternation of all canonicalizable label words.
_CANON_ALT = "|".join(f"(?:{pat})" for pat, _ in _CANON)

#: Lookup table for every word the regex above matches (e.g. "articles" -> "art").
_CANON_MAP: dict[str, str] = {}
for _pattern, _canon in _CANON:
    _base = _pattern.rstrip("s?") if _pattern.endswith("s?") else _pattern
    _CANON_MAP[_base] = _canon
    if _base != _canon:
        _CANON_MAP[_canon] = _canon
    _CANON_MAP[_base + "s"] = _canon

#: § runs become the canonical article word BEFORE punctuation stripping
#: (retrieval.md §4: "§1318" -> "art 1318").
_SEC_SIGN_RE = re.compile(r"§+\s*")
_PUNCT_RE = re.compile(r"[^\w\s]")
_WS_RE = re.compile(r"\s+")
_RANGE_SPLIT_RE = re.compile(r"\s*(?:-|–|—)\s*")
_DIGIT_RE = re.compile(r"^\d+$")
_TOKEN_RE = re.compile(r"[a-z0-9]+")

#: Printed labels may carry a letter or parenthesized suffix ("2000a",
#: "2000(a)"). Collapse "(a)" onto the digits so both spellings canonicalize
#: identically instead of silently conflating with bare "2000".
_PAREN_SUFFIX_RE = re.compile(r"(?<=\d)\s*\(\s*([a-z0-9]+)\s*\)", re.IGNORECASE)

#: A printed number: digits, optional letter suffix, optional parenthesized
#: suffix, optional numeric range tail.
_NUMBER_PATTERN = r"[0-9]+[a-z]?(?:\s*\(\s*[a-z0-9]+\s*\))?(?:\s*[-–—]\s*[0-9]+[a-z]?)?"

#: Canonical label spans in RAW text: word form + number (or a roman numeral).
#: Used by scoring/index to pull "art 1318" out of "Article 1318. Fraud"
#: without losing the range dash.
_LABEL_SPAN_RE = re.compile(
    rf"\b(?:{_CANON_ALT})\b\s*\.?\s*({_NUMBER_PATTERN}|[ivxlcdm]+)",
    re.IGNORECASE,
)

#: §-only labels: "§1318", "§§ 1318-1320".
_SEC_SIGN_LABEL_RE = re.compile(rf"§+\s*({_NUMBER_PATTERN})")

#: Canonical words that rank at the 0.5 (container) level in the ident component.
_CONTAINER_WORDS: frozenset[str] = frozenset({"ch", "subch", "title", "book"})

#: Canonical word a bare section sign becomes in the label-form string. This
#: form is display/dedup only; extract_labels emits BOTH families below.
_SECTION_SIGN_WORD = "art"


def normalize_label(s: str) -> str:
    """Canonical label form: lowercase, punctuation stripped, words mapped.

    "Art. 1318" / "Article 1318" / "§1318" -> "art 1318";
    "TITLE IV" -> "title iv"; "Subchapter V" -> "subch v";
    digits preserved (retrieval.md §4). Note: the label form keeps the §
    sign mapped to "art" for display/dedup; extract_labels dual-emits.
    """
    if not s:
        return ""
    text = _PAREN_SUFFIX_RE.sub(r"\1", s)
    text = _SEC_SIGN_RE.sub(f" {_SECTION_SIGN_WORD} ", text)
    text = _PUNCT_RE.sub(" ", text)
    text = text.lower()
    words = [w for w in _WS_RE.split(text) if w]
    return " ".join(_CANON_MAP.get(w, w) for w in words)


def expand_range(s: str) -> list[str]:
    """Expand a numeric token range: "1318-1320" -> ["1318","1319","1320"].

    En dashes (–) and em dashes (—) are also range separators; a plain token
    "1318" -> ["1318"]. Non-numeric tokens pass through unexpanded.
    """
    stripped = s.strip()
    if not stripped:
        return []
    parts = [p.strip() for p in _RANGE_SPLIT_RE.split(stripped)]
    if len(parts) != 2 or not all(_DIGIT_RE.fullmatch(p) for p in parts):
        return [stripped]
    lo, hi = int(parts[0]), int(parts[1])
    if hi < lo or hi - lo > 1000:
        return [stripped]
    return [str(n) for n in range(lo, hi + 1)]


def _canon_number(raw: str) -> str:
    """Compact a printed number: drop whitespace and fold "(a)" onto digits."""
    compact = re.sub(r"\s+", "", raw.lower())
    return _PAREN_SUFFIX_RE.sub(r"\1", compact)


def extract_labels(s: str) -> list[tuple[str, float]]:
    """Canonical labels inside raw text, each with its ident level.

    Returns ``(label, level)`` pairs where level is 1.0 for article/section
    labels and 0.5 for chapter/subchapter/title/book (container) labels.
    A bare § sign emits BOTH label families ("§ 101" -> "art 101" and
    "sec 101") so §-printed and word-printed provisions resolve identically
    (retrieval.md §4). Numeric ranges
    yield the bare label ("art 1318-1320") plus every expanded label
    ("art 1318", "art 1319", "art 1320"). Letter/parenthesized suffixes are
    preserved ("art 2000a"). Duplicates removed, order stable.
    """
    found: list[tuple[str, float]] = []
    for m in _LABEL_SPAN_RE.finditer(s):
        canon = _CANON_MAP[m.group(0).split()[0].strip(".").lower()]
        number = _canon_number(m.group(1))
        level = 0.5 if canon in _CONTAINER_WORDS else 1.0
        found.append((f"{canon} {number}", level))
        for piece in expand_range(number):
            found.append((f"{canon} {piece}", level))
    for m in _SEC_SIGN_LABEL_RE.finditer(s):
        number = _canon_number(m.group(1))
        # A bare § sign bridges to BOTH section label families (retrieval.md
        # §4): §-printed and word-printed provisions ("§ 101" vs
        # "Section 101") must resolve to the same ident component.
        for word in ("art", "sec"):
            found.append((f"{word} {number}", 1.0))
            for piece in expand_range(number):
                found.append((f"{word} {piece}", 1.0))
    seen: set[str] = set()
    out: list[tuple[str, float]] = []
    for label, level in found:
        if label not in seen:
            seen.add(label)
            out.append((label, level))
    return out


def label_tokens(s: str) -> set[str]:
    """Raw words consumed by label spans — the tokens other components must NOT see.

    Disjoint inputs (retrieval.md §3): identifier tokens are removed from the
    lex and title inputs so a heading term fires in exactly one component.
    """
    consumed: set[str] = set()
    for m in _LABEL_SPAN_RE.finditer(s):
        consumed.update(_TOKEN_RE.findall(m.group(0).lower()))
    for m in _SEC_SIGN_LABEL_RE.finditer(s):
        consumed.update(_TOKEN_RE.findall(m.group(1).lower()))
    return consumed
