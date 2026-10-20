"""Route layer for the /v1 endpoints (api.md §1-3).

Transport only: validation, temp-file handling, uniform error rendering, and
the SSE framing. All persistence/parse work lives in
apps/api/ingestion/service.py, all retrieval lives in
apps/api/retrieval/api.py, and all agent work lives in apps/api/agent/ask.py.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
import uuid
from collections.abc import Iterator
from typing import Any

from apps.api.agent import ask as ask_service
from apps.api.ingestion import db as ingestion_db
from apps.api.ingestion import errors, service
from apps.api.models import schemas
from apps.api.retrieval import api as retrieval_api
from apps.api.retrieval import index as retrieval_index
from apps.api.retrieval.scoring import ScoredNode
from fastapi import APIRouter, Form, Query, Request, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse

router = APIRouter(prefix="/v1")


def _db_path(request: Request) -> str:
    """Resolve the SQLite path set by the app factory (defaults to service.DB_PATH)."""
    return getattr(request.app.state, "db_path", None) or service.DB_PATH


def _error_response(err: errors.ApiError) -> JSONResponse:
    return JSONResponse(status_code=err.status, content=err.body())


def _new_trace_id() -> str:
    """A query id (api.md §1: ids are ``q_<uuid8>``)."""
    return f"q_{uuid.uuid4().hex[:8]}"


def _ensure_retrieval_indexes(db_path: str, document_id_or_slug: str) -> None:
    """Build the retrieval indexes of a document that does not have them.

    The upload route builds them right after the ingest. That wiring point is
    the route layer and not the ingestion service because ingestion/ must not
    import retrieval/ (architecture.md §2).

    A document that reached the store by another route - a database restored
    from a copy, an ingest performed by a script, a store built before the
    upload path existed - has no ``node_fts`` and no ``ident_index``, and
    every retrieval query against it fails with "no such table: node_fts",
    which reaches the caller as a 500. The explorer hit exactly this.

    The check is two indexed counts, so the common path costs one lookup and
    nothing is rebuilt. The rebuild itself is retrieval's own
    ``build_indexes``, which is idempotent and rebuilds from the tree, so the
    indexes cannot disagree with the nodes they describe.

    An unknown document is left alone: the retrieval call raises the typed
    404, and this helper must not invent one of its own.
    """
    with ingestion_db.connect(db_path) as conn:
        row = conn.execute(
            "SELECT id FROM documents WHERE id = ? OR slug = ?",
            (document_id_or_slug, document_id_or_slug),
        ).fetchone()
        if row is None:
            return
        doc_id = str(row["id"])
        node_count = int(
            conn.execute(
                "SELECT COUNT(*) FROM nodes WHERE document_id = ?", (doc_id,)
            ).fetchone()[0]
        )
        if node_count == 0:
            # Nothing to index. The retrieval call reports the empty tree.
            return
        tables = {
            str(entry["name"])
            for entry in conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
            )
        }
        fts_count = 0
        if "node_fts" in tables:
            fts_count = int(
                conn.execute(
                    "SELECT COUNT(*) FROM node_fts WHERE node_id IN"
                    " (SELECT id FROM nodes WHERE document_id = ?)",
                    (doc_id,),
                ).fetchone()[0]
            )
        ident_count = 0
        if "ident_index" in tables:
            ident_count = int(
                conn.execute(
                    "SELECT COUNT(*) FROM ident_index WHERE document_id = ?", (doc_id,)
                ).fetchone()[0]
            )
    if fts_count > 0 and ident_count > 0:
        return
    retrieval_index.build_indexes(db_path, doc_id)


@router.post("/documents", status_code=201, response_model=schemas.DocumentUploaded)
async def upload_document(
    request: Request,
    file: UploadFile,
    slug: str | None = Form(default=None),
) -> schemas.DocumentUploaded | JSONResponse:
    db_path = _db_path(request)

    filename = file.filename or ""
    if not filename.lower().endswith(".pdf"):
        return _error_response(
            errors.validation_error("file must be a PDF", filename=filename)
        )

    tmp_path: str | None = None
    try:
        fd, tmp_path = tempfile.mkstemp(suffix=".pdf")
        with os.fdopen(fd, "wb") as tmp:
            while chunk := await file.read(1024 * 1024):
                tmp.write(chunk)
        uploaded = service.ingest_pdf(tmp_path, slug=slug, db_path=db_path)
        # architecture.md §2: retrieval must never be imported by ingestion/,
        # so the product wiring point is here — build per-document indexes on
        # every successful upload (idempotent).
        retrieval_index.build_indexes(db_path, uploaded.id)
        return uploaded
    except errors.ApiError as err:
        return _error_response(err)
    finally:
        if tmp_path is not None:
            with contextlib.suppress(OSError):
                os.unlink(tmp_path)


@router.get("/documents", response_model=schemas.DocumentList)
def list_documents(
    request: Request,
    page: int = Query(default=1),
    page_size: int = Query(default=20, alias="pageSize"),
) -> schemas.DocumentList | JSONResponse:
    if page < 1:
        return _error_response(errors.validation_error("page must be >= 1", page=str(page)))
    if not 1 <= page_size <= 100:
        return _error_response(
            errors.validation_error("pageSize must be between 1 and 100", pageSize=str(page_size))
        )
    try:
        return service.list_documents(page=page, page_size=page_size, db_path=_db_path(request))
    except errors.ApiError as err:
        return _error_response(err)


@router.get("/documents/{doc_id}", response_model=schemas.DocumentDetail)
def get_document(
    request: Request,
    doc_id: str,
) -> schemas.DocumentDetail | JSONResponse:
    try:
        return service.get_document(doc_id, db_path=_db_path(request))
    except errors.ApiError as err:
        return _error_response(err)


@router.get("/documents/{doc_id}/tree", response_model=schemas.DocumentTree)
def get_document_tree(
    request: Request,
    doc_id: str,
    depth: int = Query(default=3),
) -> schemas.DocumentTree | JSONResponse:
    if depth < 1:
        return _error_response(errors.validation_error("depth must be >= 1", depth=str(depth)))
    try:
        return service.get_tree(doc_id, depth=depth, db_path=_db_path(request))
    except errors.ApiError as err:
        return _error_response(err)


# ---------------------------------------------------------------------------
# §3 Retrieval & asking
# ---------------------------------------------------------------------------


def _search_hit(hit: ScoredNode) -> schemas.SearchHit:
    """One retrieval hit as the frozen response shape (api.md §3).

    The retrieval layer's ``ScoredNode`` carries more than the contract's six
    fields (depth, position, the flat-fallback flag). The mapping is
    explicit, so a field added to the dataclass later cannot leak into the
    frozen hit shape by accident.
    """
    return schemas.SearchHit(
        node_id=hit.node_id,
        title=hit.title,
        snippet=hit.snippet,
        score=hit.score,
        components=dict(hit.components),
        page_start=hit.page_start,
    )


@router.post("/search", response_model=schemas.SearchResponse)
def search(
    request: Request,
    payload: schemas.SearchRequest,
) -> schemas.SearchResponse | JSONResponse:
    """`POST /v1/search` (api.md §3): scored node hits for one query.

    An unknown document is a 404; the retrieval layer raises it. ``top_k``
    is bounded by the request model (1..20), so an out-of-range value is a
    422 rather than a clamped value - the contract's range is part of the
    request, not a suggestion.
    """
    db_path = _db_path(request)
    _ensure_retrieval_indexes(db_path, payload.document_id)
    try:
        hits = retrieval_api.search_nodes(
            query=payload.query,
            document_id=payload.document_id,
            top_k=payload.top_k,
            db_path=db_path,
        )
    except errors.ApiError as err:
        return _error_response(err)
    except ValueError as err:
        # The retrieval tool's own bound check (retrieval.md §5). It is
        # reached only if the request model and the tool's bound ever
        # disagree, and a bad value is a bad body either way.
        return _error_response(errors.validation_error(str(err)))
    return schemas.SearchResponse(
        hits=[_search_hit(hit) for hit in hits],
        trace_id=_new_trace_id(),
    )


@router.post("/ask")
def ask(
    request: Request,
    payload: schemas.AskRequest,
) -> dict[str, Any]:
    """`POST /v1/ask` (api.md §3): the AskResponse, verbatim.

    The response is the ask surface's dict, returned as it is: the AskResponse
    shape is frozen (api.md §3, api.md §5), so re-declaring it here would be a
    second copy that could drift from the one the agent produces. A typed
    failure (404, 422, 503 LLM_UNAVAILABLE) propagates to the app-level
    handler, which renders the uniform error body (api.md §1).
    """
    db_path = _db_path(request)
    _ensure_retrieval_indexes(db_path, payload.document_id)
    return ask_service.run_ask(
        payload.document_id,
        payload.question,
        max_iterations=payload.max_iterations,
        db_path=db_path,
    )


def _sse_frame(event: dict[str, Any]) -> str:
    """One SSE frame: the event name and the payload, as api.md §3 freezes them.

    The frame is ``event: <name>\\ndata: <json>\\n\\n``. The payload is the
    event's SCHEMA object: the ``event`` key names the frame and is not part
    of the payload, so the client reads the name from the ``event:`` line and
    the fields from the JSON.

    The JSON is written on ONE line: an SSE ``data:`` field may be repeated
    for a multi-line payload, and a client that joins the lines with a
    newline would then see a payload the contract does not describe.
    """
    name = str(event.get("event", ""))
    payload = {key: value for key, value in event.items() if key != "event"}
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"event: {name}\ndata: {data}\n\n"


def _sse_body(
    document_id: str,
    question: str,
    max_iterations: int,
    db_path: str,
) -> Iterator[str]:
    """The SSE frames of one ask, in the frozen order (api.md §3).

    The frames are yielded as the agent produces them, so a client sees the
    retrieval steps as they happen (PRD US-3). The terminal ``error`` event is
    the LAST frame of the stream: a failure during the run ends the stream,
    and a ``done`` after it would claim an answer the run did not produce.
    """
    for event in ask_service.iter_sse_events(
        document_id,
        question,
        max_iterations=max_iterations,
        db_path=db_path,
    ):
        yield _sse_frame(event)


@router.post("/ask/stream")
def ask_stream(
    request: Request,
    payload: schemas.AskRequest,
) -> StreamingResponse:
    """`POST /v1/ask/stream` (api.md §3): the frozen SSE event sequence.

    A bad body is a 422 RESPONSE, not an SSE ``error`` event: the request
    never reached a run, so there is no run to report an error about. The
    ``error`` event is for a failure DURING the run.

    The retrieval indexes are built BEFORE the response starts, for the same
    reason: an index that is missing is a fault of the STORE, not of the
    run, and it must not arrive as a terminal SSE ``error`` after the client
    has already begun reading a stream.
    """
    db_path = _db_path(request)
    _ensure_retrieval_indexes(db_path, payload.document_id)
    return StreamingResponse(
        _sse_body(
            payload.document_id,
            payload.question,
            payload.max_iterations,
            db_path,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            # The proxy must not buffer the stream: a buffered response
            # arrives all at once, and the streamed steps are the point.
            "X-Accel-Buffering": "no",
        },
    )


def _outgoing_references(node_id: str, db_path: str) -> list[schemas.NodeReference]:
    """The node's outgoing reference edges (api.md §3).

    Read from the ``references_`` table through the same connection helper the
    retrieval layer uses. A resolved edge names its target; an UNRESOLVED edge
    (``target_node_id`` NULL) carries None, because the target was not
    identified and must not be guessed (retrieval.md §7).
    """
    with ingestion_db.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT id, target_node_id, reference_text FROM references_"
            " WHERE source_node_id = ? ORDER BY id",
            (node_id,),
        ).fetchall()
    return [
        schemas.NodeReference(
            id=str(row["id"]),
            target_node_id=(
                str(row["target_node_id"]) if row["target_node_id"] is not None else None
            ),
            reference_text=str(row["reference_text"]),
        )
        for row in rows
    ]


@router.get("/nodes/{node_id}", response_model=schemas.NodeDetail)
def get_node(
    request: Request,
    node_id: str,
) -> schemas.NodeDetail | JSONResponse:
    """`GET /v1/nodes/{id}` (api.md §3): the node, its path, its references.

    The node record comes from the retrieval tool, so the transport does not
    read the tree itself; the hierarchy path comes from the source tool, and
    the outgoing edges from the references table.
    """
    db_path = _db_path(request)
    try:
        node = retrieval_api.inspect_node(node_id=node_id, db_path=db_path)
        source = retrieval_api.get_source(node_id=node_id, db_path=db_path)
    except errors.ApiError as err:
        return _error_response(err)
    return schemas.NodeDetail(
        node_id=str(node["node_id"]),
        document_id=str(node["document_id"]),
        parent_id=(str(node["parent_id"]) if node["parent_id"] is not None else None),
        type=str(node["type"]),
        title=str(node["title"]),
        text=str(node["text"]),
        page_start=node["page_start"],
        page_end=node["page_end"],
        depth=int(node["depth"]),
        position=int(node["position"]),
        hierarchy_path=[str(title) for title in source["hierarchy_path"]],
        references=_outgoing_references(node_id, db_path),
    )


@router.get("/nodes/{node_id}/source", response_model=schemas.NodeSource)
def get_node_source(
    request: Request,
    node_id: str,
) -> schemas.NodeSource | JSONResponse:
    """`GET /v1/nodes/{id}/source` (api.md §3): the node's text and pages."""
    try:
        source = retrieval_api.get_source(node_id=node_id, db_path=_db_path(request))
    except errors.ApiError as err:
        return _error_response(err)
    return schemas.NodeSource(
        node_id=str(source["node_id"]),
        text=str(source["text"]),
        page_start=source["page_start"],
        page_end=source["page_end"],
        hierarchy_path=[str(title) for title in source["hierarchy_path"]],
    )
