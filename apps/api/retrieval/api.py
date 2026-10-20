"""Agent-facing tree tools (retrieval.md §5).

These functions are the agent's ONLY tree access — no direct SQL reads
elsewhere. Every function takes ``db_path`` as its last parameter (defaulting
to the ingestion DB_PATH) and resolves documents by id OR slug, matching the
ingestion service's resolution rule.

All returned dicts are plain JSON-safe payloads; node rows keep the §8 column
names. Text fields are truncated per tool (§5 binding limits).
"""

from __future__ import annotations

import functools
import inspect
import json
import logging
import sqlite3
import time
from collections import deque
from collections.abc import Callable
from typing import Any, ParamSpec, TypeVar

from apps.api.ingestion import db, errors
from apps.api.ingestion.service import DB_PATH as _INGEST_DB_PATH
from apps.api.retrieval.scoring import ScoredNode, score_nodes

_LOG = logging.getLogger(__name__)
_P = ParamSpec("_P")
_R = TypeVar("_R")

#: Re-exported default path so tools default to the same store as ingestion.
DB_PATH: str = _INGEST_DB_PATH

#: §5 binding limits.
_NODE_TEXT_MAX = 8000
_CHILD_SUMMARY_TEXT_MAX = 200
_CHILDREN_LIMIT_MAX = 50
_EXPAND_RADIUS_MAX = 2
_EXPAND_NODE_MAX = 5
_EXPAND_NODE_TEXT_MAX = 2000

#: select_evidence gates (retrieval.md §5, architecture.md §4).
_EVIDENCE_KEEP_TOP = 8
_EVIDENCE_MIN_TEXT_CHARS = 50
_SUPPORT_THRESHOLD = 0.15

_NODE_COLUMNS = (
    "id, document_id, parent_id, type, title, text, page_start, page_end, depth, position"
)


def _result_ids(result: Any) -> list[str]:
    """Node ids a tool returned, for the §5 structured tool log."""
    if result is None:
        return []
    if isinstance(result, dict):
        for key in ("node_id", "reference_id"):
            value = result.get(key)
            if value is not None:
                return [str(value)]
        node = result.get("node")
        if isinstance(node, dict) and node.get("node_id") is not None:
            return [str(node["node_id"])]
        kept = result.get("kept")
        if isinstance(kept, list):
            return [
                str(item["node_id"])
                for item in kept
                if isinstance(item, dict) and item.get("node_id") is not None
            ]
        return []
    if isinstance(result, list | tuple):
        ids: list[str] = []
        for item in result:
            if isinstance(item, ScoredNode):
                ids.append(item.node_id)
            elif isinstance(item, dict) and item.get("node_id") is not None:
                ids.append(str(item["node_id"]))
        return ids
    return []


def _logged(tool_name: str) -> Callable[[Callable[_P, _R]], Callable[_P, _R]]:
    """Decorator emitting the binding ``(tool, args, latency_ms, result_ids)`` log."""

    def decorate(fn: Callable[_P, _R]) -> Callable[_P, _R]:
        signature = inspect.signature(fn)

        @functools.wraps(fn)
        def wrapper(*args: _P.args, **kwargs: _P.kwargs) -> _R:
            started = time.perf_counter()
            try:
                bound = signature.bind(*args, **kwargs)
                call_args: dict[str, Any] = {
                    name: value for name, value in bound.arguments.items()
                }
            except TypeError:
                call_args = {"args": repr(args), "kwargs": repr(kwargs)}
            try:
                result = fn(*args, **kwargs)
            except Exception:
                _LOG.info(
                    json.dumps(
                        {
                            "tool": tool_name,
                            "args": call_args,
                            "latency_ms": round((time.perf_counter() - started) * 1000, 3),
                            "result_ids": [],
                            "error": True,
                        },
                        default=str,
                        sort_keys=True,
                    )
                )
                raise
            _LOG.info(
                json.dumps(
                    {
                        "tool": tool_name,
                        "args": call_args,
                        "latency_ms": round((time.perf_counter() - started) * 1000, 3),
                        "result_ids": _result_ids(result),
                    },
                    default=str,
                    sort_keys=True,
                )
            )
            return result

        return wrapper

    return decorate


def _node_row_to_dict(row: Any, *, text_limit: int | None = None) -> dict[str, Any]:
    """One nodes row as a dict; optional text truncation."""
    text = str(row["text"])
    if text_limit is not None and len(text) > text_limit:
        text = text[:text_limit]
    return {
        "node_id": str(row["id"]),
        "document_id": str(row["document_id"]),
        "parent_id": row["parent_id"],
        "type": str(row["type"]),
        "title": str(row["title"]),
        "text": text,
        "page_start": row["page_start"],
        "page_end": row["page_end"],
        "depth": int(row["depth"]),
        "position": int(row["position"]),
    }


def _fetch_node(conn: sqlite3.Connection, node_id: str) -> Any:
    """One node row or errors.not_found (404) when absent."""
    row = conn.execute(
        f"SELECT {_NODE_COLUMNS} FROM nodes WHERE id = ?", (node_id,)
    ).fetchone()
    if row is None:
        raise errors.not_found("unknown node", node_id=node_id)
    return row


def _hierarchy_path(conn: sqlite3.Connection, node: Any) -> list[str]:
    """Ancestor titles root-first, excluding the node itself."""
    titles: list[str] = []
    current: str | None = str(node["parent_id"])
    while current is not None:
        row = conn.execute(
            "SELECT title, parent_id FROM nodes WHERE id = ?", (current,)
        ).fetchone()
        if row is None:
            break
        titles.append(str(row["title"]))
        current = row["parent_id"]
    titles.reverse()
    return titles


@_logged("search_nodes")
def search_nodes(
    query: str,
    document_id: str,
    top_k: int = 10,
    db_path: str = DB_PATH,
) -> list[ScoredNode]:
    """Scored node hits for one query; the §5 binding top_k clamp applies.

    ``document_id`` accepts a document id or a slug. Hard max 20, minimum 1;
    ValueError outside that range.
    """
    if top_k > 20 or top_k < 1:
        raise ValueError(f"top_k must be in [1, 20], got {top_k}")
    hits = score_nodes(db_path, document_id, query)
    return hits[:top_k]


@_logged("inspect_node")
def inspect_node(node_id: str, db_path: str = DB_PATH) -> dict[str, Any]:
    """Full node row with text truncated to 8000 chars (§5)."""
    with db.connect(db_path) as conn:
        row = _fetch_node(conn, node_id)
        return _node_row_to_dict(row, text_limit=_NODE_TEXT_MAX)


@_logged("list_children")
def list_children(
    node_id: str, limit: int = 50, db_path: str = DB_PATH
) -> list[dict[str, Any]]:
    """Flat child summaries (title + text head), max 50 rows, 200-char text.

    Flat-fallback safe (§5): children of a page node are the page's blocks, so
    this never requires a hierarchical tree to exist.
    """
    if limit > _CHILDREN_LIMIT_MAX:
        raise ValueError(f"limit must be <= {_CHILDREN_LIMIT_MAX}, got {limit}")
    with db.connect(db_path) as conn:
        _fetch_node(conn, node_id)
        rows = conn.execute(
            f"SELECT {_NODE_COLUMNS} FROM nodes WHERE parent_id = ?"
            " ORDER BY position LIMIT ?",
            (node_id, limit),
        ).fetchall()
        return [_node_row_to_dict(row, text_limit=_CHILD_SUMMARY_TEXT_MAX) for row in rows]


@_logged("get_parent")
def get_parent(node_id: str, db_path: str = DB_PATH) -> dict[str, Any] | None:
    """The node's parent summary, or None at the document root."""
    with db.connect(db_path) as conn:
        node = _fetch_node(conn, node_id)
        if node["parent_id"] is None:
            return None
        row = conn.execute(
            f"SELECT {_NODE_COLUMNS} FROM nodes WHERE id = ?", (node["parent_id"],)
        ).fetchone()
        return _node_row_to_dict(row, text_limit=_CHILD_SUMMARY_TEXT_MAX)


@_logged("expand_context")
def expand_context(
    node_id: str, radius: int = 1, db_path: str = DB_PATH
) -> list[dict[str, Any]]:
    """BFS neighborhood (ancestors + descendants + nearest siblings), radius <= 2, <= 5 nodes.

    Ancestors: up to ``radius`` steps up the parent chain, root included only
    when it falls within that budget (not unconditionally). Siblings: the
    ``radius`` nearest siblings of the focal node by position distance —
    positions immediately below and above the focal position win, never the
    lowest positions in the sibling set; ties broken by position (deterministic).
    Descendants: BFS down to ``radius`` levels. The focal node is always
    included when the neighborhood is non-empty. Each node's text <= 2000 chars.
    """
    if radius > _EXPAND_RADIUS_MAX or radius < 0:
        raise ValueError(f"radius must be in [0, {_EXPAND_RADIUS_MAX}], got {radius}")
    with db.connect(db_path) as conn:
        focal = _fetch_node(conn, node_id)
        parent_id = focal["parent_id"]

        # Parent chain (ancestors), root-first.
        ancestors: list[Any] = []
        current: str | None = parent_id
        while current is not None and len(ancestors) < radius:
            row = conn.execute(
                f"SELECT {_NODE_COLUMNS} FROM nodes WHERE id = ?", (current,)
            ).fetchone()
            if row is None:
                break
            ancestors.append(row)
            current = row["parent_id"]

        # Siblings of the focal node (same parent, excluding focal), tree order.
        siblings: list[Any] = []
        if parent_id is not None:
            rows = conn.execute(
                f"SELECT {_NODE_COLUMNS} FROM nodes WHERE parent_id = ? AND id != ?"
                " ORDER BY position",
                (parent_id, node_id),
            ).fetchall()
            # Nearest by position distance to the focal node, not lowest
            # positions: straddle the focal position (below, then above).
            focal_pos = int(focal["position"])
            ranked = sorted(
                rows, key=lambda r: (abs(int(r["position"]) - focal_pos), int(r["position"]))
            )
            siblings = list(ranked[:radius])

        # Descendants BFS (children first), capped so the total stays <= 5.
        budget = _EXPAND_NODE_MAX - 1 - len(ancestors) - len(siblings)
        descendants: list[Any] = []
        if budget > 0:
            queue: deque[str] = deque([node_id])
            depth_map = {node_id: 0}
            while queue and len(descendants) < budget:
                nid = queue.popleft()
                if depth_map[nid] >= radius:
                    continue
                for child in conn.execute(
                    f"SELECT {_NODE_COLUMNS} FROM nodes WHERE parent_id = ?"
                    " ORDER BY position",
                    (nid,),
                ).fetchall():
                    cid = str(child["id"])
                    if cid in depth_map:
                        continue
                    depth_map[cid] = depth_map[nid] + 1
                    descendants.append(child)
                    queue.append(cid)
                    if len(descendants) >= budget:
                        break

        selected = list(ancestors) + siblings + descendants
        # Deterministic order: depth asc, position asc; focal node first.
        selected.sort(key=lambda r: (int(r["depth"]), int(r["position"])))
        out = [_node_row_to_dict(row, text_limit=_EXPAND_NODE_TEXT_MAX) for row in selected]
        focal_dict = _node_row_to_dict(focal, text_limit=_EXPAND_NODE_TEXT_MAX)
        return [focal_dict] + [d for d in out if d["node_id"] != focal_dict["node_id"]][
            : _EXPAND_NODE_MAX - 1
        ]


@_logged("follow_reference")
def follow_reference(reference_id: str, db_path: str = DB_PATH) -> dict[str, Any]:
    """Resolve one references_ edge; NULL target -> 'unresolved', never invented."""
    with db.connect(db_path) as conn:
        edge = conn.execute(
            "SELECT source_node_id, target_node_id, reference_text"
            " FROM references_ WHERE id = ?",
            (reference_id,),
        ).fetchone()
        if edge is None:
            return {
                "status": "error",
                "node": None,
                "reference_id": reference_id,
                "detail": "unknown reference id",
            }
        if edge["target_node_id"] is None:
            return {
                "status": "unresolved",
                "node": None,
                "reference_id": reference_id,
                "reference_text": str(edge["reference_text"]),
            }
        row = conn.execute(
            f"SELECT {_NODE_COLUMNS} FROM nodes WHERE id = ?", (edge["target_node_id"],)
        ).fetchone()
        if row is None:
            # Dangling resolved edge: schema FK should prevent this; fail closed.
            return {
                "status": "error",
                "node": None,
                "reference_id": reference_id,
                "detail": "resolved target missing from tree",
            }
        return {
            "status": "resolved",
            "node": _node_row_to_dict(row, text_limit=_NODE_TEXT_MAX),
            "reference_id": reference_id,
            "reference_text": str(edge["reference_text"]),
        }


@_logged("get_source")
def get_source(node_id: str, db_path: str = DB_PATH) -> dict[str, Any]:
    """Node text + pages + hierarchy path (root-first ancestor titles).

    Text is truncated to 8000 chars (architecture.md §4 cap for get_source;
    the real Civil Code corpus max node text is 8655 chars, so this bites).
    """
    with db.connect(db_path) as conn:
        node = _fetch_node(conn, node_id)
        return {
            "node_id": str(node["id"]),
            "text": _node_row_to_dict(node, text_limit=_NODE_TEXT_MAX)["text"],
            "page_start": node["page_start"],
            "page_end": node["page_end"],
            "hierarchy_path": _hierarchy_path(conn, node),
        }


@_logged("select_evidence")
def select_evidence(
    hits: list[ScoredNode],
    sub_questions: list[str],
    db_path: str = DB_PATH,
) -> dict[str, Any]:
    """retrieval-owned evidence gate (§5 + architecture.md §4, threshold 0.15).

    Frozen boundary contract: select_evidence is the ONLY consumer of
    ScoredNode; everything downstream (build_context, logs) sees the dict
    payload ``{'kept': [dicts], 'gate': {...}}``.

    Pipeline: dedup by node_id -> drop len(text) < 50 -> drop page-span-less
    (page_start IS NULL) -> rank by score desc -> top 8 -> support check at
    0.15 (a node below 0.15 may sit in the top-8 window but never supports a
    sub-question). A node supports sub-question S iff
    score >= 0.15 AND (components['lex'] > 0 OR the node id appears in
    ident_index for a label extracted from S). Returns
    ``{'kept', 'insufficient', 'gate'}`` with the canonical gate payload
    ``{'threshold', 'node_ids', 'per_question'}``. Zero sub-questions is an
    invalid plan and fails closed.
    """
    from apps.api.retrieval import normalizer

    # 1. Dedup by node_id (first occurrence wins).
    seen: set[str] = set()
    deduped: list[ScoredNode] = []
    for hit in hits:
        if hit.node_id not in seen:
            seen.add(hit.node_id)
            deduped.append(hit)

    kept: list[dict[str, Any]] = []
    with db.connect(db_path) as conn:
        document_id: str | None = None
        # 2. Drop <50 chars of text; 3. drop page-span-less (page_start IS NULL);
        # 4. rank below; the 0.15 threshold applies only to the support gate.
        for hit in deduped:
            row = conn.execute(
                "SELECT document_id, text, page_start, page_end FROM nodes WHERE id = ?",
                (hit.node_id,),
            ).fetchone()
            if row is None:
                continue
            if document_id is None:
                document_id = str(row["document_id"])
            text = str(row["text"])
            if len(text) < _EVIDENCE_MIN_TEXT_CHARS or row["page_start"] is None:
                continue
            kept.append(
                {
                    "node_id": hit.node_id,
                    "title": hit.title,
                    "text": text,
                    "snippet": hit.snippet,
                    "score": hit.score,
                    "components": dict(hit.components),
                    "page_start": row["page_start"],
                    "page_end": row["page_end"],
                    "depth": hit.depth,
                    "position": hit.position,
                }
            )

        kept.sort(
            key=lambda item: (
                -float(item["score"]),
                int(item["depth"]),
                int(item["position"]),
                str(item["node_id"]),
            )
        )
        kept = kept[:_EVIDENCE_KEEP_TOP]

        # 5. Per-sub-question support check.
        per_question: dict[str, list[str]] = {}
        gate_ids: list[str] = []
        for sub in sub_questions:
            labels = [label for label, _level in normalizer.extract_labels(sub)]
            label_nodes: set[str] = set()
            if labels and document_id is not None:
                placeholders = ",".join("?" * len(labels))
                rows = conn.execute(
                    "SELECT node_id FROM ident_index WHERE document_id = ?"
                    f" AND normalized_label IN ({placeholders})",
                    (document_id, *labels),
                ).fetchall()
                label_nodes = {str(row["node_id"]) for row in rows}
            supporters = [
                str(item["node_id"])
                for item in kept
                if float(item["score"]) >= _SUPPORT_THRESHOLD
                and (
                    float(item["components"].get("lex", 0.0)) > 0
                    or str(item["node_id"]) in label_nodes
                )
            ]
            per_question[sub] = supporters
            for node_id in supporters:
                if node_id not in gate_ids:
                    gate_ids.append(node_id)

    insufficient = (
        not sub_questions
        or not kept
        or any(not supporters for supporters in per_question.values())
    )
    return {
        "kept": kept,
        "insufficient": insufficient,
        "gate": {
            "threshold": _SUPPORT_THRESHOLD,
            "node_ids": gate_ids,
            "per_question": per_question,
        },
    }


def identifier_labels(query: str) -> list[str]:
    """The canonical labels a query carries (retrieval.md sec 4).

    "article 1318" -> ["art 1318"]; "section 2" -> ["sec 2"]; a bare number
    carries no label and yields [].

    The broaden-retry's identifier-only query (retrieval.md sec 3) is built
    from these. It lives here, on the retrieval surface the agent is allowed
    to import, so the agent does not reach into the normalizer itself.
    """
    from apps.api.retrieval import normalizer

    return [label for label, _level in normalizer.extract_labels(query)]
