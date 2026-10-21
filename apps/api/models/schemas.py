"""Frozen API models (api.md §2-3) — pydantic v2, additive-only under /v1."""

from __future__ import annotations

from pydantic import BaseModel, Field


class TreeNode(BaseModel):
    """One node in the document tree (`GET /documents/{id}/tree`)."""

    id: str
    type: str
    title: str
    depth: int = Field(ge=0)
    position: int = Field(ge=0)
    page_start: int | None = Field(default=None, ge=1)
    page_end: int | None = Field(default=None, ge=1)
    child_count: int = Field(ge=0)
    children: list[TreeNode] = []


class TreeRoot(BaseModel):
    """The `tree_root` summary inside the 201 upload response (api.md §2)."""

    id: str
    type: str
    title: str
    child_count: int = Field(ge=0)


class DocumentUploaded(BaseModel):
    """201 response body for `POST /v1/documents` (api.md §2)."""

    id: str
    slug: str
    name: str
    page_count: int = Field(ge=1)
    parse_confidence: float = Field(ge=0.0, le=1.0)
    tree_root: TreeRoot


class DocumentSummary(BaseModel):
    """One row of `GET /v1/documents` list data (api.md §2)."""

    id: str
    name: str
    page_count: int = Field(ge=1)
    created_at: str


class Pagination(BaseModel):
    """Pagination envelope (api.md §1: 1-indexed, max pageSize 100)."""

    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    total_items: int = Field(ge=0)
    total_pages: int = Field(ge=0)


class DocumentList(BaseModel):
    """200 response for `GET /v1/documents` (api.md §2)."""

    data: list[DocumentSummary]
    pagination: Pagination


class DocumentDetail(BaseModel):
    """200 response for `GET /v1/documents/{id}` (api.md §2)."""

    id: str
    name: str
    file_hash: str
    page_count: int = Field(ge=1)
    parse_confidence: float = Field(ge=0.0, le=1.0)
    created_at: str


class DocumentTree(BaseModel):
    """200 response for `GET /v1/documents/{id}/tree?depth=3` (api.md §2)."""

    root: TreeNode
    children: list[TreeNode]


# ---------------------------------------------------------------------------
# §3 Retrieval & asking
# ---------------------------------------------------------------------------


class SearchRequest(BaseModel):
    """Request body for `POST /v1/search` (api.md §3).

    ``top_k`` is bounded to 1..20 by the contract. The bound is ENFORCED,
    not clamped: an out-of-range value is a bad body (422), because a
    silently clamped value would answer a different question than the one
    that was asked.

    The string fields carry NO length bound, because the contract declares
    bounds only for the numeric fields (``top_k``, ``max_iterations``,
    ``pageSize`` - api.md §5). A bound the contract does not declare would
    reject a request the contract declares legal.
    """

    document_id: str
    query: str
    top_k: int = Field(default=10, ge=1, le=20)


class SearchHit(BaseModel):
    """One ranked node in a `POST /v1/search` response (api.md §3).

    The fields are the six the contract freezes: ``node_id, title, snippet,
    score, components, page_start``. The retrieval layer's ``ScoredNode``
    carries more (depth, position, the flat-fallback flag); those are not
    part of the frozen hit shape and are not returned.
    """

    node_id: str
    title: str
    snippet: str
    score: float
    components: dict[str, float]
    page_start: int | None = Field(default=None, ge=1)


class SearchResponse(BaseModel):
    """200 response for `POST /v1/search` (api.md §3)."""

    hits: list[SearchHit]
    #: The query id (api.md §1: ids are ``q_<uuid8>``). It names the run, so
    #: a search and the trace row it produced can be tied together.
    trace_id: str


class AskRequest(BaseModel):
    """Request body for `POST /v1/ask` and `POST /v1/ask/stream` (api.md §3).

    ``max_iterations`` is bounded to 1..12 by the contract. The bound is
    ENFORCED, not clamped: an out-of-range value is a bad body (422).

    The string fields carry NO length bound, because the contract declares
    bounds only for the numeric fields (api.md §5). An empty question in
    particular is LEGAL: it is the zero-tool-call run (api.md §3), so a
    length bound would reject a request the contract declares legal.
    """

    document_id: str
    question: str
    max_iterations: int = Field(default=12, ge=1, le=12)


class NodeReference(BaseModel):
    """One outgoing reference edge of a node (api.md §3).

    ``target_node_id`` is None for an UNRESOLVED edge: the reference was
    found in the text but its target was not identified. The field is
    None, never a guess (retrieval.md §7: an unresolved edge is typed, never
    invented).
    """

    id: str
    target_node_id: str | None = Field(default=None)
    reference_text: str


class NodeFull(BaseModel):
    """The node record itself (`GET /v1/nodes/{id}`, api.md §3).

    The field names are the retrieval layer's node-row names (retrieval.md
    §5): ``node_id`` is the row's ``id``, and the rest are the row's own
    columns. The text is the retrieval-capped text (8000 chars), not the
    full body.
    """

    node_id: str
    document_id: str
    parent_id: str | None = Field(default=None)
    type: str
    title: str
    text: str
    page_start: int | None = Field(default=None, ge=1)
    page_end: int | None = Field(default=None, ge=1)
    depth: int = Field(ge=0)
    position: int = Field(ge=0)


class NodeDetail(NodeFull):
    """200 response for `GET /v1/nodes/{id}` (api.md §3).

    The node's own fields, plus its hierarchy path (root-first ancestor
    titles) and its outgoing reference edges. The shape is FLAT: api.md §3
    writes "NodeFull + hierarchy_path + outgoing refs", and the sibling
    endpoint (`GET /v1/nodes/{id}/source`) is flat too, so the two answers
    about a node are read the same way.

    The node id is ``node_id``, the name the retrieval layer already uses
    (``inspect_node`` returns ``node_id``). It is NOT also published as
    ``id``: two names for one field is two names to keep in agreement.
    """

    hierarchy_path: list[str]
    references: list[NodeReference] = []


class NodeSource(BaseModel):
    """200 response for `GET /v1/nodes/{id}/source` (api.md §3).

    The four contract keys are ``text, page_start, page_end,
    hierarchy_path``. ``node_id`` is an OPTIONAL addition (api.md §5 admits
    additive optional fields under /v1): it names the node the source came
    from, so a client that fetched it from a citation can check that the
    citation and the source agree.
    """

    node_id: str
    text: str
    page_start: int | None = Field(default=None, ge=1)
    page_end: int | None = Field(default=None, ge=1)
    hierarchy_path: list[str]
