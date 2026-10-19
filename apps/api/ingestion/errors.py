"""Typed error taxonomy for the ingestion path (api.md §1 error table).

Codes and statuses are frozen by api.md §1; the transport layer (apps/api/api/routes.py)
maps these onto the uniform error body `{ "error": { "code", "message", "details" } }`.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ApiError(Exception):
    """One API failure with its frozen code/status pair (api.md §1).

    The dataclass gives typed fields and the uniform ``body()``; it is deliberately NOT
    ``frozen=True`` — a frozen dataclass blocks the exception machinery's
    ``__traceback__`` assignment, so raising the error itself would crash with
    ``FrozenInstanceError``. api.md freezes the code/status *values*, not instance
    mutability.
    """

    code: str
    message: str
    status: int
    details: dict[str, str] = field(default_factory=dict)

    def body(self) -> dict[str, object]:
        """The uniform error body every endpoint returns on failure."""
        return {"error": {"code": self.code, "message": self.message, "details": self.details}}


def validation_error(message: str, **details: str) -> ApiError:
    """422 — bad body, non-PDF, oversize, >500 pages, empty slug."""
    return ApiError("VALIDATION_ERROR", message, 422, dict(details))


def ocr_required(message: str, **details: str) -> ApiError:
    """422 — >30% of pages carry <50 extracted chars (includes fully empty text layer)."""
    return ApiError("OCR_REQUIRED", message, 422, dict(details))


def parse_failed(message: str, **details: str) -> ApiError:
    """422 — PyMuPDF cannot open/extract at all (corrupt, encrypted-unreadable)."""
    return ApiError("PARSE_FAILED", message, 422, dict(details))


def not_found(message: str, **details: str) -> ApiError:
    """404 — unknown document/node id."""
    return ApiError("NOT_FOUND", message, 404, dict(details))


def conflict(message: str, **details: str) -> ApiError:
    """409 — identical file_hash re-upload, or duplicate slug (hash and slug live ONLY here)."""
    return ApiError("CONFLICT", message, 409, dict(details))
