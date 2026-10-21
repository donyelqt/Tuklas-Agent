"""Context builder — EvidenceSet -> PromptContext (retrieval.md §5, Day-2 plan).

Budget assembly rules (binding):

- ORDER: score desc; ties broken shallower depth first, then position asc.
- NEVER-DROP-GATE (WHY): the gate lists which nodes PROVE each sub-question.
  Dropping a gate node silently invalidates the sufficiency gate the agent
  already passed — the prompt would look complete while a sub-question lost its
  only support. Gate nodes are therefore shortened only by last-resort
  proportional truncation, never by overflow eviction.
- Overflow: drop lowest-score non-gate parts first until the payload fits.
- If the gate parts ALONE exceed the budget, truncate each gate part's text
  proportionally to fit and set ``context_truncated=True``.
- ``total_chars`` = sum of emitted part text lengths, always <= budget_chars.

``kept`` items are plain dicts; build_context is dict-only by contract (see
the boundary note in tools.py ``select_evidence``), normalizing to the emitted
part shape ``{'node_id','text','score'}``.
"""

from __future__ import annotations

from typing import Any

#: Default prompt budget in characters (retrieval.md §5: 12k).
DEFAULT_BUDGET_CHARS = 12000


def _item_field(item: Any, key: str, default: Any = None) -> Any:
    return item.get(key, default)


def _item_id(item: Any) -> str:
    return str(_item_field(item, "node_id", ""))


def _item_text(item: Any) -> str:
    """Emitted text for one item: body text if present, else the snippet."""
    for key in ("text", "snippet"):
        value = _item_field(item, key)
        if isinstance(value, str) and value:
            return value
    return ""


def _item_score(item: Any) -> float:
    return float(_item_field(item, "score", 0.0))


def _item_depth(item: Any) -> int:
    return int(_item_field(item, "depth", 0) or 0)


def _item_position(item: Any) -> int:
    return int(_item_field(item, "position", 0) or 0)


def _is_item(obj: Any) -> bool:
    """A usable evidence item: a dict carrying node_id."""
    return isinstance(obj, dict) and "node_id" in obj


def _gate_node_ids(evidence: dict[str, Any]) -> set[str]:
    """Every node id the gate protects.

    Accepts the select_evidence gate shape ``{sub_question: [node_ids...]}``
    (values are node-id collections) plus the defensive ``{'node_ids': [...]}``
    payload; scalar gate metadata keys are ignored.
    """
    gate = evidence.get("gate") or {}
    ids: set[str] = set()
    if isinstance(gate, dict):
        for key, value in gate.items():
            if isinstance(value, list | tuple | set | frozenset):
                ids.update(str(v) for v in value)
            elif isinstance(value, str) and key not in {"threshold"}:
                # Tolerate a bare id value.
                ids.add(value)
    elif isinstance(gate, list | tuple | set | frozenset):
        ids.update(str(v) for v in gate)
    return ids


def _is_identified(item: Any) -> bool:
    """True when the item is the node the query identified.

    The flag travels from the retrieval ranking (scoring.ScoredNode) through
    the evidence set. It is a fact about the query, not a score, so it does
    not change any part's score - it only decides which part is emitted
    FIRST, so the provision the user named is the one the model reads first.
    """
    return bool(_item_field(item, "identified", False))


def _sort_key(item: Any) -> tuple[int, float, int, int, str]:
    """identified first; then score desc; ties: shallower depth, position, id."""
    return (
        0 if _is_identified(item) else 1,
        -_item_score(item),
        _item_depth(item),
        _item_position(item),
        _item_id(item),
    )


def _evict_key(item: Any) -> tuple[float, int, int, str]:
    """Eviction order: lowest score first; ties deepest, latest, then id."""
    return (_item_score(item), -_item_depth(item), -_item_position(item), _item_id(item))


def _protected_ids(evidence: dict[str, Any], items: list[Any]) -> set[str]:
    """Node ids that must not be evicted for space: the gate set, plus the
    node the query identifies.

    The identified node is the provision the user named, and its score is the
    LOWEST by construction - that is exactly why the identifier precedence
    exists (§3 sums five bounded components, so `ident` 1.0 loses to
    `lex 1.0 + title 0.1 + hier 1.0 = 2.1`). Evicting by lowest score
    therefore drops the named provision FIRST, silently undoing the fix at
    exactly the moment the prompt is too large to hold everything. It is
    protected alongside the gate set for the same reason the gate set is: the
    answer must be able to speak about it.
    """
    protected = _gate_node_ids(evidence)
    for item in items:
        if _is_identified(item):
            protected.add(_item_id(item))
    return protected


def _part(item: Any, text: str) -> dict[str, Any]:
    """One PromptContext part: exactly the three contract keys."""
    return {"node_id": _item_id(item), "text": text, "score": _item_score(item)}


def build_context(
    evidence: dict[str, Any], budget_chars: int = DEFAULT_BUDGET_CHARS
) -> dict[str, Any]:
    """Assemble the PromptContext payload from a select_evidence result.

    Returns ``{'parts': [{'node_id','text','score'}...], 'total_chars': int,
    'context_truncated': bool}`` with ``total_chars <= budget_chars``.
    """
    if budget_chars < 0:
        raise ValueError(f"budget_chars must be >= 0, got {budget_chars}")

    items = sorted(
        (item for item in evidence.get("kept", []) if _is_item(item)), key=_sort_key
    )
    protected_ids = _protected_ids(evidence, items)
    texts = {id(item): _item_text(item) for item in items}
    total = sum(len(texts[id(item)]) for item in items)

    if total <= budget_chars:
        parts = [_part(item, texts[id(item)]) for item in items]
        return {"parts": parts, "total_chars": total, "context_truncated": False}

    gate_items = [item for item in items if _item_id(item) in protected_ids]
    gate_total = sum(len(texts[id(item)]) for item in gate_items)

    if gate_total > budget_chars:
        # The protected set alone overflows: truncate each protected part
        # PROPORTIONALLY to its text length (largest-remainder), so longer
        # texts give up more characters; free parts emit nothing.
        parts = []
        if budget_chars < len(gate_items):
            # Not enough budget for 1 char each; floor at 1 char per part.
            shares = [1] * len(gate_items)
        else:
            lengths = [len(texts[id(item)]) for item in gate_items]
            total_len = sum(lengths)
            shares = [budget_chars * ln // total_len for ln in lengths]
            # Distribute leftover chars by largest remainder among parts whose
            # share is below their full length, +1 char each until exhausted.
            leftover = budget_chars - sum(shares)
            order = sorted(
                range(len(gate_items)),
                key=lambda i: (-(budget_chars * lengths[i] % total_len), i),
            )
            for i in order:
                if leftover <= 0:
                    break
                if shares[i] < lengths[i]:
                    shares[i] += 1
                    leftover -= 1
        parts = [
            _part(item, texts[id(item)][:share])
            for item, share in zip(gate_items, shares, strict=False)
        ]
        emitted = sum(len(part["text"]) for part in parts)
        return {"parts": parts, "total_chars": emitted, "context_truncated": True}

    # Overflow eviction: drop lowest-score NON-PROTECTED parts first until it
    # fits. ``items`` is already in emission order; removing victims preserves
    # the order of the survivors, so output order stays score-desc/depth/position.
    selected = list(items)
    while total > budget_chars:
        evictable = [
            item for item in selected if _item_id(item) not in protected_ids
        ]
        if not evictable:
            break
        victim = min(evictable, key=_evict_key)
        selected.remove(victim)
        total -= len(texts[id(victim)])

    parts = [_part(item, texts[id(item)]) for item in selected]
    emitted = sum(len(part["text"]) for part in parts)
    return {"parts": parts, "total_chars": emitted, "context_truncated": True}
