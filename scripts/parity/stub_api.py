"""A deterministic stub of the API the explorer calls.

The visual parity harness screenshots the explorer in a browser, and a
screenshot is only comparable against another screenshot if the DATA
behind it is the same. A real API would serve whatever the corpus holds
and whatever the model produced, so the pixels would differ between two
runs for reasons that have nothing to do with the interface.

This stub serves FIXED responses, so two runs of the same code against
this stub produce the same pixels. That is what makes a pixel diff a
statement about the interface rather than about the data.

It serves exactly the five routes `docs/api.md` section 3 freezes, and
the SSE stream emits the frozen event sequence:
`plan -> tool_call -> tool_result -> (repeat) -> evidence -> token* ->
done`.

Run it with:

    python scripts/parity/stub_api.py --port 8000
"""

from __future__ import annotations

import argparse
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

DOCUMENT_ID = "doc-civil-code"

DOCUMENTS: dict[str, Any] = {
    "data": [
        {
            "id": DOCUMENT_ID,
            "name": "Civil Code of the Philippines (annotated)",
            "page_count": 1183,
            "created_at": "2026-10-20T09:14:00Z",
        }
    ],
    "pagination": {"page": 1, "page_size": 20, "total_items": 1, "total_pages": 1},
}


def _node(
    node_id: str,
    node_type: str,
    title: str,
    depth: int,
    position: int,
    page_start: int,
    page_end: int,
    children: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "id": node_id,
        "type": node_type,
        "title": title,
        "depth": depth,
        "position": position,
        "page_start": page_start,
        "page_end": page_end,
        "children": children,
    }


TREE: dict[str, Any] = {
    "document_id": DOCUMENT_ID,
    "depth": 5,
    "root": _node(
        DOCUMENT_ID,
        "document",
        "Civil Code of the Philippines (annotated)",
        1,
        1,
        1,
        1183,
        [
            _node(
                "book-one",
                "book",
                "Book One: General Provisions",
                2,
                1,
                1,
                274,
                [
                    _node(
                        "title-i",
                        "title",
                        "Title I: General Rules",
                        3,
                        1,
                        1,
                        274,
                        [
                            _node(
                                "chapter-i",
                                "chapter",
                                "Chapter I: Application of the Laws",
                                4,
                                1,
                                3,
                                8,
                                [
                                    _node(
                                        "art-1",
                                        "article",
                                        "Article 1",
                                        5,
                                        1,
                                        3,
                                        3,
                                        [],
                                    ),
                                    _node(
                                        "art-2",
                                        "article",
                                        "Article 2",
                                        5,
                                        2,
                                        3,
                                        3,
                                        [],
                                    ),
                                    _node(
                                        "art-3",
                                        "article",
                                        "Article 3",
                                        5,
                                        3,
                                        4,
                                        5,
                                        [],
                                    ),
                                ],
                            ),
                            _node(
                                "chapter-ii",
                                "chapter",
                                "Chapter II: Legal Transactions",
                                4,
                                2,
                                9,
                                23,
                                [
                                    _node(
                                        "art-4",
                                        "article",
                                        "Article 4",
                                        5,
                                        1,
                                        9,
                                        9,
                                        [],
                                    ),
                                    _node(
                                        "art-5",
                                        "article",
                                        "Article 5",
                                        5,
                                        2,
                                        10,
                                        10,
                                        [],
                                    ),
                                ],
                            ),
                        ],
                    ),
                    _node(
                        "title-ii",
                        "title",
                        "Title II: Obligations and Contracts",
                        3,
                        2,
                        25,
                        274,
                        [
                            _node(
                                "chapter-iii",
                                "chapter",
                                "Chapter I: General Provisions on Obligations",
                                4,
                                1,
                                25,
                                74,
                                [
                                    _node(
                                        "art-6",
                                        "article",
                                        "Article 6",
                                        5,
                                        1,
                                        25,
                                        25,
                                        [],
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
        ],
    ),
}

SOURCES: dict[str, str] = {
    "art-1": (
        "Article 1. This Code shall govern the relations between persons governed by "
        "Philippine law, if the subject-matter of the relation is within the territory "
        "of the Philippines and the law of the forum is made applicable by the "
        "provisions of this Code."
    ),
    "art-2": (
        "Article 2. The provisions of this Code apply to the Philippines, and to the "
        "other countries insofar as the laws of the forum and the will of the parties "
        "expressly or tacitly refer to them."
    ),
    "art-3": (
        "Article 3. The ignorance of the law excuses no one. The proof of ignorance "
        "of the law is inadmissible, and the person who invokes it bears the burden of "
        "proving it, in case he claims that he has endeavored to comply with it."
    ),
    "art-4": (
        "Article 4. The following provisions apply in the cases contemplated by law: "
        "1. If the law of the forum is made applicable by stipulation, and the "
        "stipulation is valid; 2. If the law of the forum is made applicable by virtue "
        "of international law."
    ),
    "art-5": (
        "Article 5. If the law of the forum is made applicable by stipulation, and the "
        "stipulation is valid, the parties may freely stipulate the terms of their "
        "agreement, subject to the general rules of law."
    ),
    "art-6": (
        "Article 6. An obligation is a juridical duty to perform, in accordance with "
        "the laws of the Philippines, a certain act or to refrain from doing an act, "
        "which is due from a certain person to another."
    ),
}

ANSWER_MARKDOWN = (
    "Article 3 states that ignorance of the law excuses no one, and that the proof of "
    "ignorance is inadmissible [1]. The person who invokes it bears the burden of "
    "proving it [1]. Article 1 limits the Code's scope to relations between persons "
    "governed by Philippine law where the subject-matter of the relation is within the "
    "territory of the Philippines and the law of the forum is made applicable by the "
    "provisions of this Code [2]."
)

ANSWER: dict[str, Any] = {
    "answer_markdown": ANSWER_MARKDOWN,
    "citations": [
        {
            "n": 1,
            "node_id": "art-3",
            "label": "Article 3",
            "page_start": 4,
            "page_end": 5,
        },
        {
            "n": 2,
            "node_id": "art-1",
            "label": "Article 1",
            "page_start": 3,
            "page_end": 3,
        },
    ],
    "evidence_ids": ["art-3", "art-1"],
    "iterations": 2,
    "latency_ms": 1234,
    "tokens": {"in": 812, "out": 96},
    "tokens_source": "estimated",
    "insufficient": False,
}

STEPS: list[dict[str, Any]] = [
    {
        "event": "plan",
        "data": {"sub_questions": ["What does Article 3 say?", "When does the Code apply?"]},
    },
    {
        "event": "tool_call",
        "data": {"tool": "search_nodes", "args": {"query": "ignorance of the law"}},
    },
    {
        "event": "tool_result",
        "data": {"tool": "search_nodes", "result_ids": ["art-3"], "relevant": True},
    },
    {"event": "tool_call", "data": {"tool": "read_node", "args": {"node_id": "art-1"}}},
    {
        "event": "tool_result",
        "data": {"tool": "read_node", "result_ids": ["art-1"], "relevant": True},
    },
    {"event": "evidence", "data": {"evidence_ids": ["art-3", "art-1"], "insufficient": False}},
]


class Handler(BaseHTTPRequestHandler):
    """The stub's request handler."""

    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        """Stay quiet: the harness reads stdout for its own markers."""

    def _send(self, status: int, body: Any, content_type: str = "application/json") -> None:
        payload = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()
        self.wfile.write(payload)

    def do_OPTIONS(self) -> None:
        self._send(204, b"")

    def do_GET(self) -> None:
        path = self.path.split("?")[0]
        if path == "/v1/documents":
            self._send(200, DOCUMENTS)
            return
        if path == f"/v1/documents/{DOCUMENT_ID}/tree":
            self._send(200, TREE)
            return
        if path.startswith("/v1/nodes/") and path.endswith("/source"):
            node_id = path[len("/v1/nodes/") : -len("/source")]
            text = SOURCES.get(node_id)
            if text is None:
                self._send(
                    404,
                    {
                        "error": {
                            "code": "NODE_NOT_FOUND",
                            "message": "No such node.",
                            "details": {},
                        }
                    },
                )
                return
            self._send(
                200,
                {
                    "node_id": node_id,
                    "text": text,
                    "page_start": 4,
                    "page_end": 5,
                    "hierarchy_path": ["Book One", "Title I", "Chapter I", "Article 3"],
                },
            )
            return
        self._send(
            404, {"error": {"code": "NOT_FOUND", "message": "Unknown route.", "details": {}}}
        )

    def _chunk(self, payload: bytes) -> None:
        self.wfile.write(f"{len(payload):X}\r\n".encode("ascii"))
        self.wfile.write(payload)
        self.wfile.write(b"\r\n")
        self.wfile.flush()

    def do_POST(self) -> None:
        path = self.path.split("?")[0]
        if path != "/v1/ask/stream":
            self._send(
                404, {"error": {"code": "NOT_FOUND", "message": "Unknown route.", "details": {}}}
            )
            return
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        for step in STEPS:
            frame = f"event: {step['event']}\ndata: {json.dumps(step['data'])}\n\n"
            self._chunk(frame.encode("utf-8"))
            time.sleep(0.01)
        for token in ANSWER_MARKDOWN.split(" "):
            frame = f"event: token\ndata: {json.dumps({'text': token + ' '})}\n\n"
            self._chunk(frame.encode("utf-8"))
        frame = f"event: done\ndata: {json.dumps({'ask_response': ANSWER})}\n\n"
        self._chunk(frame.encode("utf-8"))
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"stub_api: listening on http://127.0.0.1:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
