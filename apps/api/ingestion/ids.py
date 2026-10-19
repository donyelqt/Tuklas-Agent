"""Node id grammar — decision D1 (architecture.md §3, resolved 2026-10-19).

A node id is `node_{doc_slug}_{type}_{locator}`:
- doc_slug: documents.slug, lowercase [a-z0-9] + internal single hyphens, no underscores.
- type: nodes.type verbatim, lowercase, from NODE_TYPES.
- locator: printed statutory number canonicalized LOSSLESSLY (lowercase, keep
  [a-z0-9], collapse any other char run to one '-', trim); or u{k} for unnumbered
  headings; absent for the single document root. Page nodes: locator is the 1-based
  physical PDF page index.
- duplicates of (doc_slug, type, locator): first keeps the bare id, later ones append
  _d{k} (k>=2, document order). `_` never appears inside a locator.

Ids are a pure function of (doc_slug, parser output) — no RNG/rowid/counter/timestamp —
so re-ingesting the same PDF reproduces the id set exactly. Ids are opaque handles:
retrieval's normalizer runs on labels, never ids.
"""

from __future__ import annotations

import re

#: nodes.type vocabulary (D1). Ids embed the type token verbatim.
NODE_TYPES: frozenset[str] = frozenset(
    {
        "document",
        "book",
        "title",
        "part",
        "division",
        "chapter",
        "section",
        "article",
        "subsection",
        "page",
    }
)

_SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_LOCATOR_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def slugify(name: str) -> str:
    """A registry slug from a file name stem: D1 slug charset or empty.

    Lowercase, keep [a-z0-9], collapse every other character run to a single hyphen,
    trim leading/trailing hyphens. Empty result means the caller must 422.
    """
    lowered = name.lower()
    collapsed = re.sub(r"[^a-z0-9]+", "-", lowered)
    return collapsed.strip("-")


def canonical_locator(printed: str) -> str:
    """The printed statutory number as a lossless locator token (D1).

    Lowercase; keep [a-z0-9]; collapse every run of any other character to a single
    '-'; trim. Roman numerals stay roman; ranges stay unexpanded (both conversions are
    retrieval's label-matching rules, never id rules).
    """
    collapsed = re.sub(r"[^a-z0-9]+", "-", printed.lower())
    return collapsed.strip("-")


def node_id(doc_slug: str, node_type: str, locator: str | None) -> str:
    """Build one node id; raises ValueError on any grammar violation."""
    if node_type not in NODE_TYPES:
        raise ValueError(f"unknown node type: {node_type!r}")
    if not _SLUG_RE.fullmatch(doc_slug):
        raise ValueError(f"doc_slug violates D1 charset: {doc_slug!r}")
    if node_type == "document":
        if locator is not None:
            raise ValueError("the document root carries no locator")
        return f"node_{doc_slug}_document"
    if locator is None:
        raise ValueError(f"non-document node requires a locator: {node_type!r}")
    if not _LOCATOR_RE.fullmatch(locator):
        raise ValueError(f"locator violates D1 charset: {locator!r}")
    return f"node_{doc_slug}_{node_type}_{locator}"


def dedup_suffix(bare_id: str, occurrence: int) -> str:
    """The id for the occurrence-th node sharing (doc_slug, type, locator).

    occurrence is 1-based in document order; 1 keeps the bare id, k>=2 appends _d{k}.
    """
    if occurrence < 1:
        raise ValueError("occurrence is 1-based")
    if occurrence == 1:
        return bare_id
    return f"{bare_id}_d{occurrence}"
