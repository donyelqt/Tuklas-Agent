"""Day-1 ingestion test suite (docs/PLAN.md Day 1 verification block).

Covers: real-statute end-to-end ingest, D1 id invariants, flat fallback,
the api.md §1 error matrix, the /v1/documents route contract, and the
ids.py grammar unit cases. `pytest -k ingest` selects every test here.

Synthetic PDFs are built in-test with PyMuPDF (fitz); no network access.
"""

from __future__ import annotations

import re
from pathlib import Path
from types import ModuleType
from typing import cast

import pytest
from apps.api.api.app import create_app
from apps.api.ingestion import ids
from apps.api.ingestion.errors import ApiError
from apps.api.ingestion.parser import parse_document
from apps.api.ingestion.service import get_document, get_tree, ingest_pdf

REPO_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = REPO_ROOT / "data" / "documents"
STATUTES = [
    (
        "civil-code-ra386-icnl.pdf",
        "civil-code-public",
        167,
    ),
    (
        "uscode-2023-title11.pdf",
        "usc-title-11",
        334,
    ),
]

NODE_ID_RE = re.compile(
    r"^node_[a-z0-9]+(-[a-z0-9]+)*_"
    r"(document|book|title|part|division|chapter|section|article|subsection|page)"
    r"(_[a-z0-9]+(-[a-z0-9]+)*)?(_d\d+)?$"
)


def _fitz() -> ModuleType:
    import fitz

    return cast(ModuleType, fitz)


# ---------------------------------------------------------------------------
# Synthetic PDF fixtures (built with PyMuPDF, no network)
# ---------------------------------------------------------------------------


def _write_pdf(path: Path, pages: list[str], *, encrypt: bool = False) -> None:
    fitz = _fitz()
    doc = fitz.open()
    for text in pages:
        page = doc.new_page()
        if text:
            page.insert_text((72, 72), text, fontsize=12)
    if encrypt:
        doc.save(path, encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw="user")
    else:
        doc.save(path)
    doc.close()


def _heading_pdf_text(page_no: int) -> str:
    # ≥ 1 heading-shaped line per page so parse_confidence stays high.
    return f"CHAPTER {page_no}\n\nArt. {100 + page_no}. Provision text for page {page_no}. " * 3


@pytest.fixture
def synthetic_pdf(tmp_path: Path) -> Path:
    path = tmp_path / "synthetic.pdf"
    _write_pdf(path, [_heading_pdf_text(i) for i in range(1, 4)])
    return path


@pytest.fixture
def encrypted_pdf(tmp_path: Path) -> Path:
    path = tmp_path / "encrypted.pdf"
    _write_pdf(path, ["Encrypted content page one. " * 20], encrypt=True)
    return path


@pytest.fixture
def mostly_empty_pdf(tmp_path: Path) -> Path:
    # 4 pages, 2 empty → empty ratio 0.5 > 0.30 → OCR_REQUIRED.
    path = tmp_path / "mostly-empty.pdf"
    _write_pdf(
        path,
        [_heading_pdf_text(1), "", _heading_pdf_text(3), ""],
    )
    return path


@pytest.fixture
def flat_pdf(tmp_path: Path) -> Path:
    # Prose-only pages: no heading-shaped lines → parse_confidence < 0.5 → flat fallback.
    path = tmp_path / "flat.pdf"
    prose = (
        "This document contains running prose only. No chapter, article, section, "
        "title or book headings appear anywhere in the text layer. "
    ) * 6
    _write_pdf(path, [prose for _ in range(3)])
    return path


def _leaf_page_spans_complete(tree: dict) -> bool:
    def walk(node: dict) -> bool:
        children = node.get("children") or []
        if not children:
            if node.get("type") == "document":
                return True
            return node.get("page_start") is not None and node.get("page_end") is not None
        return all(walk(child) for child in children)

    return walk(tree)


def _flatten(tree: dict) -> list[dict]:
    out = [tree]
    for child in tree.get("children") or []:
        out.extend(_flatten(child))
    return out


# ---------------------------------------------------------------------------
# 1. Real statutes end-to-end
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "file_name,slug,expected_pages",
    STATUTES,
    ids=["ingest-civil-code", "ingest-usc-title11"],
)
def test_ingest_real_statutes_end_to_end(
    file_name: str, slug: str, expected_pages: int, tmp_path: Path
) -> None:
    pdf_path = DATA_DIR / file_name
    if not pdf_path.exists():
        pytest.skip(f"corpus file missing: {pdf_path}")

    db_path = tmp_path / "docs.db"
    uploaded = ingest_pdf(str(pdf_path), slug=slug, db_path=str(db_path))

    assert uploaded.id.startswith("doc_")
    assert uploaded.slug == slug
    assert uploaded.page_count == expected_pages
    assert 0.0 <= uploaded.parse_confidence <= 1.0
    assert uploaded.tree_root.type == "document"
    assert uploaded.tree_root.child_count > 0

    tree = get_tree(slug, depth=10, db_path=str(db_path))
    root = tree.root.model_dump()
    assert root["type"] == "document"
    assert root["child_count"] > 0
    assert _leaf_page_spans_complete(root), "100% of leaf nodes must carry page spans"

    detail = get_document(slug, db_path=str(db_path))
    assert detail.page_count == expected_pages


# ---------------------------------------------------------------------------
# 2. D1 invariants
# ---------------------------------------------------------------------------


def _ingest_civil_code(tmp_path: Path, name: str) -> Path:
    pdf_path = DATA_DIR / "civil-code-ra386-icnl.pdf"
    if not pdf_path.exists():
        pytest.skip(f"corpus file missing: {pdf_path}")
    db_path = tmp_path / f"{name}.db"
    ingest_pdf(str(pdf_path), slug="civil-code-public", db_path=str(db_path))
    return db_path


def _fetch_all_nodes(db_path: Path) -> list[dict]:
    import sqlite3

    conn = sqlite3.connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, document_id, parent_id, type FROM nodes"
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def test_d1_invariants(tmp_path: Path) -> None:
    db_path = _ingest_civil_code(tmp_path, "d1a")
    nodes = _fetch_all_nodes(db_path)
    assert nodes, "ingest produced no nodes"

    all_ids = [n["id"] for n in nodes]
    for node_id_str in all_ids:
        assert NODE_ID_RE.fullmatch(node_id_str), f"id violates D1 grammar: {node_id_str}"

    assert len(all_ids) == len(set(all_ids)), "node ids must be unique"

    root_candidates = [n for n in nodes if n["type"] == "document"]
    assert len(root_candidates) == 1
    root = root_candidates[0]
    assert root["id"] == "node_civil-code-public_document"

    for node in nodes:
        if node["id"] == root["id"]:
            continue
        tokens = node["id"].split("_")
        assert tokens[1] == "civil-code-public", f"token2 (slug) != slug: {node['id']}"
        assert tokens[2] == node["type"], f"token3 (type) != type: {node['id']}"

    # Rebuild determinism: same file into a second tmp db → identical sorted id list.
    db_path2 = _ingest_civil_code(tmp_path, "d1b")
    ids_second = sorted(n["id"] for n in _fetch_all_nodes(db_path2))
    assert sorted(all_ids) == ids_second, "re-ingest must reproduce the id set exactly"


# ---------------------------------------------------------------------------
# 3. Flat fallback fixture
# ---------------------------------------------------------------------------


def test_flat_fallback_fixture(flat_pdf: Path, tmp_path: Path) -> None:
    slug = "flat-fixture"
    db_path = tmp_path / "flat.db"
    result = parse_document(str(flat_pdf), slug)

    assert result.fallback is True
    assert 0.0 <= result.parse_confidence < 0.5

    # Via full ingest so the stored tree is exercised too.
    ingest_pdf(str(flat_pdf), slug=slug, db_path=str(db_path))
    tree = get_tree(slug, depth=10, db_path=str(db_path))
    root = tree.root.model_dump()

    flat_nodes = _flatten(root)
    assert flat_nodes[0]["type"] == "document"
    page_nodes = flat_nodes[1:]
    assert page_nodes, "flat fallback must produce page nodes"
    for node in page_nodes:
        assert node["type"] == "page", f"flat fallback must contain only page nodes: {node['id']}"

    # page node ids: node_<slug>_page_<n>
    for index, node in enumerate(page_nodes, start=1):
        assert node["id"] == f"node_{slug}_page_{index}"


# ---------------------------------------------------------------------------
# 4. Error matrix (api.md §1)
# ---------------------------------------------------------------------------


def _expect_api_error(exc_info: pytest.ExceptionInfo[ApiError], code: str, status: int) -> None:
    err = exc_info.value
    assert isinstance(err, ApiError)
    assert err.code == code, f"expected {code}, got {err.code}: {err.message}"
    assert err.status == status


def test_error_matrix_duplicate_and_slug_conflicts(synthetic_pdf: Path, tmp_path: Path) -> None:
    db_path = tmp_path / "errors.db"
    ingest_pdf(str(synthetic_pdf), slug="matrix-doc", db_path=str(db_path))

    # Identical bytes re-upload → 409 CONFLICT.
    with pytest.raises(ApiError) as exc_info:
        ingest_pdf(str(synthetic_pdf), slug="matrix-doc", db_path=str(db_path))
    _expect_api_error(exc_info, "CONFLICT", 409)

    # Different bytes, same slug → 409 CONFLICT.
    other = tmp_path / "other.pdf"
    _write_pdf(other, [_heading_pdf_text(9) for _ in range(3)])
    with pytest.raises(ApiError) as exc_info:
        ingest_pdf(str(other), slug="matrix-doc", db_path=str(db_path))
    _expect_api_error(exc_info, "CONFLICT", 409)


def test_error_matrix_encrypted(encrypted_pdf: Path, tmp_path: Path) -> None:
    with pytest.raises(ApiError) as exc_info:
        ingest_pdf(str(encrypted_pdf), slug="enc-doc", db_path=str(tmp_path / "enc.db"))
    _expect_api_error(exc_info, "PARSE_FAILED", 422)


def test_error_matrix_ocr_required(mostly_empty_pdf: Path, tmp_path: Path) -> None:
    # Empty ratio 0.5 (2/4 pages empty) > 0.30 → OCR_REQUIRED 422.
    with pytest.raises(ApiError) as exc_info:
        ingest_pdf(str(mostly_empty_pdf), slug="empty-doc", db_path=str(tmp_path / "empty.db"))
    _expect_api_error(exc_info, "OCR_REQUIRED", 422)


def test_error_matrix_slug_charset(synthetic_pdf: Path, tmp_path: Path) -> None:
    with pytest.raises(ApiError) as exc_info:
        ingest_pdf(str(synthetic_pdf), slug="Bad_Slug!", db_path=str(tmp_path / "bad.db"))
    _expect_api_error(exc_info, "VALIDATION_ERROR", 422)


def test_error_matrix_not_found(tmp_path: Path) -> None:
    with pytest.raises(ApiError) as exc_info:
        get_document("doc_doesnotexist", db_path=str(tmp_path / "nf.db"))
    _expect_api_error(exc_info, "NOT_FOUND", 404)


# ---------------------------------------------------------------------------
# 5. API route contract
# ---------------------------------------------------------------------------


def test_api_routes_contract(synthetic_pdf: Path, tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    app = create_app(db_path=str(tmp_path / "routes.db"))
    client = TestClient(app)

    with synthetic_pdf.open("rb") as fh:
        response = client.post(
            "/v1/documents",
            files={"file": ("synthetic.pdf", fh, "application/pdf")},
            data={"slug": "routes-doc"},
        )
    assert response.status_code == 201, response.text
    body = response.json()
    for key in ("id", "slug", "name", "page_count", "parse_confidence", "tree_root"):
        assert key in body, f"201 body missing {key}"
    assert body["id"].startswith("doc_")
    assert body["tree_root"]["type"] == "document"

    # Duplicate upload → 409 CONFLICT with uniform error body.
    with synthetic_pdf.open("rb") as fh:
        dup = client.post(
            "/v1/documents",
            files={"file": ("synthetic.pdf", fh, "application/pdf")},
            data={"slug": "routes-doc"},
        )
    assert dup.status_code == 409, dup.text
    assert dup.json()["error"]["code"] == "CONFLICT"

    listing = client.get("/v1/documents")
    assert listing.status_code == 200
    listing_body = listing.json()
    assert isinstance(listing_body["data"], list)
    page = listing_body["pagination"]
    assert page["page"] == 1
    assert page["page_size"] >= 1
    assert page["total_items"] >= 1
    assert page["total_pages"] >= 1

    missing = client.get("/v1/documents/doc_nope")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "NOT_FOUND"

    tree_resp = client.get("/v1/documents/routes-doc/tree", params={"depth": 2})
    assert tree_resp.status_code == 200, tree_resp.text
    tree_body = tree_resp.json()
    assert tree_body["root"]["type"] == "document"
    assert isinstance(tree_body["children"], list)
    assert len(tree_body["children"]) > 0

    bad_page_size = client.get("/v1/documents", params={"pageSize": 0})
    assert bad_page_size.status_code == 422
    assert bad_page_size.json()["error"]["code"] == "VALIDATION_ERROR"

    bad_depth = client.get("/v1/documents/routes-doc/tree", params={"depth": 0})
    assert bad_depth.status_code == 422
    assert bad_depth.json()["error"]["code"] == "VALIDATION_ERROR"


# ---------------------------------------------------------------------------
# 6. ids.py grammar
# ---------------------------------------------------------------------------


def test_ids_grammar() -> None:
    assert ids.slugify("Civil Code (R.A. 386)") == "civil-code-r-a-386"
    assert ids.canonical_locator("2000(a)") == "2000-a"
    assert ids.canonical_locator("IV") == "iv"

    bare = ids.node_id("civil-code-public", "chapter", "1")
    assert bare == "node_civil-code-public_chapter_1"
    assert ids.dedup_suffix(bare, 1) == bare
    assert ids.dedup_suffix(bare, 2) == bare + "_d2"

    # Valid cases across types.
    assert ids.node_id("usc-title-11", "document", None) == "node_usc-title-11_document"
    assert ids.node_id("a", "page", "12") == "node_a_page_12"

    # Charset violations raise ValueError.
    with pytest.raises(ValueError):
        ids.node_id("Bad_Slug", "chapter", "1")
    with pytest.raises(ValueError):
        ids.node_id("ok-slug", "chapter", "1_2")
    with pytest.raises(ValueError):
        ids.node_id("ok-slug", "not-a-type", "1")
    with pytest.raises(ValueError):
        ids.node_id("ok-slug", "chapter", None)
    with pytest.raises(ValueError):
        ids.node_id("ok-slug", "document", "extra")
    assert ids.slugify("!!!") == ""  # empty result: the CALLER (service) raises 422


def test_error_matrix_oversize_page_gate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The >500-pages gate, unit-tested at the parse seam.

    Synthesizing a >500-page PDF is too slow for this suite, so the gate is exercised
    by stubbing ``parser.extract_pages`` — the exact function ``service.ingest_pdf``
    calls — to return 501 pages. The service-side branch (page_count > 500 → 422
    VALIDATION_ERROR) is the code under test, not the PDF writing.
    """
    from apps.api.ingestion import parser

    monkeypatch.setattr(parser, "extract_pages", lambda _path: ["page"] * 501)
    tiny = tmp_path / "tiny.pdf"
    tiny.write_bytes(b"%PDF-1.4 stub")  # never opened: extract_pages is stubbed
    with pytest.raises(ApiError) as exc_info:
        ingest_pdf(str(tiny), slug="oversize-doc", db_path=str(tmp_path / "big.db"))
    _expect_api_error(exc_info, "VALIDATION_ERROR", 422)


def test_parser_ignores_marginal_notes() -> None:
    """A marginal note is not a heading.

    The Civil Code PDF prints side notes like "article 765. (n)" beside a
    provision. Read as a heading, each one creates a SECOND node for a
    provision that is printed in full elsewhere, and the label stops
    identifying anything: measured on the corpus, "art 765", "art 895",
    "art 1544", "art 1984" and "art 2168" each resolved to two nodes.

    The rule is a label, a period, and then ONLY a parenthesised marker. A
    real provision carries text after the period and must still be a heading.
    """
    from apps.api.ingestion import parser

    # The marginal notes the Civil Code actually prints: not headings.
    for note in (
        "article 765. (n)",
        "article 895. (n)",
        "article 1544. (n)",
        "article 1984. (n)",
        "article 2168. (1782)",
    ):
        assert parser._match_heading(note, 0, 0) is None, (
            f"{note!r} is a marginal note and must not become a heading"
        )

    # A real provision, even one that ends in a parenthesised marker, IS a
    # heading. This is the false-positive side of the rule.
    for real in (
        "Article 1320. An acceptance may be express or implied. (n)",
        "Article 1318. There is no contract unless the following requisites concur:",
        "SECTION 1",
        "CHAPTER 2",
        "BOOK I",
        "TITLE IV",
    ):
        assert parser._match_heading(real, 0, 0) is not None, (
            f"{real!r} is a real heading and must still be matched"
        )


def test_parser_ignores_body_text_headings() -> None:
    """Body text is not a heading, even when it starts with a family word.

    The RapidFuzz near-miss path used to turn body sentences into headings:
    "party regarding the solemnization of the marriage in a place other than
    those mentioned in article 57 ..." became a 'part' node, and the 402
    articles under it inherited the label "art 57". A heading's number must
    be terminated by a period or the end of the line; a number followed by a
    word is part of a sentence.
    """
    from apps.api.ingestion import parser

    for body in (
        "party regarding the solemnization of the marriage in a place other than "
        "those mentioned in article 57 if there be any such affidavit, in",
        "party wall supports a building belonging to him. (575)",
        "division of a thing owned in common by two or more persons. (541a)",
        "at the expense of the usufructuary. (500)",
        "articles 74 and 75. (4a)",
        "articles 232 and 243.",
    ):
        assert parser._match_heading(body, 0, 0) is None, (
            f"{body!r} is body text and must not become a heading"
        )
