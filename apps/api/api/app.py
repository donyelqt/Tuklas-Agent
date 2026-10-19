"""FastAPI app factory for Tuklas Agent (api.md §1-2)."""

from __future__ import annotations

from apps.api.api.routes import router
from apps.api.ingestion import errors, service
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


def create_app(db_path: str | None = None) -> FastAPI:
    """App factory; db_path None -> service.DB_PATH."""
    app = FastAPI(title="Tuklas Agent API", version="v1")
    app.state.db_path = db_path or service.DB_PATH
    app.include_router(router)

    @app.exception_handler(errors.ApiError)
    async def api_error_handler(request: Request, err: errors.ApiError) -> JSONResponse:
        return JSONResponse(status_code=err.status, content=err.body())

    @app.exception_handler(Exception)
    async def internal_error_handler(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=500,
            content={"error": {"code": "INTERNAL_ERROR", "message": "internal server error", "details": {}}},
        )

    return app


app = create_app()
