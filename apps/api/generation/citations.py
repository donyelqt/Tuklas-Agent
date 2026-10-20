"""Citation assembly (architecture.md sec 6, api.md sec 3).

``citations[] = [{n, node_id, label, page_start, page_end}]`` — one entry per
rendered evidence part, numbered from 1 in the order the evidence was
rendered.

The page span comes from the KEPT EVIDENCE. The PromptContext parts carry
only ``{node_id, text, score}`` (retrieval.md sec 5), so the page span of a
citation is looked up in the evidence the Context Builder was given.

A node that is not in the evidence set is NOT invented: no citation is
emitted for it, and no page span is guessed. A part can only lack its
evidence entry when the caller passed a PromptContext that was not built from
the evidence it handed over — in the normal flow every part comes from the
kept set, so this branch is a guard against a fabricated citation rather
than a routine path.
"""

from __future__ import annotations

from typing import Any


def _kept_by_id(evidence: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The kept evidence items, keyed by node id.

    ``evidence`` is the select_evidence payload (``{'kept': [...]}``). A
    payload that is already keyed by node id is accepted as well, so the
    lookup does not depend on which of the two shapes the caller holds.
    Only items that carry a node id are indexed; anything else is ignored.
    """
    if not isinstance(evidence, dict):
        return {}
    kept = evidence.get("kept")
    indexed: dict[str, dict[str, Any]] = {}
    if isinstance(kept, list):
        for item in kept:
            if not isinstance(item, dict):
                continue
            node_id = str(item.get("node_id", ""))
            if node_id:
                indexed[node_id] = item
        return indexed
    for node_id, item in evidence.items():
        if isinstance(item, dict):
            indexed[str(node_id)] = item
    return indexed


def _label(item: dict[str, Any], node_id: str) -> str:
    """The citation label: the evidence's title, or the node id when it has none."""
    for key in ("title", "label"):
        value = item.get(key)
        if isinstance(value, str) and value:
            return value
    return node_id


def _page(item: dict[str, Any], key: str) -> int | None:
    """A page number from the evidence, or None when the evidence has none."""
    value = item.get(key)
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def assemble_citations(
    parts: list[dict[str, Any]], evidence: dict[str, Any]
) -> list[dict[str, Any]]:
    """Assemble the citation list for the rendered evidence.

    ``parts`` are the PromptContext parts in the order they were rendered;
    ``evidence`` is the kept evidence the Context Builder was given. The
    result is ordered like ``parts``, numbered from 1.
    """
    by_id = _kept_by_id(evidence)
    citations: list[dict[str, Any]] = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        node_id = str(part.get("node_id", ""))
        if not node_id:
            continue
        item = by_id.get(node_id)
        if item is None:
            # Not in the evidence set: no citation is invented for it.
            continue
        citations.append(
            {
                "n": len(citations) + 1,
                "node_id": node_id,
                "label": _label(item, node_id),
                "page_start": _page(item, "page_start"),
                "page_end": _page(item, "page_end"),
            }
        )
    return citations


__all__ = ["assemble_citations"]
