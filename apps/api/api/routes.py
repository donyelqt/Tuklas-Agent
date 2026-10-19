"""Route layer for the Day 1 documents endpoints (api.md §1-2).

Transport only: validation, temp-file handling, and uniform error rendering.
All persistence/parse work lives in apps/api/ingestion/service.py.
"""

from __future__ import annotations

import contextlib
import os
import tempfile

from apps.api.ingestion import errors, service
from apps.api.models import schemas
from fastapi import APIRouter, Form, Query, Request, UploadFile
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/v1")


def _db_path(request: Request) -> str:
    """Resolve the SQLite path set by the app factory (defaults to service.DB_PATH)."""
    return getattr(request.app.state, "db_path", None) or service.DB_PATH


def _error_response(err: errors.ApiError) -> JSONResponse:
    return JSONResponse(status_code=err.status, content=err.body())


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
        return service.ingest_pdf(tmp_path, slug=slug, db_path=db_path)
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
