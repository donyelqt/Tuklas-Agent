"""Frozen API models (api.md §2-3) — pydantic v2, additive-only under /v1."""

from __future__ import annotations

from typing import Any

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


# ---------------------------------------------------------------------------
# §4 Evaluation (typed)
# ---------------------------------------------------------------------------


class EvalArmGeneration(BaseModel):
    """One arm's generation parameters (api.md §4).

    The four fields are the PARITY fields: every arm must carry the same
    model, the same backend, temperature 0 and the same seed
    (evaluation.md §1: "Arms differ only in retrieval. Different models
    across arms = invalid run"). The temperature is bounded to 0 by the
    model, not by a range: api.md §4 fixes it, so a non-zero value is a
    bad body, not a clamped one.
    """

    model: str
    backend: str
    temperature: float = Field(default=0.0, ge=0.0, le=0.0)
    seed: int


class EvalBonusFlags(BaseModel):
    """The two retrieval bonus flags (api.md §4). Both default to off:
    retrieval.md §3 says the phrase and fuzzy bonuses are off by default
    and enabled only via a recorded tuning run.
    """

    phrase: bool = False
    fuzzy: bool = False


class EvalMargins(BaseModel):
    """The provisional margins (api.md §4)."""

    recall5: float = Field(default=0.10, ge=0.0, le=1.0)
    faithfulness: float = Field(default=0.10, ge=0.0, le=1.0)
    max_category_gap: float = Field(default=0.20, ge=0.0, le=1.0)


class EvalWeights(BaseModel):
    """The full retrieval weights snapshot (api.md §4).

    The five components are retrieval.md §3's, and each is bounded to the
    legal range [0, 2]. A weight outside the range is a bad body, not a
    clamped value.
    """

    lex: float = Field(default=1.0, ge=0.0, le=2.0)
    ident: float = Field(default=1.0, ge=0.0, le=2.0)
    title: float = Field(default=1.0, ge=0.0, le=2.0)
    ref: float = Field(default=1.0, ge=0.0, le=2.0)
    hier: float = Field(default=1.0, ge=0.0, le=2.0)


class EvalConfig(BaseModel):
    """The run config (api.md §4).

    Every field the contract declares is REQUIRED except the ones with a
    declared default, and the bounds are ENFORCED: a fuzzy_threshold
    outside 80..95 is a 422, not a clamped value.
    """

    code_version: str
    weights: EvalWeights
    normalization: str
    fuzzy_threshold: int = Field(default=85, ge=80, le=95)
    bonus_flags: EvalBonusFlags = Field(default_factory=EvalBonusFlags)
    stoplist_version: str
    model: str
    backend: str
    temperature: float = Field(default=0.0, ge=0.0, le=0.0)
    seed: int
    judge_prompt_version: str
    margins: EvalMargins = Field(default_factory=EvalMargins)


class EvaluationRequest(BaseModel):
    """The body of ``POST /v1/evaluations`` (api.md §4).

    ``arms`` is an OBJECT of per-arm generation blocks, keyed by the arm
    name. The contract writes ``arms: ["tuklas","vector"]`` (a list of
    names), and a list of names cannot carry the per-arm model, so the
    object is the shape that satisfies the contract's own requirement that
    the arms' generation parameters be checkable. The arm names are
    constrained to the two the framework knows.
    """

    dataset_snapshot: str
    arms: dict[str, EvalArmGeneration]
    k: list[int] = Field(default_factory=lambda: [1, 3, 5, 10])
    config: EvalConfig


class EvaluationQueued(BaseModel):
    """The 202 response body of ``POST /v1/evaluations`` (api.md §4)."""

    evaluation_id: str
    status: str


class QuestionRow(BaseModel):
    """One question, one arm (api.md §4's QuestionRow)."""

    question_id: str
    arm: str
    retrieved_node_ids: list[str]
    gold_hit: bool
    rank_first_hit: int | None = Field(default=None, ge=1)
    claims_matched: int = Field(default=0, ge=0)
    claims_total: int = Field(default=0, ge=0)
    faithfulness: float | None = Field(default=None, ge=0.0, le=1.0)
    citations_valid: bool
    insufficient: bool
    latency_ms: dict[str, int]
    tokens: dict[str, int]


class RetrievalAggregate(BaseModel):
    """The retrieval aggregate (api.md §4's Aggregates)."""

    overall: dict[str, float | int]
    by_category: dict[str, dict[str, float | int]]


class GenerationAggregate(BaseModel):
    """The generation aggregate (api.md §4's Aggregates)."""

    correctness: float = Field(ge=0.0, le=1.0)
    faithfulness: float = Field(ge=0.0, le=1.0)
    citation_accuracy: float = Field(ge=0.0, le=1.0)
    abstention_score: float = Field(ge=0.0, le=1.0)


class SystemAggregate(BaseModel):
    """The system aggregate (api.md §4's Aggregates)."""

    latency_p50: float
    latency_p95: float
    tokens_mean: float
    iterations_mean: float


class ArmAggregates(BaseModel):
    """One arm's aggregates (api.md §4's Aggregates)."""

    retrieval: RetrievalAggregate
    generation: GenerationAggregate
    system: SystemAggregate


class Aggregates(BaseModel):
    """The aggregates (api.md §4's Aggregates).

    The arms are reported SEPARATELY, under ``arms``. There is no
    "winner" field and no single blended number: the outcome of the
    comparison is unknown until it is measured, and a field that named it
    would be making a claim the spec forbids.
    """

    arms: dict[str, ArmAggregates]
    n_questions: int = Field(ge=0)
    n_arms: int = Field(ge=0)


class SystemStats(BaseModel):
    """The system stats (api.md §4's SystemStats)."""

    n_questions: int = Field(ge=0)
    n_arms: int = Field(ge=0)
    split_seed: int
    dataset_snapshot: str


class EvaluationDetail(BaseModel):
    """The 200 response body of ``GET /v1/evaluations/{id}`` (api.md §4)."""

    status: str
    config: dict[str, Any]
    per_question: list[QuestionRow]
    aggregates: Aggregates
    system_stats: SystemStats
