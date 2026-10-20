"""The HTTP layer of the LLM interface, tested against a local server.

The fake LLM in the other tests hides the whole request/response path, so a
defect in the PAYLOAD would ship: a seed moved out of `options` (the backend
ignores it and samples randomly) while the recorded seed still says 42, and
the suite stays green.

These tests assert on the REQUEST that goes out and on the RESPONSE that
comes back, using a real socket - so the assertions are about the wire, not
about the implementation's internals.
"""

from __future__ import annotations

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import pytest
from apps.api.generation.interface import LLMInterface


class _Recorder:
    """A stand-in backend that records the requests it receives."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.mode = "single"
        self._server: HTTPServer | None = None
        self._thread: threading.Thread | None = None

    base_url: str = ""

    def start(self) -> str:
        recorder = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                """Silence the default stderr logging."""

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length) or b"{}")
                recorder.requests.append(body)
                if recorder.mode == "stream":
                    self.send_response(200)
                    self.send_header("Content-Type", "application/x-ndjson")
                    self.end_headers()
                    for piece in ("Hel", "lo ", "world"):
                        self.wfile.write(
                            json.dumps({"response": piece, "done": False}).encode()
                            + b"\n"
                        )
                    self.wfile.write(
                        json.dumps(
                            {
                                "response": "",
                                "done": True,
                                "model": "recorded-model",
                                "prompt_eval_count": 11,
                                "eval_count": 3,
                            }
                        ).encode()
                        + b"\n"
                    )
                    self.wfile.flush()
                    return
                payload = {
                    "response": "Hello world",
                    "done": True,
                    "model": "recorded-model",
                    "prompt_eval_count": 11,
                    "eval_count": 3,
                }
                encoded = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

        # Port 0: the OS picks a free port, so the test never fights a
        # running service for one.
        self._server = HTTPServer(("127.0.0.1", 0), Handler)
        port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        self.base_url = f"http://127.0.0.1:{port}"
        return self.base_url

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)


@pytest.fixture
def recorder() -> Any:
    """A recording backend, stopped when the test ends."""
    rec = _Recorder()
    rec.start()
    yield rec
    rec.stop()


def test_generation_request_carries_the_pinned_sampling_parameters(
    recorder: Any,
) -> None:
    """The seed and temperature go in `options`, where the backend reads them.

    A seed at the payload's top level is IGNORED by the backend - it samples
    randomly - while the recorded seed still says 42. The recorded value
    would look reproducible and the run would not be.
    """
    llm = LLMInterface(
        model="recorded-model",
        base_url=recorder.base_url,
        temperature=0.0,
        seed=42,
    )
    result = llm.generate("the question")
    assert recorder.requests, "the backend must have received a request"
    body = recorder.requests[-1]
    assert body["model"] == "recorded-model"
    assert body["prompt"] == "the question"
    assert body["stream"] is False
    assert body["options"]["temperature"] == 0.0
    assert body["options"]["seed"] == 42
    # The recorded result matches what was sent.
    assert result.temperature == 0.0
    assert result.seed == 42
    assert result.model == "recorded-model"
    # The provider's own counts are read from the response.
    assert result.prompt_tokens == 11
    assert result.completion_tokens == 3
    assert result.text == "Hello world"


def test_generation_stream_request_sets_the_stream_flag(recorder: Any) -> None:
    """The streaming call says so on the wire, and parses the NDJSON body."""
    recorder.mode = "stream"
    llm = LLMInterface(
        model="recorded-model", base_url=recorder.base_url, seed=42
    )
    result, chunks = llm.stream_result("the question")
    body = recorder.requests[-1]
    assert body["stream"] is True
    assert body["options"]["seed"] == 42
    assert body["options"]["temperature"] == 0.0
    # The deltas are the backend's, in its order.
    assert chunks == ["Hel", "lo ", "world"]
    assert result.text == "Hello world"
    # The counters come from the final streamed object.
    assert result.prompt_tokens == 11
    assert result.completion_tokens == 3


def test_generation_single_shot_and_stream_send_the_same_request(
    recorder: Any,
) -> None:
    """ADR-004: a streamed answer and a single-shot answer are the same run.

    The two calls differ only in the `stream` flag; every sampling parameter
    is identical, so the backend produces the same answer from the same seed.
    """
    llm = LLMInterface(
        model="recorded-model", base_url=recorder.base_url, seed=42
    )
    llm.generate("the question")
    single = recorder.requests[-1]
    recorder.mode = "stream"
    llm.stream_result("the question")
    streamed = recorder.requests[-1]
    assert single["options"] == streamed["options"]
    assert single["model"] == streamed["model"]
    assert single["prompt"] == streamed["prompt"]
    assert single["stream"] is False
    assert streamed["stream"] is True


def test_generation_a_per_call_seed_overrides_the_instance(recorder: Any) -> None:
    """ADR-004 names temperature and seed as CALL parameters.

    A judge (the Day-6 eval runner) needs the interface to express a
    per-call seed and temperature, and the recorded result must carry the
    EFFECTIVE values - the record and the run cannot disagree.
    """
    llm = LLMInterface(
        model="recorded-model", base_url=recorder.base_url, temperature=0.0, seed=42
    )
    result = llm.generate("the question", temperature=0.7, seed=99)
    options = recorder.requests[-1]["options"]
    assert options["seed"] == 99
    assert options["temperature"] == 0.7
    # The recorded result carries what was SENT, not the instance's values.
    assert result.seed == 99
    assert result.temperature == 0.7
    # A call without overrides uses the instance's.
    llm.generate("the question")
    assert recorder.requests[-1]["options"]["seed"] == 42
    assert recorder.requests[-1]["options"]["temperature"] == 0.0


def test_generation_stop_and_max_tokens_are_sent(recorder: Any) -> None:
    """The stop sequences and the token cap reach the backend."""
    llm = LLMInterface(
        model="recorded-model", base_url=recorder.base_url, seed=42
    )
    llm.generate("the question", stop=["\n\n"], max_tokens=256)
    options = recorder.requests[-1]["options"]
    assert options["stop"] == ["\n\n"]
    assert options["num_predict"] == 256


def test_generation_unusable_body_is_a_503_not_an_answer(recorder: Any) -> None:
    """A backend that answers with something unusable is still unavailable.

    api.md sec 1: LLM_UNAVAILABLE is "backend down/timeout ... or returns an
    unusable body". A non-JSON body must not become an empty answer.
    """
    from apps.api.ingestion import errors

    class _Broken(BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:
            """Silence the default stderr logging."""

        def do_POST(self) -> None:
            body = b"<html>not json</html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = HTTPServer(("127.0.0.1", 0), _Broken)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        llm = LLMInterface(
            model="recorded-model", base_url=f"http://127.0.0.1:{port}", seed=42
        )
        with pytest.raises(errors.ApiError) as excinfo:
            llm.generate("the question")
        assert excinfo.value.code == "LLM_UNAVAILABLE"
        assert excinfo.value.status == 503
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_generation_an_empty_response_is_a_503_not_an_empty_answer(
    recorder: Any,
) -> None:
    """A backend that answers with no usable text is UNAVAILABLE.

    api.md sec 1 puts "returns an unusable body" under LLM_UNAVAILABLE.
    Answering "" with a 200 would present a backend failure as a successful
    (and empty) answer, which is the "fake answer" shape ADR-004 forbids.
    """
    from apps.api.ingestion import errors

    class _Empty(BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:
            """Silence the default stderr logging."""

        def do_POST(self) -> None:
            body = json.dumps({"model": "recorded-model", "done": True}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = HTTPServer(("127.0.0.1", 0), _Empty)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        llm = LLMInterface(
            model="recorded-model", base_url=f"http://127.0.0.1:{port}", seed=42
        )
        with pytest.raises(errors.ApiError) as excinfo:
            llm.generate("the question")
        assert excinfo.value.code == "LLM_UNAVAILABLE"
        assert excinfo.value.status == 503
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_generation_a_stream_with_no_text_is_a_503(recorder: Any) -> None:
    """A stream that carries no text is an unusable backend too."""
    from apps.api.ingestion import errors

    class _EmptyStream(BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:
            """Silence the default stderr logging."""

        def do_POST(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.end_headers()
            self.wfile.write(
                json.dumps({"response": "", "done": True}).encode() + b"\n"
            )
            self.wfile.flush()

    server = HTTPServer(("127.0.0.1", 0), _EmptyStream)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        llm = LLMInterface(
            model="recorded-model", base_url=f"http://127.0.0.1:{port}", seed=42
        )
        with pytest.raises(errors.ApiError) as excinfo:
            llm.stream_result("the question")
        assert excinfo.value.code == "LLM_UNAVAILABLE"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_generation_a_closed_port_fails_fast(recorder: Any) -> None:
    """A backend that is not there fails in seconds, not in the read timeout."""
    import time

    from apps.api.ingestion import errors

    # A socket that is bound and immediately closed gives a port nothing is
    # listening on.
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    llm = LLMInterface(
        model="recorded-model", base_url=f"http://127.0.0.1:{port}", timeout=120.0
    )
    start = time.perf_counter()
    with pytest.raises(errors.ApiError) as excinfo:
        llm.generate("the question")
    elapsed = time.perf_counter() - start
    assert excinfo.value.code == "LLM_UNAVAILABLE"
    assert elapsed < 30.0, f"a dead backend took {elapsed:.1f}s to fail"
