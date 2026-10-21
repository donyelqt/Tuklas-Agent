"""The /v1 retrieval & asking routes, against the frozen contract (api.md §3).

Day 5 deliverable: the five routes api.md §3 declares are exposed over HTTP,
and their responses are the frozen shapes.

The suite is model-free. The ask surface defaults to the real local LLM
(ADR-004), so every test here either drives the agent with the deterministic
stub or points the generator at a dead backend. No test needs a live model,
and no test needs a network.

The document is a SYNTHETIC PDF built with PyMuPDF (six articles, each with
its own topic), so the suite runs without the corpus. The corpus PDFs are
not committed to the repository (ADR-002: they never leave the machine), so
a suite that depends on them would skip itself on a fresh clone.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest
from apps.api.agent.graph import CANNED_ABSTENTION
from apps.api.api.app import create_app
from fastapi.testclient import TestClient

#: The frozen event names (api.md §3).
SSE_EVENTS = ("plan", "tool_call", "tool_result", "evidence", "token", "done", "error")

#: The six fields of a search hit (api.md §3).
HIT_FIELDS = {"node_id", "title", "snippet", "score", "components", "page_start"}

#: The seven required AskResponse keys, plus the optional one (api.md §3).
ASK_REQUIRED_KEYS = {
    "answer_markdown",
    "citations",
    "evidence_ids",
    "iterations",
    "latency_ms",
    "tokens",
    "insufficient",
}


def _fitz() -> ModuleType:
    import fitz

    return cast(ModuleType, fitz)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

#: Six articles, each with its own topic. Distinct topics matter: the
#: retrieval layer must be able to tell them apart, and the agent's gate
#: must find a supporter for the question's own topic.
_ARTICLES: list[tuple[str, str]] = [
    (
        "Article 1. Requisites of contracts.",
        "The requisites of contracts are consent, object, and certain. A contract requires "
        "the consent of the parties, a lawful object, and certainty of the terms. "
        "The requisites of contracts are set out in this article. ",
    ),
    (
        "Article 2. Succession.",
        "Succession is the transfer of the rights and obligations of a deceased person to "
        "the heirs or legatees. The rules of succession follow the civil code. ",
    ),
    (
        "Article 3. Registration of chattel mortgages.",
        "A chattel mortgage is registered with the registry of chattel mortgages so that "
        "the creditor is protected against third parties. ",
    ),
    (
        "Article 4. Prescription.",
        "Prescription extinguishes a claim after the period laid down by law. The periods "
        "of prescription are enumerated in this article. ",
    ),
    (
        "Article 5. Damages.",
        "Damages are the sum of money which the court orders the defendant to pay. The "
        "kinds of damages and the manner of their assessment are provided here. ",
    ),
    (
        "Article 6. Bailment.",
        "Bailment is the delivery of personal property to another upon a contract that "
        "the bailee shall return it. The duties of the bailee are set out here. ",
    ),
]


def _write_pdf(path: Path) -> None:
    """The synthetic statute: one page per article, a heading and a body."""
    fitz = _fitz()
    doc = fitz.open()
    for heading, body in _ARTICLES:
        page = doc.new_page()
        page.insert_textbox(
            fitz.Rect(50, 50, 560, 780), heading + "\n" + body * 3, fontsize=11
        )
    doc.save(path)
    doc.close()


@pytest.fixture(scope="module")
def corpus(tmp_path_factory: pytest.TempPathFactory) -> Any:
    """A synthetic document, ingested, indexed, and ready to query.

    The per-document BM25 artifact is the runtime lex path (retrieval.md §2),
    so the index is built and the scoring module is pointed at it - the same
    two steps the agent's own test fixture performs.
    """
    import shutil

    import apps.api.retrieval.scoring as scoring
    from apps.api.ingestion.service import ingest_pdf
    from apps.api.retrieval.index import build_indexes

    root = tmp_path_factory.mktemp("routes")
    pdf_path = root / "synthetic.pdf"
    _write_pdf(pdf_path)

    db_path = str(root / "corpus.db")
    document = ingest_pdf(str(pdf_path), slug="synth-doc", db_path=db_path)
    index_root = root / "indexes"
    build_indexes(db_path, document.id, index_root=index_root)

    original_root = scoring.INDEX_ROOT
    scoring.INDEX_ROOT = index_root
    try:
        yield {
            "db_path": db_path,
            "doc_id": document.id,
            "slug": "synth-doc",
            "article_node_id": "node_synth-doc_article_1",
        }
    finally:
        scoring.INDEX_ROOT = original_root
        shutil.rmtree(root, ignore_errors=True)


@pytest.fixture
def client(corpus: Any) -> TestClient:
    """A test client bound to the fixture's temporary database."""
    return TestClient(create_app(db_path=corpus["db_path"]))


@pytest.fixture(autouse=True)
def _model_free_generator(monkeypatch: pytest.MonkeyPatch) -> None:
    """The default generator is the model-free stub.

    The ask surface defaults to the real local LLM (ADR-004), so without this
    the ask tests would call a model. The stub is the deterministic
    stand-in; the tests that need a REAL backend failure point the generator
    at a dead address instead.
    """
    from apps.api.agent import ask as ask_module
    from apps.api.agent import graph as graph_module
    from apps.api.agent.graph import StubGenerator

    monkeypatch.setattr(ask_module, "_default_generator", StubGenerator)
    monkeypatch.setattr(graph_module, "_default_generator", StubGenerator)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_sse(body: str) -> list[tuple[str, dict[str, Any]]]:
    """The SSE frames of a response body as (event name, payload) pairs.

    The parser is strict on purpose: it reads the ``event:`` line and the
    ``data:`` line of each frame, and it FAILS on a frame the contract does
    not describe (a missing event name, a missing data line, a data line that
    is not one JSON object). A lenient parser would let a malformed frame
    pass the test that is supposed to pin the wire format.
    """
    frames: list[tuple[str, dict[str, Any]]] = []
    for block in body.split("\n\n"):
        if not block.strip():
            continue
        name: str | None = None
        data_lines: list[str] = []
        for line in block.split("\n"):
            if line.startswith("event:"):
                name = line[len("event:") :].strip()
            elif line.startswith("data:"):
                data_lines.append(line[len("data:") :].strip())
            else:
                raise AssertionError(f"unrecognised SSE line: {line!r}")
        assert name is not None, f"an SSE frame has no event name: {block!r}"
        assert name in SSE_EVENTS, f"unknown SSE event name: {name!r}"
        assert len(data_lines) == 1, (
            f"an SSE frame must carry exactly one data line: {block!r}"
        )
        payload = json.loads(data_lines[0])
        assert isinstance(payload, dict), f"the SSE payload is not an object: {payload!r}"
        assert "event" not in payload, (
            "the payload is the schema object; the event name travels in the "
            f"event: line, not inside the JSON: {payload!r}"
        )
        frames.append((name, payload))
    return frames


def _assert_error_body(body: Any, code: str) -> None:
    """The frozen error body: exactly {error: {code, message, details}}."""
    assert isinstance(body, dict), f"the error body is not an object: {body!r}"
    assert set(body.keys()) == {"error"}, f"the error body has extra keys: {body!r}"
    payload = body["error"]
    assert isinstance(payload, dict)
    assert set(payload.keys()) == {"code", "message", "details"}
    assert payload["code"] == code
    assert isinstance(payload["message"], str) and payload["message"]
    assert isinstance(payload["details"], dict)


# ---------------------------------------------------------------------------
# 1. POST /v1/search
# ---------------------------------------------------------------------------


def test_search_returns_the_frozen_hit_shape(client: TestClient, corpus: Any) -> None:
    response = client.post(
        "/v1/search",
        json={"document_id": corpus["doc_id"], "query": "requisites of contracts"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body.keys()) == {"hits", "trace_id"}, body.keys()
    # api.md §1: ids are q_<uuid8>.
    assert body["trace_id"].startswith("q_")
    assert len(body["trace_id"]) == len("q_") + 8
    assert body["hits"], "the synthetic corpus must produce at least one hit"
    for hit in body["hits"]:
        assert set(hit.keys()) == HIT_FIELDS, f"the hit shape drifted: {hit.keys()}"
        assert isinstance(hit["node_id"], str) and hit["node_id"]
        assert isinstance(hit["title"], str)
        assert isinstance(hit["snippet"], str)
        assert isinstance(hit["score"], float)
        assert set(hit["components"].keys()) == {"lex", "ident", "title", "ref", "hier"}
        # page_start is nullable: a container node (the document root) has no
        # page of its own.
        assert hit["page_start"] is None or isinstance(hit["page_start"], int)


def test_search_respects_top_k(client: TestClient, corpus: Any) -> None:
    response = client.post(
        "/v1/search",
        json={"document_id": corpus["doc_id"], "query": "contracts", "top_k": 1},
    )
    assert response.status_code == 200, response.text
    assert len(response.json()["hits"]) == 1


def test_search_works_on_a_document_ingested_through_the_upload_route(
    tmp_path: Path,
) -> None:
    """The product path: upload, then search, then ask.

    The upload route builds the retrieval indexes (architecture.md §2: the
    product wiring point is the route layer, because ingestion/ must not
    import retrieval/). A document that reached the store WITHOUT that step
    has no ``node_fts`` and no ``ident_index``, and every retrieval query
    against it fails.

    So the whole path is exercised here, against a real upload: the document
    is ingested over HTTP, and the search and ask routes are then able to
    answer for it. A test that builds the indexes by hand would miss a
    regression in that wiring.
    """
    pdf_path = tmp_path / "upload.pdf"
    _write_pdf(pdf_path)
    db_path = str(tmp_path / "uploaded.db")
    client = TestClient(create_app(db_path=db_path))

    with pdf_path.open("rb") as fh:
        uploaded = client.post(
            "/v1/documents",
            files={"file": ("upload.pdf", fh, "application/pdf")},
            data={"slug": "uploaded-doc"},
        )
    assert uploaded.status_code == 201, uploaded.text
    document_id = uploaded.json()["id"]

    search = client.post(
        "/v1/search",
        json={"document_id": document_id, "query": "requisites of contracts"},
    )
    assert search.status_code == 200, search.text
    assert search.json()["hits"], "the uploaded document must be searchable"

    ask = client.post(
        "/v1/ask",
        json={
            "document_id": document_id,
            "question": "What are the requisites of contracts?",
        },
    )
    assert ask.status_code == 200, ask.text
    assert ask.json()["insufficient"] is False

    # The document is also reachable by its slug, the way the explorer
    # addresses it.
    by_slug = client.post(
        "/v1/search",
        json={"document_id": "uploaded-doc", "query": "requisites of contracts"},
    )
    assert by_slug.status_code == 200, by_slug.text


def test_search_unknown_document_is_404(client: TestClient) -> None:
    response = client.post(
        "/v1/search", json={"document_id": "doc_nope", "query": "contracts"}
    )
    assert response.status_code == 404, response.text
    _assert_error_body(response.json(), "NOT_FOUND")


def test_search_works_on_a_document_whose_indexes_are_missing(
    tmp_path: Path,
) -> None:
    """A document ingested outside the upload route must still be queryable.

    The upload route builds the retrieval indexes (architecture.md §2: the
    product wiring point is the route layer, because ingestion/ must not
    import retrieval/). A document that reached the store another way - a
    database restored from a copy, an ingest performed by a script - has no
    ``node_fts`` and no ``ident_index``, and every retrieval query against it
    fails with "no such table: node_fts", which reaches the caller as a 500.

    The route layer owns that wiring, so the route layer repairs it: the
    indexes are rebuilt from the tree before the query runs. The rebuild is
    idempotent and derives everything from the nodes, so the indexes cannot
    disagree with the tree they describe.

    This is the defect the web explorer hit against the local store.
    """
    from apps.api.ingestion.service import ingest_pdf

    pdf_path = tmp_path / "no-index.pdf"
    _write_pdf(pdf_path)
    db_path = str(tmp_path / "no-index.db")
    document = ingest_pdf(str(pdf_path), slug="noindex-doc", db_path=db_path)

    # The store really has no indexes: the ingest did not build them.
    import sqlite3

    conn = sqlite3.connect(db_path)
    try:
        tables = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    finally:
        conn.close()
    assert "node_fts" not in tables, "the fixture must start without the indexes"

    client = TestClient(create_app(db_path=db_path))

    search = client.post(
        "/v1/search",
        json={"document_id": document.id, "query": "requisites of contracts"},
    )
    assert search.status_code == 200, search.text
    assert search.json()["hits"], "the document must be searchable"

    ask = client.post(
        "/v1/ask",
        json={
            "document_id": document.id,
            "question": "What are the requisites of contracts?",
        },
    )
    assert ask.status_code == 200, ask.text
    assert ask.json()["insufficient"] is False

    # The stream path too: the repair happens before the response starts, so
    # a missing index never arrives as a terminal SSE error.
    stream = client.post(
        "/v1/ask/stream",
        json={
            "document_id": document.id,
            "question": "What are the requisites of contracts?",
        },
    )
    assert stream.status_code == 200, stream.text
    names = [name for name, _payload in _parse_sse(stream.text)]
    assert names[-1] == "done", f"the stream did not finish: {names}"


@pytest.mark.parametrize("top_k", [0, 21, -1, 100])
def test_search_top_k_outside_the_range_is_422(
    client: TestClient, corpus: Any, top_k: int
) -> None:
    """The bound is ENFORCED, not clamped (api.md §3: top_k 1..20)."""
    response = client.post(
        "/v1/search",
        json={"document_id": corpus["doc_id"], "query": "contracts", "top_k": top_k},
    )
    assert response.status_code == 422, response.text
    _assert_error_body(response.json(), "VALIDATION_ERROR")


def test_search_malformed_body_is_422_with_the_frozen_shape(client: TestClient) -> None:
    """A malformed body is the frozen 422, not FastAPI's default {"detail": ...}."""
    response = client.post(
        "/v1/search",
        content=b"this is not json",
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422, response.text
    _assert_error_body(response.json(), "VALIDATION_ERROR")


def test_search_missing_field_is_422_with_the_frozen_shape(
    client: TestClient, corpus: Any
) -> None:
    response = client.post("/v1/search", json={"document_id": corpus["doc_id"]})
    assert response.status_code == 422, response.text
    _assert_error_body(response.json(), "VALIDATION_ERROR")


# ---------------------------------------------------------------------------
# 2. POST /v1/ask
# ---------------------------------------------------------------------------


def test_ask_returns_the_frozen_ask_response(client: TestClient, corpus: Any) -> None:
    response = client.post(
        "/v1/ask",
        json={
            "document_id": corpus["doc_id"],
            "question": "What are the requisites of contracts?",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body.keys()) >= ASK_REQUIRED_KEYS, body.keys()
    assert body["insufficient"] is False
    assert isinstance(body["answer_markdown"], str) and body["answer_markdown"]
    assert isinstance(body["citations"], list) and body["citations"]
    for citation in body["citations"]:
        assert set(citation.keys()) == {"n", "node_id", "label", "page_start", "page_end"}
    assert isinstance(body["iterations"], int)
    assert isinstance(body["latency_ms"], int)
    assert set(body["tokens"].keys()) == {"in", "out"}


def test_ask_unknown_document_is_404(client: TestClient) -> None:
    response = client.post(
        "/v1/ask", json={"document_id": "doc_nope", "question": "What is succession?"}
    )
    assert response.status_code == 404, response.text
    _assert_error_body(response.json(), "NOT_FOUND")


@pytest.mark.parametrize("max_iterations", [0, 13, -1, 100])
def test_ask_max_iterations_outside_the_range_is_422(
    client: TestClient, corpus: Any, max_iterations: int
) -> None:
    """The bound is ENFORCED, not clamped (api.md §3: max_iterations 1..12)."""
    response = client.post(
        "/v1/ask",
        json={
            "document_id": corpus["doc_id"],
            "question": "What are the requisites of contracts?",
            "max_iterations": max_iterations,
        },
    )
    assert response.status_code == 422, response.text
    _assert_error_body(response.json(), "VALIDATION_ERROR")


def test_ask_malformed_body_is_422_with_the_frozen_shape(client: TestClient) -> None:
    response = client.post(
        "/v1/ask",
        content=b"{not json",
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422, response.text
    _assert_error_body(response.json(), "VALIDATION_ERROR")


def test_ask_llm_down_is_503_not_a_fake_answer(
    client: TestClient, corpus: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A dead backend is a 503, never an answer (ADR-004, api.md §1).

    The generator points at a closed port, so the run fails the way a real
    backend outage fails - with no model and no network needed.
    """
    from apps.api.agent import ask as ask_module
    from apps.api.generation.config import build_generator

    dead = build_generator(base_url="http://127.0.0.1:1", timeout=1.0)
    monkeypatch.setattr(ask_module, "_default_generator", lambda: dead)

    response = client.post(
        "/v1/ask",
        json={
            "document_id": corpus["doc_id"],
            "question": "What are the requisites of contracts?",
        },
    )
    assert response.status_code == 503, response.text
    _assert_error_body(response.json(), "LLM_UNAVAILABLE")


# ---------------------------------------------------------------------------
# 3. POST /v1/ask/stream
# ---------------------------------------------------------------------------


def test_ask_stream_event_order_is_frozen(client: TestClient, corpus: Any) -> None:
    """The WHOLE event sequence, asserted as a sequence (api.md §3).

    Asserting membership would prove nothing: a batch/replay implementation
    produces exactly the same SET of events. What the contract freezes is the
    ORDER - ``plan`` first, then each ``tool_call`` IMMEDIATELY followed by
    its ``tool_result``, then ``evidence``, then the ``token`` events, then
    ``done`` last.

    The expected sequence is rebuilt from the observed one, so the test fails
    on ANY reordering: an ``evidence`` event in the middle of the tool
    stream, a ``token`` before ``evidence``, a ``tool_result`` whose
    ``tool_call`` is not the event before it, or a ``done`` that is not last.
    """
    response = client.post(
        "/v1/ask/stream",
        json={
            "document_id": corpus["doc_id"],
            "question": "What are the requisites of contracts?",
        },
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-accel-buffering"] == "no"

    frames = _parse_sse(response.text)
    names = [name for name, _payload in frames]

    tool_pairs = names.count("tool_call")
    token_count = names.count("token")
    expected = (
        ["plan"]
        + ["tool_call", "tool_result"] * tool_pairs
        + ["evidence"]
        + ["token"] * token_count
        + ["done"]
    )
    assert names == expected, f"the frozen event order changed:\n{names}\n!=\n{expected}"

    # The order is not vacuous: the run really did use tools and really did
    # stream tokens.
    assert tool_pairs > 0, "the run must exercise the tool stream"
    assert token_count > 0, "a passing run streams tokens"

    # Every tool_call is IMMEDIATELY followed by its own tool_result, and the
    # two name the same tool.
    for index, (name, payload) in enumerate(frames):
        if name != "tool_call":
            continue
        assert index + 1 < len(frames), "a tool_call must be followed by a tool_result"
        next_name, next_payload = frames[index + 1]
        assert next_name == "tool_result", (
            f"a tool_call must be immediately followed by its tool_result, "
            f"got {next_name}"
        )
        assert next_payload["tool"] == payload["tool"], (
            "a tool_result must belong to the tool_call before it"
        )

    # The payload of each event carries the frozen fields (api.md §3).
    for name, payload in frames:
        if name == "plan":
            assert set(payload.keys()) == {"sub_questions"}
            assert isinstance(payload["sub_questions"], list)
        elif name == "tool_call":
            assert set(payload.keys()) == {"tool", "args"}
            assert isinstance(payload["args"], dict)
        elif name == "tool_result":
            assert set(payload.keys()) == {"tool", "result_ids", "relevant"}
            assert isinstance(payload["result_ids"], list)
            assert isinstance(payload["relevant"], bool)
        elif name == "evidence":
            assert set(payload.keys()) == {"evidence_ids", "insufficient"}
            assert isinstance(payload["evidence_ids"], list)
            assert isinstance(payload["insufficient"], bool)
        elif name == "token":
            assert set(payload.keys()) == {"text"}
            assert isinstance(payload["text"], str)
        elif name == "done":
            assert set(payload.keys()) == {"ask_response"}
            assert set(payload["ask_response"].keys()) >= ASK_REQUIRED_KEYS


def test_ask_stream_rejects_a_batch_replay_implementation(
    client: TestClient, corpus: Any
) -> None:
    """The order test must FAIL against a batch/replay implementation.

    A batch implementation runs the whole agent first and then replays the
    trace. It produces the SAME SET of events in the SAME order, so a
    membership (or even an order) assertion over the finished body cannot
    tell the two apart. What tells them apart is WHEN the frames leave the
    server.

    So the check is stated as a function here, and applied to BOTH bodies:
    the route's own (streaming) body and a batch/replay body written for
    this test. The check must ACCEPT the first and REJECT the second.

    If the check ever stops rejecting the batch body, the order test is
    asserting something a replay would also satisfy, and the contract test
    has stopped discriminating.
    """
    from apps.api.agent import ask as ask_module
    from apps.api.api import routes as routes_module

    def batch_body(
        document_id: str, question: str, max_iterations: int, db_path: str
    ) -> Any:
        """A batch/replay body: collect every event, then emit them."""
        events = list(
            ask_module.iter_sse_events(
                document_id,
                question,
                max_iterations=max_iterations,
                db_path=db_path,
            )
        )
        return [routes_module._sse_frame(event) for event in events]

    def streamed_before_the_tools_ran(body: Any) -> bool:
        """True when the first frame was produced before any tool ran.

        The tools are counted by wrapping the search tool, so the count says
        whether the graph had already executed when the first frame left.
        """
        from apps.api.agent import graph as graph_module

        calls: list[str] = []
        original_search = graph_module.TOOL_TABLE["search_nodes"]

        def counting_search(state: Any, args: Any) -> Any:
            calls.append("search")
            return original_search(state, args)

        graph_module.TOOL_TABLE["search_nodes"] = counting_search
        try:
            generator = body(
                corpus["doc_id"], "What are the requisites of contracts?", 12, corpus["db_path"]
            )
            first = next(iter(generator))
            assert first.startswith("event: plan\n"), first
            return calls == []
        finally:
            graph_module.TOOL_TABLE["search_nodes"] = original_search

    # The route's own body streams: the plan leaves the server before the
    # graph has run a single tool.
    assert streamed_before_the_tools_ran(routes_module._sse_body), (
        "the route no longer streams: the plan was produced after the graph "
        "had already run the tools"
    )

    # The batch/replay body does NOT stream, and the check REJECTS it.
    assert not streamed_before_the_tools_ran(batch_body), (
        "the discriminating check no longer rejects a batch/replay body: "
        "the order test would accept an implementation that replays the "
        "trace instead of streaming it"
    )


def test_ask_stream_zero_tool_call_is_legal(client: TestClient, corpus: Any) -> None:
    """The zero-tool-call run: plan -> evidence(empty) -> done (api.md §3).

    An empty question is the invalid plan the gate fails closed on. It is a
    LEGAL request, so it is not a 422: the run abstains.
    """
    response = client.post(
        "/v1/ask/stream",
        json={"document_id": corpus["doc_id"], "question": ""},
    )
    assert response.status_code == 200, response.text
    frames = _parse_sse(response.text)
    assert [name for name, _ in frames] == ["plan", "evidence", "done"]
    evidence = frames[1][1]
    assert evidence["evidence_ids"] == []
    assert evidence["insufficient"] is True
    done = frames[2][1]["ask_response"]
    assert done["insufficient"] is True
    assert done["citations"] == []
    assert done["answer_markdown"] == CANNED_ABSTENTION


def test_ask_stream_error_is_terminal(client: TestClient) -> None:
    """A failure during the run ends the stream with one error (api.md §3).

    The document does not exist, so the run fails after the plan. The
    ``error`` event is the LAST frame, and no ``done`` follows it: a ``done``
    after an error would claim an answer the run did not produce.
    """
    response = client.post(
        "/v1/ask/stream",
        json={"document_id": "doc_nope", "question": "What is succession?"},
    )
    assert response.status_code == 200, response.text
    frames = _parse_sse(response.text)
    names = [name for name, _ in frames]
    assert names[-1] == "error", f"the error must be the last frame: {names}"
    assert names.count("error") == 1
    assert "done" not in names
    _assert_error_body(frames[-1][1], "NOT_FOUND")


def test_ask_stream_bad_body_is_422_not_an_sse_error(
    client: TestClient, corpus: Any
) -> None:
    """A bad body is a RESPONSE, not an SSE error event.

    The request never reached a run, so there is no run to report an error
    about: the ``error`` event is for a failure DURING the run.
    """
    response = client.post(
        "/v1/ask/stream",
        json={
            "document_id": corpus["doc_id"],
            "question": "What are the requisites of contracts?",
            "max_iterations": 99,
        },
    )
    assert response.status_code == 422, response.text
    assert "text/event-stream" not in response.headers.get("content-type", "")
    _assert_error_body(response.json(), "VALIDATION_ERROR")


def test_ask_stream_malformed_body_is_422_with_the_frozen_shape(
    client: TestClient,
) -> None:
    response = client.post(
        "/v1/ask/stream",
        content=b"not json at all",
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422, response.text
    _assert_error_body(response.json(), "VALIDATION_ERROR")


# ---------------------------------------------------------------------------
# 4. GET /v1/nodes/{id}
# ---------------------------------------------------------------------------


def test_get_node_returns_the_node_its_path_and_its_references(
    client: TestClient, corpus: Any
) -> None:
    response = client.get(f"/v1/nodes/{corpus['article_node_id']}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["node_id"] == corpus["article_node_id"]
    assert body["document_id"] == corpus["doc_id"]
    assert body["type"] == "article"
    assert body["title"].startswith("Article 1")
    assert isinstance(body["text"], str) and body["text"]
    assert body["page_start"] == 1
    # The article's body runs onto the next page, so the span ends there.
    assert body["page_end"] is None or isinstance(body["page_end"], int)
    assert body["depth"] == 1
    assert isinstance(body["position"], int)
    assert isinstance(body["hierarchy_path"], list)
    assert isinstance(body["references"], list)


def test_get_node_unknown_node_is_404(client: TestClient) -> None:
    response = client.get("/v1/nodes/node_nope")
    assert response.status_code == 404, response.text
    _assert_error_body(response.json(), "NOT_FOUND")


# ---------------------------------------------------------------------------
# 5. GET /v1/nodes/{id}/source
# ---------------------------------------------------------------------------


def test_get_node_source_returns_the_frozen_source(client: TestClient, corpus: Any) -> None:
    response = client.get(f"/v1/nodes/{corpus['article_node_id']}/source")
    assert response.status_code == 200, response.text
    body = response.json()
    # The four contract keys are present. node_id is the additive optional
    # key (api.md §5), not a replacement for any of them.
    assert {"text", "page_start", "page_end", "hierarchy_path"} <= set(body.keys())
    assert isinstance(body["text"], str) and body["text"]
    assert body["page_start"] == 1
    assert body["page_end"] is None or isinstance(body["page_end"], int)
    assert isinstance(body["hierarchy_path"], list)


def test_get_node_source_unknown_node_is_404(client: TestClient) -> None:
    response = client.get("/v1/nodes/node_nope/source")
    assert response.status_code == 404, response.text
    _assert_error_body(response.json(), "NOT_FOUND")


# ---------------------------------------------------------------------------
# 6. The CORS preflight the explorer needs
# ---------------------------------------------------------------------------


def test_cors_preflight_is_answered(client: TestClient) -> None:
    """The explorer is served from another origin, so the preflight must work.

    architecture.md §7: the explorer at /app/* is a separate runtime that
    calls the local API, so the API has to answer the browser's preflight.
    """
    response = client.options(
        "/v1/ask/stream",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert response.status_code == 200, response.text
    assert response.headers.get("access-control-allow-origin") == "http://localhost:3000"
    assert "POST" in response.headers.get("access-control-allow-methods", "")
