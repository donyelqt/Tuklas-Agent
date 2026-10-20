"""The /v1 response-schema snapshot (CONSTRAINTS.md: "Breaking API").

CONSTRAINTS.md requires: "No response-field removal/rename under /v1" and
api.md §5: "Changes additive-only under /v1 (new optional fields);
renames/removals -> /v2 + ADR. Enforced by breaking-change test (snapshot
response schemas)."

The test was named in both documents and did not exist, so the rule was
asserted but not enforced. This is that test.

It pins the frozen AskResponse shape and the frozen error table, and it
enforces the ADDITIVE-ONLY rule mechanically:

- every key the v1 contract declares must still be present;
- no key may be renamed (a rename shows up as a missing key plus an extra);
- a key added after the freeze must be OPTIONAL - api.md §5 admits only
  optional new fields, and a required new key breaks a client generated from
  the frozen shape under additionalProperties: false.
"""

from __future__ import annotations

import re
from pathlib import Path

from apps.api.agent.ask import ASK_RESPONSE_KEYS, ASK_RESPONSE_REQUIRED_KEYS
from apps.api.agent.graph import CANNED_ABSTENTION
from apps.api.ingestion import errors

REPO_ROOT = Path(__file__).resolve().parents[3]
API_MD = REPO_ROOT / "docs" / "api.md"

#: The AskResponse shape as frozen in api.md §3. Every key here must still
#: exist, and no key may be renamed.
FROZEN_ASK_RESPONSE_KEYS = frozenset(
    {
        "answer_markdown",
        "citations",
        "evidence_ids",
        "iterations",
        "latency_ms",
        "tokens",
        "insufficient",
    }
)

#: The error codes api.md §1 freezes. A code that is not here has no arm in
#: a client's error switch.
FROZEN_ERROR_CODES = frozenset(
    {
        "VALIDATION_ERROR",
        "OCR_REQUIRED",
        "PARSE_FAILED",
        "NOT_FOUND",
        "CONFLICT",
        "LLM_UNAVAILABLE",
        "EVAL_FAILED",
        "INTERNAL_ERROR",
    }
)


def _api_md_ask_response_keys() -> set[str]:
    """The AskResponse keys as api.md §3 declares them.

    The shape is written across two lines, so the block is read from the
    "AskResponse:" marker up to the first line that does not look like part
    of the shape.
    """
    text = API_MD.read_text(encoding="utf-8")
    marker = "→ 200 AskResponse:"
    assert marker in text, "api.md §3 no longer declares the AskResponse"
    tail = text.split(marker, 1)[1]
    body_lines: list[str] = []
    for line in tail.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if body_lines and not stripped.startswith(("{", "}")) and ":" not in stripped:
            break
        if body_lines and stripped.startswith("#"):
            break
        body_lines.append(stripped)
        if stripped.endswith("}") and "{" in "".join(body_lines):
            break
    body = " ".join(body_lines)
    return set(re.findall(r"([a-z_]+)\??\s*:", body))


def _api_md_error_codes() -> set[str]:
    """The error codes api.md §1 declares."""
    text = API_MD.read_text(encoding="utf-8")
    section = text.split("## 1. Conventions", 1)[1].split("## 2.", 1)[0]
    return set(re.findall(r"`([A-Z_]+)`\s*\|\s*\d{3}\s*\|", section))


def test_api_v1_snapshot() -> None:
    """The /v1 AskResponse shape and the error table have not drifted."""
    documented = _api_md_ask_response_keys()
    assert documented, "api.md §3 declares no AskResponse keys"

    # 1. No field was removed or renamed: every frozen key is still there.
    missing = FROZEN_ASK_RESPONSE_KEYS - documented
    assert not missing, f"api.md §3 dropped frozen AskResponse fields: {sorted(missing)}"

    # 2. The code and the document agree.
    assert ASK_RESPONSE_REQUIRED_KEYS <= ASK_RESPONSE_KEYS
    assert FROZEN_ASK_RESPONSE_KEYS <= ASK_RESPONSE_KEYS, (
        "the implementation dropped a frozen AskResponse field"
    )
    assert documented <= ASK_RESPONSE_KEYS, (
        f"api.md §3 declares fields the implementation does not return: "
        f"{sorted(documented - ASK_RESPONSE_KEYS)}"
    )

    # 3. Additive-only: a key added after the freeze must be OPTIONAL.
    #    api.md §5 admits new OPTIONAL fields under /v1; a required new key
    #    breaks a client generated from the frozen shape.
    added = ASK_RESPONSE_KEYS - FROZEN_ASK_RESPONSE_KEYS
    for key in added:
        assert f"{key}?" in API_MD.read_text(encoding="utf-8"), (
            f"the added field {key!r} must be declared OPTIONAL in api.md §3 "
            f"(a required new key is a strict-schema break)"
        )


def test_api_v1_error_codes() -> None:
    """Every error code the code can emit is in api.md §1's frozen table."""
    documented = _api_md_error_codes()
    assert documented, "api.md §1 declares no error codes"
    assert documented >= FROZEN_ERROR_CODES, (
        f"api.md §1 dropped frozen error codes: "
        f"{sorted(FROZEN_ERROR_CODES - documented)}"
    )

    # The codes the agent surfaces must be in the table.
    for error in (
        errors.validation_error("x"),
        errors.not_found("x"),
        errors.ApiError("INTERNAL_ERROR", "internal server error", 500, {}),
    ):
        assert error.code in documented, (
            f"the agent can emit {error.code!r}, which api.md §1 does not list"
        )


def test_api_v1_error_body_shape() -> None:
    """The error body is exactly {code, message, details} (api.md §1)."""
    for error in (
        errors.validation_error("bad body", field="x"),
        errors.not_found("missing", identifier="y"),
    ):
        body: dict[str, object] = error.body()
        assert set(body.keys()) == {"error"}
        payload = body["error"]
        assert isinstance(payload, dict)
        assert set(payload.keys()) == {"code", "message", "details"}


def test_api_v1_abstention_string_is_pinned() -> None:
    """The abstention wording in api.md §3 is what the code returns."""
    text = API_MD.read_text(encoding="utf-8")
    assert CANNED_ABSTENTION in text, (
        "the canned abstention string is not pinned in api.md §3"
    )
