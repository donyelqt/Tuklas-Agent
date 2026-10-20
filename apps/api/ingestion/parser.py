"""Structure parser (architecture.md §3, D1 ids, CORPUS.md caveats).

Pipeline: PDF → PyMuPDF pages → heading scan (regex + RapidFuzz near-miss) →
container-stack tree → ParsedNode list. Flat fallback when parse_confidence < 0.5.
Standalone: imports ids.py and errors.py only — never apps/api/api.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import fitz  # PyMuPDF
from apps.api.ingestion import errors, ids
from rapidfuzz.fuzz import ratio as fuzz_ratio

__all__ = ["ParsedNode", "ParseResult", "extract_pages", "parse_document", "APPARATUS_HEADINGS"]


# --------------------------------------------------------------------------- #
# US Code editorial apparatus (CORPUS.md): never tree nodes, never confidence.
# --------------------------------------------------------------------------- #
APPARATUS_HEADINGS: frozenset[str] = frozenset(
    {
        "editorial notes",
        "editorial notes; amendments",
        "statutory notes",
        "statutory notes and related subdivisions",
        "historical notes",
        "revision notes",
        "derivation",
        "amendments",
        "effective dates",
        "effective date of 2023 amendment",
        "table i",
        "table ii",
        "table iii",
    }
)

_APPARATUS_PREFIXES = ("editorial notes", "statutory notes", "historical notes", "table ")


def _is_apparatus(line: str) -> bool:
    lowered = line.strip().lower().rstrip(".:")
    if lowered in APPARATUS_HEADINGS:
        return True
    return any(lowered.startswith(p) for p in _APPARATUS_PREFIXES)


# --------------------------------------------------------------------------- #
# Heading families. rank: smaller = higher in the tree (container stack rank).
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class _Family:
    keyword: str
    rank: int
    node_type: str
    number_re: str  # capture group for the printed number, or "" if unnumbered-only


_FAMILIES: tuple[_Family, ...] = (
    _Family("book", 1, "book", r"([IVXLC]+)"),
    _Family("part", 2, "part", r"([IVXLC\d]+)"),
    _Family("division", 3, "division", r"([A-Z0-9IVXLC]+)"),
    _Family("title", 4, "title", r"([IVXLC\d]+)"),
    _Family("chapter", 5, "chapter", r"(\d+)"),
    _Family("subchapter", 6, "subsection", r"([IVXLC]+)"),
    _Family("section", 7, "section", r"(\d+[a-z]?)"),
    _Family("article", 8, "article", r"(\d+[a-z]?(?:\([a-z0-9]+\))?)"),
)

# Extra aliases accepted by the RapidFuzz near-miss path (fuzz_ratio >= 80).
_FUZZ_ALIASES: dict[str, tuple[str, ...]] = {
    "book": ("bk",),
    "part": ("pt",),
    "division": ("div",),
    "title": ("tit",),
    "chapter": ("chap", "ch", "chpt"),
    "subchapter": ("subchap", "sub-chapter"),
    "section": ("sec", "sect"),
    "article": ("art", "arts"),
}

# Numbered regexes, tried in order. Anchored at line start, case-insensitive.
#
# The number must be TERMINATED by a period or the end of the line. That
# terminator is what separates a heading from a sentence that merely names a
# provision: "Article 1318. There is no contract..." is a heading, and
# "the provisions of articles 226 to 228" is not. Without the terminator a
# body-text line becomes a heading, and the heading's title then carries a
# label that every node beneath it inherits. Measured on the Civil Code:
# without it, 75 headings came from the near-miss path, one of them a body
# sentence ("party regarding the solemnization of the marriage in a place
# other than those mentioned in article 57 ...") that became a 'part' node
# and made 402 real articles carry the label "art 57".
_NUMBERED_RES: tuple[re.Pattern[str], ...] = tuple(
    re.compile(
        rf"^{re.escape(f.keyword)}\.?\s+{f.number_re}\b(?=\.|\s*$)",
        re.IGNORECASE,
    )
    for f in _FAMILIES
)
_SECTION_SIGN_RE = re.compile(r"^§\s*(\d+[a-z]?(?:\([a-z0-9]+\))?)")
# Unnumbered family heading: optional qualifier + bare keyword ending the line.
_UNNUMBERED_RE = re.compile(
    r"^(?:[A-Za-z]+\s+)?(book|part|division|title|chapter|subchapter|section|article)\b\.?\s*$",
    re.IGNORECASE,
)
_GENERIC_NUMBER_RE = re.compile(r"([IVXLC\d]+(?:\([a-z0-9]+\))?)\b")
# Ceiling for the unnumbered/fuzz path only; numbered heading candidates are
# exempt because the printed number at line start is what identifies them and
# real extracted lines carry arbitrarily long title text on one line.
_MAX_HEADING_LEN = 200

#: A numbered heading's number must be followed by a terminator: a period, or
#: the end of the line. A comma or a word means the number is part of a
#: sentence, not a heading.
_NUMBER_TERMINATOR_RE = re.compile(r"^\s*(\.|\)|$)")

#: A MARGINAL NOTE: a label, a period, and then ONLY a parenthesised marker
#: - "article 765. (n)", "article 2168. (1782)". These are the PDF's side
#: notes pointing at a provision, not the provision itself.
#:
#: The Civil Code PDF prints them at the left margin, set in a line 47-62
#: points wide, and every one of them names a provision that is printed
#: elsewhere in full. Read as a heading, each one creates a SECOND node for a
#: provision that already exists, and the label stops identifying anything:
#: measured on the Civil Code, "art 765", "art 895", "art 1544", "art 1984"
#: and "art 2168" each resolved to two nodes, so the identifier precedence
#: could not identify any of them.
#:
#: The pattern is exact: label, period, one parenthesised marker, end of
#: line. Measured over the 2583 headings the Civil Code produces, it matches
#: exactly these 5 lines and nothing else.
_MARGINAL_NOTE_RE = re.compile(
    r"^(?:book|part|division|title|chapter|subchapter|section|subsection|article)"
    r"\.?\s+[0-9]+[a-z]?\s*\.\s*\([^)]{0,12}\)\s*\.?$",
    re.IGNORECASE,
)


def _fuzz_family(token: str) -> _Family | None:
    """Near-miss keyword match (e.g. 'CHAPTEER', 'Sec.') via fuzz ratio >= 80.

    Exact alias hits (keyword or abbreviation, e.g. 'Art.', 'Sec.') win first, so
    short tokens like 'art' are never fuzz-bumped onto a longer family keyword.
    """
    cleaned = token.strip(".,:").lower()
    if not cleaned:
        return None
    for family in _FAMILIES:
        if cleaned in (family.keyword, *_FUZZ_ALIASES[family.keyword]):
            return family
    best: _Family | None = None
    best_ratio = 0.0
    for family in _FAMILIES:
        for alias in (family.keyword, *_FUZZ_ALIASES[family.keyword]):
            r = fuzz_ratio(cleaned, alias)
            if r >= 80 and r > best_ratio:
                best, best_ratio = family, r
    return best


@dataclass(frozen=True)
class _Heading:
    page_idx: int  # 0-based
    line_idx: int
    family: _Family
    printed: str | None  # None => unnumbered
    title: str


def _match_heading(line: str, page_idx: int, line_idx: int) -> _Heading | None:
    stripped = line.strip()
    if not stripped:
        return None

    # A marginal note is not a heading, whatever it looks like. The check
    # comes first so no path below can promote one to a node.
    if _MARGINAL_NOTE_RE.match(stripped):
        return None

    # Numbered families (regex first — exact forms win over fuzz).
    for family, pattern in zip(_FAMILIES, _NUMBERED_RES, strict=False):
        m = pattern.match(stripped)
        if m:
            return _Heading(page_idx, line_idx, family, m.group(1), stripped)
    m = _SECTION_SIGN_RE.match(stripped)
    if m:
        section_family = next(f for f in _FAMILIES if f.keyword == "section")
        return _Heading(page_idx, line_idx, section_family, m.group(1), stripped)

    # Unnumbered heading words: keyword ends the line (e.g. "PRELIMINARY TITLE").
    if len(stripped) > _MAX_HEADING_LEN:
        return None
    m = _UNNUMBERED_RE.match(stripped)
    if m:
        keyword = m.group(1).lower()
        family = next(f for f in _FAMILIES if f.keyword == keyword)
        return _Heading(page_idx, line_idx, family, None, stripped)

    # RapidFuzz near-miss: first token ≈ a family keyword.
    # Gate applies here (not above) so a numbered candidate is never rejected
    # for length; the printed number identifies it no matter how long the title.
    if len(stripped) > _MAX_HEADING_LEN:
        return None
    tokens = stripped.split()
    fuzz_hit = _fuzz_family(tokens[0])
    if fuzz_hit is None:
        return None
    rest = stripped[len(tokens[0]):].strip()
    m = _GENERIC_NUMBER_RE.search(rest)
    if m:
        # The number must be TERMINATED the same way a numbered heading's
        # number is, and it must be the FIRST thing in the rest of the line.
        # "articles 74 and 75" is a cross-reference, not a heading: its
        # number is followed by a word. "Art. 5" and "SUBSECTION 1. Wills"
        # are headings: the number is followed by a period.
        if _GENERIC_NUMBER_RE.match(rest) and _NUMBER_TERMINATOR_RE.match(
            rest[m.end():]
        ):
            return _Heading(page_idx, line_idx, fuzz_hit, m.group(1), stripped)
        return None
    # Unnumbered near-miss only when the keyword ends the line ("PRELIMINARY TITEL").
    if rest and fuzz_ratio(rest.rstrip(".:"), fuzz_hit.keyword) >= 80:
        return _Heading(page_idx, line_idx, fuzz_hit, None, stripped)
    return None


# --------------------------------------------------------------------------- #
# PDF pages
# --------------------------------------------------------------------------- #
def extract_pages(pdf_path: str) -> list[str]:
    """PyMuPDF text per 1-based page (list index i = page i+1).

    Raises errors.ApiError PARSE_FAILED on encrypted or corrupt/unreadable PDFs.
    """
    try:
        doc = fitz.open(pdf_path)
    except Exception as exc:  # corrupt file, missing file, bad magic
        raise errors.parse_failed(f"cannot open PDF: {pdf_path}", reason=str(exc)) from exc
    with doc:
        if doc.needs_pass:
            raise errors.parse_failed(f"PDF is encrypted: {pdf_path}")
        try:
            return [doc.load_page(i).get_text() for i in range(doc.page_count)]
        except Exception as exc:
            raise errors.parse_failed(f"cannot extract PDF text: {pdf_path}", reason=str(exc)) from exc


# --------------------------------------------------------------------------- #
# Tree construction
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ParsedNode:
    node_id: str
    parent_id: str | None
    type: str
    title: str
    text: str
    page_start: int | None
    page_end: int | None
    depth: int
    position: int


@dataclass(frozen=True)
class ParseResult:
    nodes: list[ParsedNode]
    parse_confidence: float
    fallback: bool


@dataclass
class _StackEntry:
    node_id: str
    rank: int  # 0 = document root


def parse_document(pdf_path: str, doc_slug: str) -> ParseResult:
    """PDF -> pages -> heading scan -> tree (architecture.md §3 semantics)."""
    pages = extract_pages(pdf_path)
    page_count = len(pages)
    if page_count == 0:
        raise errors.parse_failed(f"PDF has no pages: {pdf_path}")

    # Page text kept as line lists so node text spans can be sliced per page.
    page_lines: list[list[str]] = [page.splitlines() for page in pages]

    headings: list[_Heading] = []
    pages_with_heading = 0
    for p_idx, lines in enumerate(page_lines):
        matched_on_page = False
        for l_idx, line in enumerate(lines):
            if _is_apparatus(line):
                continue
            heading = _match_heading(line, p_idx, l_idx)
            if heading is not None:
                headings.append(heading)
                matched_on_page = True
        if matched_on_page:
            pages_with_heading += 1

    parse_confidence = pages_with_heading / page_count

    root_id = ids.node_id(doc_slug, "document", None)
    if parse_confidence < 0.5:
        # Flat fallback: ONLY root + one page node per page (retrieval.md §6).
        nodes = [
            ParsedNode(
                node_id=root_id,
                parent_id=None,
                type="document",
                title=doc_slug,
                text="",
                page_start=None,
                page_end=None,
                depth=0,
                position=0,
            )
        ]
        for i, page_text in enumerate(pages, start=1):
            nodes.append(
                ParsedNode(
                    node_id=ids.node_id(doc_slug, "page", str(i)),
                    parent_id=root_id,
                    type="page",
                    title=f"Page {i}",
                    text=page_text,
                    page_start=i,
                    page_end=i,
                    depth=1,
                    position=i,
                )
            )
        return ParseResult(nodes=nodes, parse_confidence=parse_confidence, fallback=True)

    # Heading -> (start page, start line, end page, end line exclusive) runs.
    runs: list[tuple[_Heading, int, int, int, int]] = []  # (heading, ps, pl, pe, pl_end)
    for i, heading in enumerate(headings):
        if i + 1 < len(headings):
            nxt = headings[i + 1]
            if nxt.page_idx == heading.page_idx:
                end_page, end_line = heading.page_idx, nxt.line_idx
            else:
                end_page, end_line = nxt.page_idx, 0
        else:
            end_page, end_line = heading.page_idx, len(page_lines[heading.page_idx])
        runs.append((heading, heading.page_idx, heading.line_idx, end_page, end_line))

    def slice_text(start_page: int, start_line: int, end_page: int, end_line: int) -> str:
        if start_page == end_page:
            return "\n".join(page_lines[start_page][start_line:end_line])
        parts = ["\n".join(page_lines[start_page][start_line:])]
        for p in range(start_page + 1, end_page):
            parts.append("\n".join(page_lines[p]))
        parts.append("\n".join(page_lines[end_page][:end_line]))
        return "\n".join(parts)

    # Assign locators in document order: u{k} per (type) unnumbered ordinal, then
    # dedup by (type, locator) occurrence index.
    bare_locators: list[str] = []
    unnumbered_seen: dict[str, int] = {}
    for heading, *_ in runs:
        if heading.printed is None:
            k = unnumbered_seen.get(heading.family.node_type, 0) + 1
            unnumbered_seen[heading.family.node_type] = k
            bare_locators.append(f"u{k}")
        else:
            bare_locators.append(ids.canonical_locator(heading.printed))

    occurrence: dict[tuple[str, str], int] = {}
    final_ids: list[str] = []
    for (heading, *_), locator in zip(runs, bare_locators, strict=False):
        key = (heading.family.node_type, locator)
        n = occurrence.get(key, 0) + 1
        occurrence[key] = n
        final_ids.append(ids.dedup_suffix(ids.node_id(doc_slug, heading.family.node_type, locator), n))

    # Document-order node emission with a container stack by family rank.
    nodes = [
        ParsedNode(
            node_id=root_id,
            parent_id=None,
            type="document",
            title=doc_slug,
            text="",
            page_start=None,
            page_end=None,
            depth=0,
            position=0,
        )
    ]
    stack: list[_StackEntry] = [_StackEntry(root_id, 0)]
    sibling_counts: dict[str, int] = {root_id: -1}  # next position per parent
    for (heading, ps, pl, pe, pl_end), node_id_val in zip(runs, final_ids, strict=False):
        while stack[-1].rank >= heading.family.rank:
            stack.pop()
        parent_id = stack[-1].node_id
        position = sibling_counts.get(parent_id, -1) + 1
        sibling_counts[parent_id] = position
        nodes.append(
            ParsedNode(
                node_id=node_id_val,
                parent_id=parent_id,
                type=heading.family.node_type,
                title=heading.title,
                text=slice_text(ps, pl, pe, pl_end),
                page_start=ps + 1,  # 1-based physical pages
                page_end=pe + 1,
                depth=len(stack),
                position=position,
            )
        )
        stack.append(_StackEntry(node_id_val, heading.family.rank))
        # New container: reset its child position counter to 0.
        sibling_counts.setdefault(node_id_val, 0)

    return ParseResult(nodes=nodes, parse_confidence=parse_confidence, fallback=False)
