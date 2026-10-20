"""Agent tool wrappers (PLAN.md Day 3, architecture.md sec 4).

Seven thin wrappers around ``apps.api.retrieval.api``. Each does exactly four
things and nothing else:

1. validate args against the binding caps (retrieval.md sec 5),
2. call the retrieval tool,
3. record a structured trace entry + tool_result (architecture.md sec 4:
   ``tool_result`` carries ``relevant: bool``; irrelevant branches contribute
   zero evidence nodes and are logged with their branch id),
4. increment the iteration budget (max 12 per ask, broaden-retry included).

There is no sqlite import and no direct tree read: ``retrieval.api`` is the
only tree access this module performs. That is the agent-thinness floor in
CONSTRAINTS.md, enforced by ``scripts/check_no_vectors.py``.
"""

from __future__ import annotations

import time
from typing import Any

from apps.api.agent.state import AgentState
from apps.api.ingestion import errors
from apps.api.retrieval.api import (
    expand_context,
    follow_reference,
    get_parent,
    get_source,
    inspect_node,
    list_children,
    search_nodes,
)

#: retrieval.md sec 5: a node shorter than this cannot contribute evidence.
EVIDENCE_MIN_TEXT_CHARS = 50


def _db_kwarg(state: AgentState) -> dict[str, Any]:
    """The retrieval db_path override, when the caller pinned one.

    ``retrieval.api`` binds its default db_path at definition time, so a
    per-run store (tests, the API app factory) must be passed explicitly.
    """
    path = state.get("_db_path")
    return {"db_path": path} if path else {}


def _evidence_eligible(node: Any) -> bool:
    """True when this node could itself contribute evidence.

    Same two rules retrieval.md sec 5 applies when selecting evidence: enough
    text to be quotable, and a page span to cite. A heading-only node fails
    both, so it is a dead branch rather than evidence.
    """
    if not isinstance(node, dict):
        return False
    text = node.get("text")
    if text is None:
        return False
    if len(str(text)) < EVIDENCE_MIN_TEXT_CHARS:
        return False
    return node.get("page_start") is not None


def _hit_matched(hit: Any) -> bool:
    """True when a scored hit actually matched the query.

    ``search_nodes`` returns the document's top-k nodes regardless of whether
    the query matched them; a non-matching query comes back with every score
    at 0.0. A hit counts as a match when the LEXICAL or IDENTIFIER component
    fired — the same two signals the sufficiency gate accepts (retrieval.md
    §3: "score >= 0.15 AND (lex > 0 OR identifier-hit)").

    The total score alone is NOT enough. The ``hier`` component scores 1.0 for
    any node that shares a parent with a lex-top-3 node, so a structural
    sibling can carry a full point without matching a single query term.
    Treating that as a match would make a search branch look relevant when
    the query matched nothing of substance.
    """
    components = getattr(hit, "components", None) or {}
    return bool(
        float(components.get("lex", 0.0) or 0.0) > 0.0
        or float(components.get("ident", 0.0) or 0.0) > 0.0
    )


def _node_ids(nodes: Any) -> list[str]:
    """Node ids from a list of node dicts."""
    if not isinstance(nodes, list):
        return []
    out: list[str] = []
    for node in nodes:
        if isinstance(node, dict) and node.get("node_id"):
            out.append(str(node["node_id"]))
    return out


def _record(
    state: AgentState,
    tool: str,
    args: dict[str, Any],
    result_ids: list[str],
    relevant: bool,
    latency_ms: float,
    error: str | None = None,
) -> None:
    """Append the tool_result + trace entry for one wrapper call."""
    state["tool_iterations"] = int(state.get("tool_iterations", 0)) + 1
    branch_id = f"{tool}#{state['tool_iterations']}"
    entry: dict[str, Any] = {
        "tool": tool,
        "args": args,
        "result_ids": result_ids,
        "relevant": relevant,
        "branch_id": branch_id,
        "latency_ms": round(latency_ms, 3),
    }
    if error is not None:
        entry["error"] = error
    state["tool_results"].append(
        {
            "tool": tool,
            "args": args,
            "result_ids": result_ids,
            "relevant": relevant,
            "latency_ms": round(latency_ms, 3),
        }
    )
    state["trace"].append(entry)
    if not relevant:
        # Irrelevant branch: zero evidence nodes, logged with its branch id.
        state["visited_branches"].append(
            {
                "branch_id": branch_id,
                "tool": tool,
                "args": args,
                "reason": "irrelevant_no_evidence",
            }
        )


def budget_left(state: AgentState) -> int:
    """Tool iterations still available (architecture.md sec 4 budget)."""
    return int(state.get("max_iterations", 12)) - int(state.get("tool_iterations", 0))


def agent_search_nodes(state: AgentState, args: dict[str, Any]) -> AgentState:
    """search_nodes — default 10, hard max 20 (retrieval.md sec 5).

    Relevance: a search branch is relevant only if the retrieval actually
    matched something. ``search_nodes`` returns the top-k NODES of the
    document, so a query that matches nothing still comes back with ten
    results — every one of them at score 0.0, because the min-max
    normalization in scoring.py maps an all-zero lex layer to all-zero
    scores. Counting those as a hit would make every search branch
    relevant and hide the dead end the trace is supposed to show.
    A hit is a match when its score is above zero, or when it carries an
    identifier or title match (either can carry the answer on its own).
    """
    query = str(args["query"])
    raw_top_k = int(args.get("top_k", 10))
    top_k = min(20, max(1, raw_top_k))
    start = time.perf_counter()
    hits = search_nodes(
        query=query,
        document_id=state["document_id"],
        top_k=top_k,
        **_db_kwarg(state),
    )
    latency = (time.perf_counter() - start) * 1000.0
    matched = [hit for hit in hits if _hit_matched(hit)]
    hit_ids = [str(hit.node_id) for hit in matched]
    # The scored hits feed select_evidence (retrieval-owned) later.
    state.setdefault("_hits", []).extend(hits)
    state.setdefault("_searched", []).append(query)
    _record(
        state,
        "search_nodes",
        {"query": query, "top_k": top_k},
        hit_ids,
        bool(hit_ids),
        latency,
    )
    return state


def agent_inspect_node(state: AgentState, args: dict[str, Any]) -> AgentState:
    """inspect_node — single node, 8000-char cap (retrieval.md sec 5)."""
    node_id = str(args["node_id"])
    start = time.perf_counter()
    try:
        node = inspect_node(node_id=node_id, **_db_kwarg(state))
    except errors.ApiError as err:
        _record(
            state,
            "inspect_node",
            {"node_id": node_id},
            [],
            False,
            (time.perf_counter() - start) * 1000.0,
            error=err.code,
        )
        return state
    latency = (time.perf_counter() - start) * 1000.0
    relevant = _evidence_eligible(node)
    state.setdefault("_inspected", []).append(node_id)
    _record(
        state,
        "inspect_node",
        {"node_id": node_id},
        [node_id] if isinstance(node, dict) and node.get("node_id") else [],
        relevant,
        latency,
    )
    return state


def agent_list_children(state: AgentState, args: dict[str, Any]) -> AgentState:
    """list_children — max 50 rows, 200-char summaries (retrieval.md sec 5)."""
    node_id = str(args["node_id"])
    limit = min(50, max(1, int(args.get("limit", 50))))
    start = time.perf_counter()
    try:
        children = list_children(node_id=node_id, limit=limit, **_db_kwarg(state))
    except errors.ApiError as err:
        _record(
            state,
            "list_children",
            {"node_id": node_id, "limit": limit},
            [],
            False,
            (time.perf_counter() - start) * 1000.0,
            error=err.code,
        )
        return state
    latency = (time.perf_counter() - start) * 1000.0
    ids = _node_ids(children)
    relevant = any(_evidence_eligible(child) for child in children)
    _record(
        state,
        "list_children",
        {"node_id": node_id, "limit": limit},
        ids,
        relevant,
        latency,
    )
    return state


def agent_get_parent(state: AgentState, args: dict[str, Any]) -> AgentState:
    """get_parent — None at the document root (retrieval.md sec 5)."""
    node_id = str(args["node_id"])
    start = time.perf_counter()
    try:
        parent = get_parent(node_id=node_id, **_db_kwarg(state))
    except errors.ApiError as err:
        _record(
            state,
            "get_parent",
            {"node_id": node_id},
            [],
            False,
            (time.perf_counter() - start) * 1000.0,
            error=err.code,
        )
        return state
    latency = (time.perf_counter() - start) * 1000.0
    ids = [str(parent["node_id"])] if isinstance(parent, dict) and parent.get("node_id") else []
    _record(
        state,
        "get_parent",
        {"node_id": node_id},
        ids,
        _evidence_eligible(parent),
        latency,
    )
    return state


def agent_expand_context(state: AgentState, args: dict[str, Any]) -> AgentState:
    """expand_context — radius <=2, <=5 nodes, <=2000 chars/node."""
    node_id = str(args["node_id"])
    radius = min(2, max(0, int(args.get("radius", 1))))
    start = time.perf_counter()
    try:
        neighbors = expand_context(node_id=node_id, radius=radius, **_db_kwarg(state))
    except errors.ApiError as err:
        _record(
            state,
            "expand_context",
            {"node_id": node_id, "radius": radius},
            [],
            False,
            (time.perf_counter() - start) * 1000.0,
            error=err.code,
        )
        return state
    latency = (time.perf_counter() - start) * 1000.0
    ids = _node_ids(neighbors)
    relevant = any(_evidence_eligible(node) for node in neighbors)
    _record(
        state,
        "expand_context",
        {"node_id": node_id, "radius": radius},
        ids,
        relevant,
        latency,
    )
    return state


def agent_follow_reference(state: AgentState, args: dict[str, Any]) -> AgentState:
    """follow_reference — an unresolved edge stays unresolved (retrieval.md sec 7)."""
    reference_id = str(args["reference_id"])
    start = time.perf_counter()
    followed = follow_reference(reference_id=reference_id, **_db_kwarg(state))
    latency = (time.perf_counter() - start) * 1000.0
    node = followed.get("node") if isinstance(followed, dict) else None
    ids = (
        [str(node["node_id"])]
        if isinstance(node, dict) and node.get("node_id")
        else []
    )
    resolved = isinstance(followed, dict) and followed.get("status") == "resolved"
    _record(
        state,
        "follow_reference",
        {"reference_id": reference_id},
        ids,
        bool(resolved and _evidence_eligible(node)),
        latency,
    )
    return state


def agent_get_source(state: AgentState, args: dict[str, Any]) -> AgentState:
    """get_source — node text + pages + hierarchy path (retrieval.md sec 5)."""
    node_id = str(args["node_id"])
    start = time.perf_counter()
    try:
        source = get_source(node_id=node_id, **_db_kwarg(state))
    except errors.ApiError as err:
        _record(
            state,
            "get_source",
            {"node_id": node_id},
            [],
            False,
            (time.perf_counter() - start) * 1000.0,
            error=err.code,
        )
        return state
    latency = (time.perf_counter() - start) * 1000.0
    ids = (
        [str(source["node_id"])]
        if isinstance(source, dict) and source.get("node_id")
        else []
    )
    _record(
        state,
        "get_source",
        {"node_id": node_id},
        ids,
        _evidence_eligible(source),
        latency,
    )
    return state


#: The 7 tools, and nothing else. The graph may only reach the tree through
#: this table.
TOOL_TABLE: dict[str, Any] = {
    "search_nodes": agent_search_nodes,
    "inspect_node": agent_inspect_node,
    "list_children": agent_list_children,
    "get_parent": agent_get_parent,
    "expand_context": agent_expand_context,
    "follow_reference": agent_follow_reference,
    "get_source": agent_get_source,
}
