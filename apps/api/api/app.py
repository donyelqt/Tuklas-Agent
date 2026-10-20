"""FastAPI app factory for Tuklas Agent (api.md §1-3)."""

from __future__ import annotations

import os

from apps.api.api.routes import router
from apps.api.ingestion import errors, service
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

#: The environment variable that lists the browser origins allowed to call
#: the API. The explorer at /app/* is served by the Next dev server on a
#: different origin than the API, so the browser needs the API to answer the
#: preflight. Comma-separated; the default covers the two spellings of the
#: local Next dev server.
ENV_CORS_ORIGINS = "TUKLAS_CORS_ORIGINS"

#: The default allowed origins: the local Next dev server.
DEFAULT_CORS_ORIGINS = (
    "http://localhost:3000",
    "http://127.0.0.1:3000",
)


def _cors_origins() -> list[str]:
    """The allowed browser origins, read from the environment.

    An empty list disables the CORS middleware entirely: a deployment that
    serves the explorer from the API's own origin needs no cross-origin
    allowance, and an empty allowlist that still answers a preflight would be
    a permissive default.
    """
    raw = os.environ.get(ENV_CORS_ORIGINS, "")
    if not raw.strip():
        return list(DEFAULT_CORS_ORIGINS)
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


def create_app(db_path: str | None = None) -> FastAPI:
    """App factory; db_path None -> service.DB_PATH."""
    app = FastAPI(title="Tuklas Agent API", version="v1")
    app.state.db_path = db_path or service.DB_PATH
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins(),
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )
    app.include_router(router)

    @app.exception_handler(errors.ApiError)
    async def api_error_handler(request: Request, err: errors.ApiError) -> JSONResponse:
        return JSONResponse(status_code=err.status, content=err.body())

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, err: RequestValidationError
    ) -> JSONResponse:
        """A bad body is the frozen 422, not FastAPI's default shape.

        FastAPI answers a malformed body with ``{"detail": [...]}``. api.md §1
        freezes ONE error shape for every endpoint - ``{"error": {"code",
        "message", "details"}}`` - and lists ``VALIDATION_ERROR`` (422) for a
        bad body. A client has no arm for ``detail``, so the default shape
        would leave the frozen code unreachable for exactly the case the
        table names.

        The details carry the field and the problem type. They never carry
        the submitted value: the value can be the document text the client
        sent, and echoing it back would put the client's own content into the
        response body.
        """
        details: dict[str, str] = {}
        for problem in err.errors():
            location = ".".join(str(part) for part in problem.get("loc", ()))
            field = location or "body"
            kind = str(problem.get("type", "invalid"))
            details[field] = kind
        return JSONResponse(
            status_code=422,
            content=errors.validation_error("the request body is invalid", **details).body(),
        )

    @app.exception_handler(Exception)
    async def internal_error_handler(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=500,
            content={"error": {"code": "INTERNAL_ERROR", "message": "internal server error", "details": {}}},
        )

    return app


app = create_app()
