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
#:
#: "subsection" maps to "sec" because that is how the parser TYPES it: the
#: Civil Code prints "SUBSECTION 1. Wills in General", and the heading
#: matcher reads that as a SECTION node (its "subchapter" family expects a
#: roman numeral). A node typed `section` must own a "sec" label, or the
#: 28 subsections in the Civil Code would own nothing.
_CANON: tuple[tuple[str, str], ...] = (
    (r"articles?", "art"),
    (r"arts?", "art"),
    (r"sections?", "sec"),
    (r"secs?", "sec"),
    (r"subsections?", "sec"),
    (r"subsecs?", "sec"),
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

#: A COORDINATED list of numbers after a plural label word: "articles 1318,
#: 2124 and 26". The label span above captures only the FIRST number, so
#: without this the second and third provisions were invisible to the
#: identifier channel - and the planner then split the question so that each
#: part carried a bare number, which is not a label at all.
#:
#: The list is bounded: only numbers separated by a comma, "and", "or", or
#: "to" may join, and at most _MAX_COORDINATED of them. A bare number that
#: starts the list is the span's own number and is not repeated here.
_COORDINATED_RE = re.compile(
    r"\s*(?:,|and|or|to|&)?\s*"
    r"(?:\d+[a-z]?(?:\([a-z0-9]+\))?|[ivxlcdm]+)"
    r"(?:\s*(?:,|and|or|to|&)\s*"
    r"(?:\d+[a-z]?(?:\([a-z0-9]+\))?|[ivxlcdm]+))*",
    re.IGNORECASE,
)

#: The separator words, so a number extractor can skip them. Without this
#: the roman-numeral alternative matched the "d" inside "and", producing a
#: label "art d".
_COORDINATED_SEPARATORS = frozenset({"and", "or", "to"})

#: A number in a coordinated list: digits with an optional suffix, or a
#: roman numeral. The roman form must be a REAL numeral, not the tail of an
#: English word.
_COORDINATED_NUMBER_RE = re.compile(
    r"\d+[a-z]?(?:\([a-z0-9]+\))?|^[ivxlcdm]+$",
    re.IGNORECASE,
)

#: Ceiling on how many numbers one label word may coordinate. A list longer
#: than this is prose, not a citation.
_MAX_COORDINATED = 8

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


def _coordinated_numbers(s: str, after: int) -> list[str]:
    """The numbers in a coordinated list that follows a label word.

    "articles 1318, 2124 and 26" names three provisions. The label span
    captures only the first, so the rest are read here. The list must be a
    genuine list of numbers joined by separators: a comma, "and", "or" or
    "to", optionally with a trailing "and". A number that is not a real
    number is not a number - "and" must not be read as the roman numeral
    "d".

    Returns the numbers in order, each already canonicalized.
    """
    tail = s[after:]
    match = _COORDINATED_RE.match(tail)
    if match is None:
        return []
    out: list[str] = []
    for token in match.group(0).replace(",", " ").split():
        lowered = token.lower()
        if lowered in _COORDINATED_SEPARATORS or lowered == "&":
            continue
        if not _COORDINATED_NUMBER_RE.match(lowered):
            # A word that is not a number ends the list.
            break
        out.append(_canon_number(lowered))
        if len(out) >= _MAX_COORDINATED:
            break
    # The match starts AFTER the span's own number, so every number in it is
    # an ADDITIONAL one. (The span's number was captured by the label span
    # itself, and the list resumes with a separator.)
    return out


def _label_spans(s: str) -> list[tuple[str, float, int]]:
    """Every label span in raw text: (label, level, start offset).

    The span's start offset is what lets ``own_identity`` ask "is this
    label at the START of the text?", which is how an owned heading is told
    apart from a cited provision. The label grammar lives here and ONLY
    here, so ownership and extraction cannot drift apart.
    """
    found: list[tuple[str, float, int]] = []
    for m in _LABEL_SPAN_RE.finditer(s):
        canon = _CANON_MAP[m.group(0).split()[0].strip(".").lower()]
        number = _canon_number(m.group(1))
        level = 0.5 if canon in _CONTAINER_WORDS else 1.0
        found.append((f"{canon} {number}", level, m.start()))
        for piece in expand_range(number):
            found.append((f"{canon} {piece}", level, m.start()))
        # A coordinated list: "articles 1318, 2124 and 26" names three
        # provisions, and only the first was captured by the span above.
        # Each further number in the list is a label of the same family.
        for other in _coordinated_numbers(s, m.end()):
            found.append((f"{canon} {other}", level, m.start()))
            for piece in expand_range(other):
                found.append((f"{canon} {piece}", level, m.start()))
    for m in _SEC_SIGN_LABEL_RE.finditer(s):
        number = _canon_number(m.group(1))
        # A bare § sign bridges to BOTH section label families (retrieval.md
        # §4): §-printed and word-printed provisions ("§ 101" vs
        # "Section 101") must resolve to the same ident component.
        for word in ("art", "sec"):
            found.append((f"{word} {number}", 1.0, m.start()))
            for piece in expand_range(number):
                found.append((f"{word} {piece}", 1.0, m.start()))
    return found


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
    seen: set[str] = set()
    out: list[tuple[str, float]] = []
    for label, level, _start in _label_spans(s):
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


# ---------------------------------------------------------------------------
# Own identity (retrieval.md §4, identity tier)
# ---------------------------------------------------------------------------

#: The canonical label family each node TYPE may own. The node type is the
#: arbiter: only a node of the matching type may own the label, so an
#: "article" node cannot own "ch 2" and a "chapter" node cannot own
#: "art 1318".
_TYPE_OWNS: dict[str, frozenset[str]] = {
    "article": frozenset({"art"}),
    "section": frozenset({"sec"}),
    "subsection": frozenset({"subch"}),
    "chapter": frozenset({"ch"}),
    "title": frozenset({"title"}),
    "book": frozenset({"book"}),
    "part": frozenset({"part"}),
    "division": frozenset({"division"}),
}


def own_identity(node_type: str, title: str) -> tuple[str, float] | None:
    """The identity label a node OWNS, from its own heading, or None.

    A node's identity is the label in its own heading — "Article 1318. There is
    no contract..." owns "art 1318". Two rules make this precise:

    1. The label must be the FIRST thing in the title. A node whose title
       merely MENTIONS a provision ("the provisions of articles 226 to 228
       and 235 to 236") does not own it; it only references it. Without this
       rule a node inherits every provision it cites, and one label then names
       hundreds of nodes.
    2. The node type must match the label family. A "chapter" node cannot own
       "art 1318"; a "part" node cannot own "ch 2".

    The label grammar is NOT restated here. It is read from the same
    ``_label_spans`` extractor that ``extract_labels`` uses, so the two can
    never disagree about what a label is. That matters: a second regex
    accepted only the full words ("Article") and missed the forms the
    contract requires - "Art. 1318", "Sec. 3", "Article 2000(a)" and the
    bare "§ 101" that U.S. Code Title 11 prints - so the ident channel was
    DEAD on that corpus: every section query returned ident 0.0 for all 986
    nodes.

    Returns ``(label, level)`` where level is 1.0 for an article/section
    (exact) label and 0.5 for a container (chapter/title/book) label, matching
    the ident levels of ``extract_labels``. Returns None when the title carries
    no own heading.
    """
    allowed = _TYPE_OWNS.get(node_type)
    if allowed is None:
        return None
    stripped = title.lstrip()
    lead = len(title) - len(stripped)
    for label, level, start in _label_spans(title):
        if start != lead:
            # Only the FIRST label in the title can be the node's own.
            break
        family = label.split(" ", 1)[0]
        if family in allowed:
            return label, level
        # A "§ 101" heading emits BOTH families ("art 101" and "sec 101",
        # retrieval.md §4). A section node owns the "sec" twin; the "art" one
        # is the same provision under its other name. So when the type's
        # family is "sec" and the span is the "art" twin of a § heading, keep
        # looking for the "sec" twin of the SAME number.
        if family == "art" and "sec" in allowed:
            number = label.split(" ", 1)[1]
            twin = f"sec {number}"
            for other, other_level, other_start in _label_spans(title):
                if other == twin and other_start == lead:
                    return twin, other_level
            return None
        return None
    return None
