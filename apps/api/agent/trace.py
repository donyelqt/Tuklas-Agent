"""The ask trace (ADR-004, architecture.md §9).

ADR-004: "Model id + backend + temperature + seed recorded in every ask
trace and eval run" - a run cannot be compared across runs unless the run
that produced it is named.

This module writes the ``queries`` row for one ask: the question, the
document, the run record, and the observable facts of the run (iterations,
evidence ids, tokens, latency, whether it refused).

It is a separate module because the ask layer must not import the ingestion
DB directly: the agent's thinness floor is checked by
``scripts/check_no_vectors.py``, and the ingestion module owns the schema.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from apps.api.ingestion import db as ingestion_db


def question_hash(question: str) -> str:
    """A stable hash of the question (architecture.md §9: "question hash").

    The question itself is stored, so the hash is not a privacy measure; it
    is a join key that survives a question being edited, so the same
    question asked twice is recognisable as the same question.
    """
    return hashlib.sha256(question.strip().encode("utf-8")).hexdigest()[:16]


def _query_id(document_id: str, question: str) -> str:
    """The id of the ask row (api.md §1: ids are ``q_<uuid8>``)."""
    return f"q_{uuid.uuid4().hex[:8]}"


def record_ask(
    document_id: str,
    question: str,
    *,
    db_path: str,
    state: dict[str, Any],
    response: dict[str, Any],
) -> str:
    """Write the ask's trace row and return its id.

    The row carries the run record (model, backend, temperature, seed,
    prompt version) and the observable facts of the run. A row that cannot
    be written must not fail the ask: the caller already has the answer,
    and losing the trace is better than losing the answer. The failure is
    logged.
    """
    query_id = _query_id(document_id, question)
    tokens = response.get("tokens", {})
    evidence_ids = response.get("evidence_ids", [])
    try:
        with ingestion_db.connect(db_path) as conn:
            conn.execute(
                "INSERT INTO queries (id, document_id, question, created_at, model,"
                " backend, temperature, seed, prompt_version, question_hash,"
                " iterations, evidence_ids, tokens_in, tokens_out, tokens_source,"
                " latency_ms, insufficient)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    query_id,
                    document_id,
                    question,
                    datetime.now(UTC).isoformat(),
                    state.get("_gen_model"),
                    state.get("_gen_backend"),
                    state.get("_gen_temperature"),
                    state.get("_gen_seed"),
                    state.get("_gen_prompt_version"),
                    question_hash(question),
                    int(response.get("iterations", 0)),
                    json.dumps(list(evidence_ids)),
                    int(tokens.get("in", 0)) if isinstance(tokens, dict) else None,
                    int(tokens.get("out", 0)) if isinstance(tokens, dict) else None,
                    response.get("tokens_source"),
                    int(response.get("latency_ms", 0)),
                    1 if response.get("insufficient") else 0,
                ),
            )
    except Exception:
        # The ask already produced its answer; losing the trace is better
        # than losing the answer. The exception is logged, not returned.
        logging.getLogger(__name__).exception("could not record the ask trace")
    return query_id


__all__ = ["question_hash", "record_ask"]
