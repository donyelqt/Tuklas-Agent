"""Route layer for the /v1 endpoints (api.md §1-3).

Transport only: validation, temp-file handling, uniform error rendering, and
the SSE framing. All persistence/parse work lives in
apps/api/ingestion/service.py, all retrieval lives in
apps/api/retrieval/api.py, and all agent work lives in apps/api/agent/ask.py.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import tempfile
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from apps.api.agent import ask as ask_service
from apps.api.evaluation import config as eval_config
from apps.api.evaluation import dataset as dataset_module
from apps.api.evaluation import runner as eval_runner
from apps.api.evaluation import store as eval_store
from apps.api.ingestion import db as ingestion_db
from apps.api.ingestion import errors, service
from apps.api.models import schemas
from apps.api.retrieval import api as retrieval_api
from apps.api.retrieval import index as retrieval_index
from apps.api.retrieval.scoring import ScoredNode
from fastapi import APIRouter, Form, Query, Request, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse

_LOG = logging.getLogger(__name__)

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


# ---------------------------------------------------------------------------
# §4 Evaluation
# ---------------------------------------------------------------------------


#: The dataset snapshots the API can run (evaluation.md §1: the committed
#: split files under ``data/datasets/v1/``). A snapshot that is not one of
#: these is a 422 (api.md §4: "422 (unknown snapshot, arm mismatch,
#: temperature != 0)").
EVAL_SNAPSHOTS: dict[str, str] = {
    "v1-dev": "dev",
    "v1-test": "test",
}

#: The dataset directory, relative to the repo root.
EVAL_DATASET_DIR = "data/datasets/v1"

#: The arms the framework knows (api.md §4: "tuklas" | "vector"). The two
#: are the experiment's whole point: the vectorless arm and the quarantined
#: dense baseline.
EVAL_ARMS: tuple[str, ...] = ("tuklas", "vector")


def _dataset_path(snapshot: str) -> str:
    """The split file a snapshot names, or the 422 that says there is none."""
    split = EVAL_SNAPSHOTS.get(snapshot)
    if split is None:
        raise errors.validation_error(
            "unknown dataset snapshot",
            dataset_snapshot=snapshot,
            known_snapshots=",".join(sorted(EVAL_SNAPSHOTS)),
        )
    root = Path(__file__).resolve().parents[3]
    return str(root / EVAL_DATASET_DIR / f"{split}.json")


def _eval_config_payload(payload: schemas.EvaluationRequest) -> dict[str, Any]:
    """The request body as the run config's own shape (api.md §4).

    The request model and the config file share the EvalConfig shape, so
    the payload is the request's own fields plus the derived config id. The
    id is DERIVED, never supplied: a client that declared its own id
    would be asserting a hash of a config it computed, which is not a
    check.
    """
    body = {
        "dataset_snapshot": payload.dataset_snapshot,
        "arms": {
            name: {
                "model": block.model,
                "backend": block.backend,
                "temperature": block.temperature,
                "seed": block.seed,
            }
            for name, block in payload.arms.items()
        },
        "k": list(payload.k),
        "config": payload.config.model_dump(),
    }
    body["config_id"] = eval_config.derive_config_id(body)
    return body


@router.post("/evaluations", status_code=202, response_model=schemas.EvaluationQueued)
def create_evaluation(
    request: Request,
    payload: schemas.EvaluationRequest,
) -> schemas.EvaluationQueued | JSONResponse:
    """`POST /v1/evaluations` (api.md §4): queue an evaluation.

    The route QUEUES the run and answers 202 with the evaluation id and
    the status, exactly as the contract says. The run itself is executed
    by the runner, and its result is read back through
    `GET /v1/evaluations/{id}`.

    Three failures are typed, per api.md §4:

    * an unknown snapshot, an arm mismatch, or a temperature that is not
      0 -> 422 `VALIDATION_ERROR`;
    * a model-parity violation -> 422 as well, because a config whose
      arms disagree on the model is an INVALID RUN (evaluation.md §1),
      and the caller can fix it;
    * a runner crash -> 500 `EVAL_FAILED`.
    """
    try:
        dataset_path = _dataset_path(payload.dataset_snapshot)
    except errors.ApiError as err:
        return _error_response(err)

    known_arms = EVAL_ARMS
    unknown_arms = sorted(set(payload.arms) - set(known_arms))
    if unknown_arms:
        return _error_response(
            errors.validation_error(
                "unknown arm",
                arms=",".join(unknown_arms),
                known_arms=",".join(known_arms),
            )
        )
    missing_arms = sorted(set(known_arms) - set(payload.arms))
    if missing_arms:
        return _error_response(
            errors.validation_error(
                "the run must name every arm",
                arms=",".join(missing_arms),
                known_arms=",".join(known_arms),
            )
        )

    body = _eval_config_payload(payload)
    violations, _config_id = eval_config.check_run_config(body)
    if violations:
        return _error_response(
            errors.validation_error(
                "the run config is invalid", violations="; ".join(violations)
            )
        )

    db_path = _db_path(request)
    evaluation_id = eval_store.new_evaluation_id()
    try:
        dataset = dataset_module.load_dataset(dataset_path)
    except dataset_module.DatasetError as err:
        return _error_response(
            errors.validation_error("the dataset snapshot is invalid", reason=str(err))
        )

    try:
        run_config = eval_config.load_run_config(body)
    except eval_config.ConfigError as err:
        return _error_response(
            errors.validation_error("the run config is invalid", reason=str(err))
        )

    try:
        runner = eval_runner.Runner(db_path, dataset.questions[0].document, run_config)
        outcome = runner.run(dataset, evaluation_id=evaluation_id)
    except Exception:
        # The contract's code for a runner crash (api.md §4: "500 EVAL_FAILED
        # (runner crash)"). The exception is logged, not returned: the
        # details of an internal fault must not leak (api.md §1).
        _LOG.exception("the evaluation runner failed")
        return _error_response(
            errors.ApiError("EVAL_FAILED", "the evaluation run failed", 500, {})
        )

    return schemas.EvaluationQueued(
        evaluation_id=outcome["evaluation_id"],
        status=outcome["status"],
    )


@router.get("/evaluations/{evaluation_id}", response_model=schemas.EvaluationDetail)
def get_evaluation(
    request: Request,
    evaluation_id: str,
) -> schemas.EvaluationDetail | JSONResponse:
    """`GET /v1/evaluations/{id}` (api.md §4): the run, its rows, its numbers.

    An unknown evaluation is a 404 (api.md §4). The response is the
    JOIN of the ``evaluation_runs`` row and its ``retrieval_runs`` rows,
    so what a reader sees is what was recorded, not a re-computation.
    """
    db_path = _db_path(request)
    loaded = eval_store.load_evaluation(db_path, evaluation_id)
    if loaded is None:
        return _error_response(
            errors.not_found("unknown evaluation", evaluation_id=evaluation_id)
        )

    run = loaded["run"]
    questions = loaded["questions"]
    metrics = run.get("metrics") or {}
    aggregates = metrics.get("arms") or {}

    per_question = [
        schemas.QuestionRow(
            question_id=str(row.get("question_id", "")),
            arm=str(row.get("arm", "")),
            retrieved_node_ids=list(row.get("retrieved_nodes") or []),
            gold_hit=bool(row.get("gold_hit")),
            rank_first_hit=(
                int(row["rank_first_hit"]) if row.get("rank_first_hit") is not None else None
            ),
            claims_matched=int(row.get("claims_matched") or 0),
            claims_total=int(row.get("claims_total") or 0),
            faithfulness=(
                float(row["faithfulness"]) if row.get("faithfulness") is not None else None
            ),
            citations_valid=bool(row.get("citations_valid")),
            insufficient=bool(row.get("insufficient")),
            latency_ms={
                "retrieval": int(row.get("latency_ms") or 0),
                "generation": int(row.get("generation_latency_ms") or 0),
            },
            tokens={
                "in": int(row.get("tokens_in") or 0),
                "out": int(row.get("tokens_out") or 0),
            },
        )
        for row in questions
    ]

    return schemas.EvaluationDetail(
        status=str(run.get("status", "done")),
        config=run.get("config") or {},
        per_question=per_question,
        aggregates=schemas.Aggregates(
            arms={
                name: schemas.ArmAggregates(
                    retrieval=schemas.RetrievalAggregate(
                        overall=entry.get("retrieval", {}).get("overall", {}),
                        by_category=entry.get("retrieval", {}).get("by_category", {}),
                    ),
                    generation=schemas.GenerationAggregate(
                        correctness=float(entry.get("generation", {}).get("correctness", 0.0)),
                        faithfulness=float(entry.get("generation", {}).get("faithfulness", 0.0)),
                        citation_accuracy=float(
                            entry.get("generation", {}).get("citation_accuracy", 0.0)
                        ),
                        abstention_score=float(
                            entry.get("generation", {}).get("abstention_score", 0.0)
                        ),
                    ),
                    system=schemas.SystemAggregate(
                        latency_p50=float(entry.get("system", {}).get("latency_p50", 0.0)),
                        latency_p95=float(entry.get("system", {}).get("latency_p95", 0.0)),
                        tokens_mean=float(entry.get("system", {}).get("tokens_mean", 0.0)),
                        iterations_mean=float(entry.get("system", {}).get("iterations_mean", 0.0)),
                    ),
                )
                for name, entry in aggregates.items()
            },
            n_questions=int(metrics.get("n_questions", 0)),
            n_arms=int(metrics.get("n_arms", 0)),
        ),
        system_stats=schemas.SystemStats(
            n_questions=int(metrics.get("n_questions", 0)),
            n_arms=int(metrics.get("n_arms", 0)),
            split_seed=int(run.get("split_seed") or 0),
            dataset_snapshot=str(run.get("dataset_snapshot") or ""),
        ),
    )
