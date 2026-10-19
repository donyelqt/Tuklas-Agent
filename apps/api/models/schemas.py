"""Frozen API models (api.md §2) — pydantic v2, additive-only under /v1."""

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
