"""Ingestion service — the POST /v1/documents pipeline minus HTTP (api.md §2).

Pipeline order is binding (architecture.md §3 + api.md §2): slug validation,
slug-conflict check, size gate, SHA-256 hash + hash-conflict check, page
extraction, page-count / empty-page gates, structural parse, then one
transactional write of the document row and all node rows.
"""

from __future__ import annotations

import hashlib
import math
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from apps.api.ingestion import db, errors, ids, parser
from apps.api.models.schemas import (
    DocumentDetail,
    DocumentList,
    DocumentSummary,
    DocumentTree,
    DocumentUploaded,
    Pagination,
    TreeNode,
    TreeRoot,
)

DB_PATH = "data/tuklas.sqlite"

_MAX_FILE_BYTES = 100 * 1024 * 1024  # 100 MB (api.md §1 oversize limit)
_MAX_PAGES = 500
_EMPTY_PAGE_CHARS = 50
_MAX_EMPTY_RATIO = 0.30

_SLUG_CHARSET_RE = r"[a-z0-9]+(-[a-z0-9]+)*"


def _validate_slug(slug: str) -> str:
    """Raise 422 unless the slug is non-empty, in the D1 charset, and not a reserved type word."""
    if not slug:
        raise errors.validation_error("empty slug")
    if not re.fullmatch(_SLUG_CHARSET_RE, slug):
        raise errors.validation_error(
            "slug violates D1 charset (lowercase [a-z0-9] with internal single hyphens)",
            slug=slug,
        )
    if slug in ids.NODE_TYPES:
        raise errors.validation_error("slug is a reserved node type word", slug=slug)
    return slug


def ingest_pdf(
    pdf_path: str, *, slug: str | None = None, db_path: str = DB_PATH
) -> DocumentUploaded:
    """Full ingest pipeline; raises errors.ApiError subclasses (api.md §1 statuses)."""
    path = Path(pdf_path)
    doc_slug = _validate_slug(slug if slug is not None else ids.slugify(path.stem))

    # 2. Slug conflict lives ONLY here, BEFORE parsing (api.md §2).
    with db.connect(db_path) as conn:
        if conn.execute(
            "SELECT id FROM documents WHERE slug = ?", (doc_slug,)
        ).fetchone() is not None:
            raise errors.conflict(
                "slug already in use by another document", slug=doc_slug
            )

    # 3. Size gate before reading anything into memory.
    size = path.stat().st_size
    if size > _MAX_FILE_BYTES:
        raise errors.validation_error(
            "file exceeds the 100 MB limit",
            size_bytes=str(size),
            max_bytes=str(_MAX_FILE_BYTES),
        )

    # 4. SHA-256 dedup on raw bytes.
    file_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    with db.connect(db_path) as conn:
        if conn.execute(
            "SELECT id, slug FROM documents WHERE file_hash = ?", (file_hash,)
        ).fetchone() is not None:
            raise errors.conflict("identical file already ingested", file_hash=file_hash)

    # 5. Page extraction; PARSE_FAILED propagates as-is from parser.
    pages = parser.extract_pages(pdf_path)

    # 6. Page-count and empty-page gates.
    page_count = len(pages)
    if page_count > _MAX_PAGES:
        raise errors.validation_error(
            "document exceeds the 500-page limit", page_count=str(page_count)
        )
    empty_pages = sum(1 for text in pages if len(text.strip()) < _EMPTY_PAGE_CHARS)
    empty_ratio = empty_pages / page_count if page_count else 1.0
    if empty_ratio > _MAX_EMPTY_RATIO:
        raise errors.ocr_required(
            "too many pages have no extractable text layer; OCR required",
            empty_pages=str(empty_pages),
            page_count=str(page_count),
            empty_ratio=f"{empty_ratio:.4f}",
        )

    # 7. Structural parse.
    result = parser.parse_document(pdf_path, doc_slug)

    # 8-9. Persist document + all nodes in one transaction (db.connect commits).
    doc_id = "doc_" + file_hash[:8]
    created_at = datetime.now(UTC).isoformat()
    root = result.nodes[0]
    children_by_parent: dict[str | None, list[parser.ParsedNode]] = {}
    for node in result.nodes:
        children_by_parent.setdefault(node.parent_id, []).append(node)

    with db.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO documents (id, slug, name, file_path, file_hash, page_count, "
            "parse_confidence, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                doc_id,
                doc_slug,
                path.name,
                pdf_path,
                file_hash,
                page_count,
                result.parse_confidence,
                created_at,
            ),
        )
        for node in result.nodes:
            conn.execute(
                "INSERT INTO nodes (id, document_id, parent_id, type, title, text, "
                "page_start, page_end, depth, position) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    node.node_id,
                    doc_id,
                    node.parent_id,
                    node.type,
                    node.title,
                    node.text,
                    node.page_start,
                    node.page_end,
                    node.depth,
                    node.position,
                ),
            )
        # Day 1: no reference parsing; references_ table stays empty.

    # 10. Response shape.
    root_children = children_by_parent.get(root.node_id, [])
    return DocumentUploaded(
        id=doc_id,
        slug=doc_slug,
        name=path.name,
        page_count=page_count,
        parse_confidence=result.parse_confidence,
        tree_root=TreeRoot(
            id=root.node_id,
            type=root.type,
            title=root.title,
            child_count=len(root_children),
        ),
    )


def _resolve_document(conn: sqlite3.Connection, doc_id_or_slug: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM documents WHERE id = ? OR slug = ?", (doc_id_or_slug, doc_id_or_slug)
    ).fetchone()
    if row is None:
        raise errors.not_found("unknown document", identifier=doc_id_or_slug)
    typed_row: sqlite3.Row = row
    return typed_row


def get_document(
    doc_id_or_slug: str, db_path: str = DB_PATH
) -> DocumentDetail:
    """200 body for GET /v1/documents/{id} — accepts doc_<hash8> id or slug."""
    with db.connect(db_path) as conn:
        row = _resolve_document(conn, doc_id_or_slug)
    return DocumentDetail(
        id=row["id"],
        name=row["name"],
        file_hash=row["file_hash"],
        page_count=row["page_count"],
        parse_confidence=row["parse_confidence"],
        created_at=row["created_at"],
    )


def list_documents(
    page: int = 1, page_size: int = 20, db_path: str = DB_PATH
) -> DocumentList:
    """200 body for GET /v1/documents — 1-indexed, pageSize clamped to [1, 100]."""
    page = max(1, page)
    page_size = min(100, max(1, page_size))
    with db.connect(db_path) as conn:
        total = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        rows = conn.execute(
            "SELECT id, name, page_count, created_at FROM documents "
            "ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (page_size, (page - 1) * page_size),
        ).fetchall()
    total_pages = math.ceil(total / page_size) if total else 0
    return DocumentList(
        data=[
            DocumentSummary(
                id=r["id"], name=r["name"], page_count=r["page_count"], created_at=r["created_at"]
            )
            for r in rows
        ],
        pagination=Pagination(
            page=page,
            page_size=page_size,
            total_items=total,
            total_pages=total_pages,
        ),
    )


def _build_tree_node(
    row: sqlite3.Row,
    children_by_parent: dict[str | None, list[sqlite3.Row]],
    included_levels: int,
) -> TreeNode:
    """Build nested TreeNode; depth param counts node LEVELS with root at level 1."""
    child_rows = children_by_parent.get(row["id"], [])
    children: list[TreeNode] = []
    if row["depth"] + 1 < included_levels:
        children = [
            _build_tree_node(child, children_by_parent, included_levels)
            for child in sorted(child_rows, key=lambda r: r["position"])
        ]
    return TreeNode(
        id=row["id"],
        type=row["type"],
        title=row["title"],
        depth=row["depth"],
        position=row["position"],
        page_start=row["page_start"],
        page_end=row["page_end"],
        child_count=len(child_rows),
        children=children,
    )


def get_tree(
    doc_id_or_slug: str, depth: int = 3, db_path: str = DB_PATH
) -> DocumentTree:
    """200 body for GET /v1/documents/{id}/tree?depth=N (root = level 1)."""
    with db.connect(db_path) as conn:
        doc = _resolve_document(conn, doc_id_or_slug)
        nodes = conn.execute(
            "SELECT * FROM nodes WHERE document_id = ? ORDER BY depth, position",
            (doc["id"],),
        ).fetchall()

    if not nodes:
        raise errors.not_found("document has no parse tree", identifier=doc_id_or_slug)

    children_by_parent: dict[str | None, list[sqlite3.Row]] = {}
    for node in nodes:
        children_by_parent.setdefault(node["parent_id"], []).append(node)

    root_row = nodes[0]
    root = _build_tree_node(root_row, children_by_parent, max(1, depth))
    return DocumentTree(root=root, children=root.children)
