"""Day-4 generation test suite (PLAN.md Day 4, architecture.md sec 6, ADR-004).

Contract tests for the generation slice:

* the prompt renderer NEVER reorders or truncates the evidence (the Context
  Builder's job, retrieval.md sec 5);
* the rendered system prompt states the three binding constraints and carries
  the persistent disclaimer wording from the web copy;
* the advice blocklist is a SINGLE oracle: one place knows the phrases, and
  the service refuses on it;
* citations carry the page span from the EVIDENCE, never from the context
  parts, and a node outside the evidence set is never invented;
* the refusal shape is the uniform abstention of api.md sec 3;
* a dead backend is a typed 503 LLM_UNAVAILABLE, never a fake answer;
* every result records model + backend + temperature + seed (ADR-004).

The fast tests use hand-built contexts and a local fake LLM, so no model and
no network are needed. One test proves the wiring against the real local
ollama and is skipped unless TUKLAS_LIVE_LLM=1.

The public API programmed against (apps/api/generation/):

    interface.py  GenerationResult(text, model, backend, temperature, seed,
                                    prompt_tokens, completion_tokens)
                 LLMInterface(backend="ollama", model="qwen2.5:3b", *,
                              base_url=..., temperature=0.0, seed=42, timeout=...)
                 .generate(prompt, stop=None, max_tokens=None) -> GenerationResult
                 .generate_stream(prompt, stop=None, max_tokens=None) -> Iterator[str]
    prompts.py    PROMPT_VERSION: str
                 render_prompt(context, question, sub_questions, *,
                              disclaimer: str) -> str
    citations.py  assemble_citations(parts, evidence) -> list[dict]
    blocklist.py  ADVICE_PHRASES: frozenset[str]
                 contains_advice(text) -> bool
                 find_advice(text) -> list[str]
    service.py    GenerationService(llm)
                 .answer(context, question, sub_questions, evidence, *,
                         gate_passed: bool) -> dict
"""

from __future__ import annotations

import ast
import json
import os
import re
import socket
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import pytest
from apps.api.agent.graph import CANNED_ABSTENTION

if TYPE_CHECKING:  # pragma: no cover - typing only
    from apps.api.generation.interface import GenerationResult, LLMInterface

REPO_ROOT = Path(__file__).resolve().parents[3]
COPY_TS = REPO_ROOT / "apps" / "web" / "src" / "lib" / "copy.ts"
GENERATION_DIR = REPO_ROOT / "apps" / "api" / "generation"

#: The four advice phrases, as the contract states them. Compared
#: case-insensitively, so the test does not pin the constant's casing.
ADVICE_PHRASE_SET = frozenset({"you should", "i advise", "your case", "you must"})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _web_disclaimer() -> str:
    """The persistent disclaimer wording, READ from the web copy (never typed here).

    copy.ts is the single source of truth for the wording (architecture.md
    sec 7, PLAN.md Day 5): the generation prompt must carry it verbatim, so
    the test reads the string instead of hardcoding a copy that could drift.
    """
    if not COPY_TS.exists():
        pytest.fail(f"web copy file is missing: {COPY_TS}")
    source = COPY_TS.read_text(encoding="utf-8")
    match = re.search(r"disclaimer:\s*\n\s*(\"(?:[^\"\\]|\\.)*\")", source)
    if match is None:
        pytest.fail(
            "apps/web/src/lib/copy.ts carries no `disclaimer:` string literal; "
            "the persistent disclaimer wording is the source of truth for the prompt"
        )
    return cast(str, json.loads(match.group(1)))


def _closed_port() -> int:
    """An ephemeral TCP port that nothing listens on (a dead backend)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = int(probe.getsockname()[1])
    return port


def _code_strings(path: Path) -> list[str]:
    """Every string literal in a module EXCEPT its docstrings.

    Docstrings describe the contract; they do not ENCODE it. A second copy
    of the advice phrases in executable code is a drift risk, in prose it is
    a comment. Parsing the AST keeps the check about behaviour, not about
    how the module happens to be written.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstring_nodes: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = getattr(node, "body", [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                docstring_nodes.add(id(body[0].value))
    literals: list[str] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstring_nodes
        ):
            literals.append(node.value)
    return literals


def _reads_the_blocklist(path: Path) -> bool:
    """True when the module reads the advice oracle (the blocklist module).

    Any import from the blocklist counts: ``ADVICE_PHRASES``,
    ``contains_advice``, ``find_advice``, or the module itself. The point is
    that the phrases are read from ONE place, not re-typed.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.ImportFrom)
            and (node.module or "").endswith("blocklist")
            and node.names
        ):
            return True
        if isinstance(node, ast.Import) and any(
            alias.name.endswith("blocklist") for alias in node.names
        ):
            return True
    return False


def _result_type() -> type[GenerationResult]:
    """The recorded result type, imported at call time so the module can be absent."""
    from apps.api.generation.interface import GenerationResult as _Result

    return _Result


class _FakeLLM:
    """A local stand-in for LLMInterface: no network, no model, no stubbing out.

    Exposes the same surface the service reads — ``generate()`` /
    ``generate_stream()`` plus the recorded ``model`` / ``backend`` /
    ``temperature`` / ``seed`` — so the service can be exercised without a
    live backend.
    """

    def __init__(
        self,
        text: str,
        *,
        model: str = "fake-model",
        backend: str = "ollama",
        temperature: float = 0.0,
        seed: int = 42,
        prompt_tokens: int = 11,
        completion_tokens: int = 7,
    ) -> None:
        self.text = text
        self.model = model
        self.backend = backend
        self.temperature = temperature
        self.seed = seed
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.prompts: list[str] = []

    def generate(
        self,
        prompt: str,
        stop: list[str] | None = None,
        max_tokens: int | None = None,
    ) -> GenerationResult:
        """Return the canned answer as a recorded GenerationResult."""
        result_cls = _result_type()

        self.prompts.append(prompt)
        return result_cls(
            text=self.text,
            model=self.model,
            backend=self.backend,
            temperature=self.temperature,
            seed=self.seed,
            prompt_tokens=self.prompt_tokens,
            completion_tokens=self.completion_tokens,
        )

    def generate_stream(
        self,
        prompt: str,
        stop: list[str] | None = None,
        max_tokens: int | None = None,
    ) -> Iterator[str]:
        """Yield the canned answer in word-sized chunks."""
        result_cls = _result_type()

        self.prompts.append(prompt)
        result = result_cls(
            text=self.text,
            model=self.model,
            backend=self.backend,
            temperature=self.temperature,
            seed=self.seed,
            prompt_tokens=self.prompt_tokens,
            completion_tokens=self.completion_tokens,
        )
        for chunk in result.text.split(" "):
            yield chunk + " "


def _long_body(marker: str) -> str:
    """A passage far longer than any plausible prompt budget."""
    unit = (
        "The obligation to pay the price arises upon demand of the seller, and "
        "failure to comply entitles the seller to rescind the sale. "
    )
    return f"{marker} START " + unit * 400 + f" {marker} END"


def _context() -> dict[str, Any]:
    """A PromptContext whose parts have very different lengths.

    The parts carry exactly the three contract keys (retrieval.md sec 5):
    node_id, text, score. No page information — the page span lives in the
    evidence, not here.
    """
    parts = [
        {
            "node_id": "node_civil-code-public_article_1318",
            "text": "PART-ALPHA Article 1318: the demand for payment must be in writing.",
            "score": 0.91,
        },
        {
            "node_id": "node_civil-code-public_article_1319",
            "text": _long_body("PART-BETA"),
            "score": 0.55,
        },
        {
            "node_id": "node_civil-code-public_article_1320",
            "text": "PART-GAMMA Article 1320: damages are recoverable as provided by law.",
            "score": 0.20,
        },
    ]
    return {
        "parts": parts,
        "total_chars": sum(len(str(part["text"])) for part in parts),
        "context_truncated": False,
    }


def _evidence() -> dict[str, Any]:
    """A select_evidence-shaped payload, as retrieval.api.select_evidence returns it."""
    return {
        "kept": [
            {
                "node_id": "node_civil-code-public_article_1318",
                "title": "Article 1318",
                "text": "The demand for payment must be in writing.",
                "snippet": "demand for payment must be in writing",
                "score": 0.91,
                "components": {"lex": 0.8, "ident": 1.0, "title": 0.4, "ref": 0.0, "hier": 0.0},
                "page_start": 17,
                "page_end": 18,
                "depth": 2,
                "position": 3,
            },
            {
                "node_id": "node_civil-code-public_article_1319",
                "title": "Article 1319",
                "text": "Written notice is sufficient when sent to the address of the buyer.",
                "snippet": "written notice is sufficient",
                "score": 0.55,
                "components": {"lex": 0.6, "ident": 0.0, "title": 0.3, "ref": 0.0, "hier": 0.0},
                "page_start": 19,
                "page_end": 19,
                "depth": 2,
                "position": 4,
            },
            {
                "node_id": "node_civil-code-public_article_1320",
                "title": "Article 1320",
                "text": "Damages are recoverable in the cases provided by law.",
                "snippet": "damages are recoverable",
                "score": 0.20,
                "components": {"lex": 0.3, "ident": 0.0, "title": 0.2, "ref": 0.0, "hier": 0.0},
                "page_start": 20,
                "page_end": 21,
                "depth": 2,
                "position": 5,
            },
        ],
        "insufficient": False,
        "gate": {
            "threshold": 0.15,
            "node_ids": [
                "node_civil-code-public_article_1318",
                "node_civil-code-public_article_1319",
                "node_civil-code-public_article_1320",
            ],
            "per_question": {
                "What does Article 1318 require?": [
                    "node_civil-code-public_article_1318",
                    "node_civil-code-public_article_1319",
                ]
            },
        },
    }


def _contains_any(text: str, alternatives: tuple[str, ...]) -> bool:
    """True when the lower-cased text carries at least one alternative phrase."""
    lowered = text.lower()
    return any(alternative in lowered for alternative in alternatives)


# ---------------------------------------------------------------------------
# 1. The prompt renders the evidence, in order, in full
# ---------------------------------------------------------------------------


def test_generation_prompt_renders_evidence_in_order() -> None:
    """Every context part is rendered, in the Context Builder's order, uncut.

    CONTRACT TEST (architecture.md sec 6, retrieval.md sec 5): the generation
    layer renders the opaque PromptContext and NEVER reorders or truncates
    the evidence — the budget, the order and the truncation flag belong to
    retrieval's Context Builder. A renderer that drops or clips a part
    (for example under a per-part char cap) loses evidence the gate had
    already accepted, so that defect fails here.
    """
    try:
        from apps.api.generation.prompts import render_prompt
    except ImportError as exc:  # pragma: no cover - the RED phase
        pytest.fail(f"apps.api.generation.prompts.render_prompt is not importable: {exc}")

    context = _context()
    parts = list(context["parts"])
    disclaimer = _web_disclaimer()

    prompt = render_prompt(
        context,
        "What does Article 1318 require?",
        ["What does Article 1318 require?"],
        disclaimer=disclaimer,
    )

    assert isinstance(prompt, str) and prompt.strip(), "the rendered prompt is empty"

    # 1. Nothing is dropped and nothing is cut: every part's FULL text is in.
    for part in parts:
        text = str(part["text"])
        assert text in prompt, (
            f"the rendered prompt does not carry the full text of {part['node_id']}: "
            "generation must not truncate the evidence"
        )

    # 2. Nothing is reordered: the parts appear in the order given.
    positions = [prompt.index(str(part["text"])) for part in parts]
    assert positions == sorted(positions), (
        "the rendered prompt reordered the evidence parts; the order is the "
        "Context Builder's to set (retrieval.md sec 5)"
    )

    # 3. The sentinel of the long part is intact at both ends — a clipped
    #    passage would lose its tail.
    long_text = str(parts[1]["text"])
    assert long_text.startswith("PART-BETA START")
    assert long_text.endswith("PART-BETA END")
    assert "PART-BETA END" in prompt


# ---------------------------------------------------------------------------
# 2. The prompt states the binding constraints and carries the disclaimer
# ---------------------------------------------------------------------------


def test_generation_prompt_states_the_constraints() -> None:
    """The system prompt states the three constraints and the disclaimer.

    architecture.md sec 6: "answer only from evidence; every factual sentence
    carries [n]; refusal path when gate fails". The persistent disclaimer
    wording comes from the web copy (apps/web/src/lib/copy.ts) and is read
    from there, never typed into this test, so the prompt and the UI cannot
    drift apart.
    """
    try:
        from apps.api.generation.prompts import render_prompt
    except ImportError as exc:  # pragma: no cover - the RED phase
        pytest.fail(f"apps.api.generation.prompts.render_prompt is not importable: {exc}")

    disclaimer = _web_disclaimer()
    assert disclaimer, "the web copy carries an empty disclaimer"

    prompt = render_prompt(
        _context(),
        "What does Article 1318 require?",
        ["What does Article 1318 require?"],
        disclaimer=disclaimer,
    )
    lowered = prompt.lower()

    # (a) answer only from the evidence given.
    assert "evidence" in lowered, "the prompt never tells the model to use the evidence"
    assert _contains_any(
        lowered,
        ("only", "strictly", "solely", "exclusively", "no other source", "not invent"),
    ), "the prompt does not restrict the answer to the evidence given"

    # (b) every factual sentence carries a [n] citation.
    has_bracket_n = re.search(r"\[\s*n\s*\]", prompt, flags=re.IGNORECASE) is not None
    has_bracket_number = re.search(r"\[\s*\d+\s*\]", prompt) is not None
    mentions_citation = "cite" in lowered or "citation" in lowered
    assert mentions_citation, "the prompt never asks for citations"
    assert has_bracket_n or has_bracket_number, (
        "the prompt does not state the [n] citation form for a factual sentence"
    )

    # (c) refuse when the evidence does not answer the question.
    assert _contains_any(
        lowered,
        (
            "refus",
            "cannot answer",
            "unable to answer",
            "do not answer",
            "declin",
            "not answer",
        ),
    ), "the prompt does not state the refusal behaviour when the evidence is silent"

    # (d) the persistent disclaimer, verbatim.
    assert disclaimer in prompt, (
        "the rendered prompt does not carry the persistent disclaimer wording "
        "from apps/web/src/lib/copy.ts"
    )


# ---------------------------------------------------------------------------
# 3. The advice blocklist trips on the four phrases — and only on them
# ---------------------------------------------------------------------------


def test_generation_blocklist_trips_on_the_four_phrases() -> None:
    """The oracle rejects the four advice phrases, case- and punctuation-blind.

    CONSTRAINTS.md: no legal-advice language in prompts/UI. The oracle is
    case-insensitive and must survive surrounding punctuation and quoting,
    because a model wraps advice in quotation marks and trailing periods.
    Ordinary legal prose must NOT trip it — a blocklist that fires on statute
    wording would refuse every answer.
    """
    try:
        from apps.api.generation.blocklist import contains_advice, find_advice
    except ImportError as exc:  # pragma: no cover - the RED phase
        pytest.fail(f"apps.api.generation.blocklist is not importable: {exc}")

    tripping = [
        "You should consult a lawyer before acting.",
        "I advise you to file the claim within one year.",
        "In your case, the remedy is rescission.",
        "You must file the answer within the period fixed by law.",
    ]
    for sample in tripping:
        assert contains_advice(sample), f"the oracle missed the advice in: {sample!r}"
        found = find_advice(sample)
        assert found, f"find_advice returned nothing for: {sample!r}"
        for phrase in found:
            assert phrase.lower() in ADVICE_PHRASE_SET, (
                f"find_advice reported an unknown phrase {phrase!r}"
            )

    # Case-insensitive.
    assert contains_advice("YOU SHOULD appeal.")
    assert contains_advice("You Should appeal.")
    assert contains_advice("you must comply.")

    # Robust to surrounding punctuation and quoting.
    punctured = [
        '"You should," the court wrote.',
        "The note said: 'I advise' in 1950.",
        "(your case)",
        "you must.",
        "you should,",
    ]
    for sample in punctured:
        assert contains_advice(sample), f"punctuation defeated the oracle: {sample!r}"

    # Ordinary legal prose must not trip the oracle.
    innocent = [
        "Article 1318 requires that the demand for payment be in writing.",
        "The obligation to pay the price arises upon tender of delivery.",
        "Damages are recoverable in the cases provided by law, and the seller "
        "may rescind the sale when the buyer fails to comply.",
        "Written notice is sufficient when sent to the address of the buyer.",
    ]
    for sample in innocent:
        assert not contains_advice(sample), f"the oracle fired on ordinary prose: {sample!r}"
        assert find_advice(sample) == [], f"find_advice fired on ordinary prose: {sample!r}"


# ---------------------------------------------------------------------------
# 4. The blocklist is the SINGLE oracle, and the service refuses on it
# ---------------------------------------------------------------------------


def test_generation_blocklist_catches_the_plural_and_the_contraction() -> None:
    """The oracle must not have a hole where the advice slips through.

    "your case" and "your cases" are the same advice, and "you shouldn't" and
    "you shouldnt" are the same advice. A whole-word boundary that requires a
    word/non-word transition let both through, and an advice-bearing answer
    would have shipped with citations intact.
    """
    from apps.api.generation.blocklist import contains_advice

    for text in (
        "In your cases, the seller must give notice before rescission.",
        "Your cases are governed by the Code.",
        "You shouldnt file suit.",
        "You shouldn't file suit.",
        "In your case's absence, the rule differs.",
    ):
        assert contains_advice(text), f"the oracle missed {text!r}"

    # The matcher must still not trip on a longer word that merely contains
    # the phrase, and not on ordinary legal prose.
    for text in (
        "your casesick analysis",
        "shoulding a party",
        "The contract shall be performed.",
        "Article 1318 requires consent of the parties.",
    ):
        assert not contains_advice(text), f"the oracle tripped on {text!r}"


def test_generation_blocklist_is_the_single_oracle() -> None:
    """One place knows the phrases, and the service refuses on that oracle.

    The defect this catches: a second copy of the phrase list (in the
    service, the interface, the citation assembler) drifting from the
    blocklist, or a service that ships the advice-bearing text anyway
    ("partially-advice answer"). The refusal must be the SAME uniform shape
    as a gate failure (api.md sec 3), never a half-answer.
    """
    try:
        from apps.api.generation.blocklist import ADVICE_PHRASES
        from apps.api.generation.interface import LLMInterface
        from apps.api.generation.service import GenerationService
    except ImportError as exc:  # pragma: no cover - the RED phase
        pytest.fail(f"the generation package is not importable: {exc}")

    # (a) The interface exposes the ADR-004 surface the service calls.
    assert callable(getattr(LLMInterface, "generate", None)), (
        "LLMInterface does not expose generate()"
    )
    assert callable(getattr(LLMInterface, "generate_stream", None)), (
        "LLMInterface does not expose generate_stream()"
    )

    # (b) The oracle knows exactly the four contract phrases.
    assert {phrase.lower() for phrase in ADVICE_PHRASES} == set(ADVICE_PHRASE_SET), (
        "the advice oracle does not carry exactly the four contract phrases"
    )

    # (c) No second copy of the phrases in executable code. The blocklist owns
    #     them; any other module that names them must read them from the
    #     oracle, so the two cannot drift apart.
    if not GENERATION_DIR.is_dir():
        pytest.fail(f"the generation package directory is missing: {GENERATION_DIR}")
    blocklist_path = GENERATION_DIR / "blocklist.py"
    if not blocklist_path.exists():
        pytest.fail(f"the blocklist module is missing: {blocklist_path}")
    blocklist_code = " ".join(_code_strings(blocklist_path)).lower()
    for phrase in ADVICE_PHRASE_SET:
        assert phrase in blocklist_code, (
            f"the blocklist module does not carry the phrase {phrase!r} in code"
        )

    for module_path in sorted(GENERATION_DIR.glob("*.py")):
        if module_path.name == "blocklist.py":
            continue
        code = " ".join(_code_strings(module_path)).lower()
        named = [phrase for phrase in ADVICE_PHRASE_SET if phrase in code]
        if len(named) < 2:
            # A single incidental mention in a prompt instruction is prose,
            # not a duplicated oracle. A LIST of phrases is a second copy.
            continue
        assert _reads_the_blocklist(module_path), (
            f"{module_path.name} names the advice phrases {named} in code instead of "
            "reading them from the oracle; the blocklist is the single source"
        )

    # (d) The service refuses on the oracle: an answer containing an advice
    #     phrase yields the uniform refusal shape, never a partial answer.
    advice_answer = "You should file a claim within one year of the demand."
    fake = _FakeLLM(advice_answer)
    service = GenerationService(cast("LLMInterface", fake))

    result = service.answer(
        _context(),
        "What does Article 1318 require?",
        ["What does Article 1318 require?"],
        _evidence(),
        gate_passed=True,
    )

    assert result["insufficient"], (
        "an advice-bearing answer was shipped as a normal answer; the oracle must refuse it"
    )
    assert result["citations"] == [], "a refused answer must carry no citations"
    answer = str(result["answer_markdown"])
    assert answer.strip(), "the refusal carries no text"
    assert advice_answer not in answer, (
        "the advice-bearing text was shipped instead of a refusal"
    )

    # The refusal has the SAME shape as a gate failure (api.md sec 3): the
    # same keys, the same empty citation list, insufficient true.
    gate_failure = service.answer(
        _context(),
        "What does Article 1318 require?",
        ["What does Article 1318 require?"],
        _evidence(),
        gate_passed=False,
    )
    assert set(result.keys()) == set(gate_failure.keys()), (
        "the advice refusal does not have the same shape as the gate failure"
    )
    assert result["citations"] == gate_failure["citations"] == []
    assert result["insufficient"]
    assert result["answer_markdown"] == gate_failure["answer_markdown"], (
        "the advice refusal does not carry the same refusal wording as a gate failure"
    )
    assert result["answer_markdown"] == CANNED_ABSTENTION, (
        "the advice refusal does not carry the uniform canned abstention wording"
    )


# ---------------------------------------------------------------------------
# 5. Citation assembly
# ---------------------------------------------------------------------------


def test_generation_citations_shape() -> None:
    """citations[] = {n, node_id, label, page_start, page_end}, numbered from 1.

    The page span comes from the KEPT EVIDENCE: a PromptContext part carries
    only {node_id, text, score} (retrieval.md sec 5), so a renderer that
    took the span from the parts could only invent it. A node that is not in
    the evidence set must never appear with a page span.
    """
    try:
        from apps.api.generation.citations import assemble_citations
    except ImportError as exc:  # pragma: no cover - the RED phase
        pytest.fail(f"apps.api.generation.citations.assemble_citations is not importable: {exc}")

    parts = [
        {"node_id": "node_civil-code-public_article_1318", "text": "alpha text", "score": 0.91},
        {"node_id": "node_civil-code-public_article_1319", "text": "beta text", "score": 0.55},
        {"node_id": "node_civil-code-public_article_1320", "text": "gamma text", "score": 0.20},
        # A part whose node is NOT in the evidence set: it must not be cited
        # with a page span.
        {"node_id": "node_ghost_article_9999", "text": "delta text", "score": 0.10},
    ]
    evidence = _evidence()
    evidence_ids = {str(item["node_id"]) for item in evidence["kept"]}

    citations = assemble_citations(parts, evidence)

    assert isinstance(citations, list)
    assert citations, "no citations were assembled for cited evidence"

    for citation in citations:
        assert {"n", "node_id", "label", "page_start", "page_end"} <= set(citation.keys()), (
            f"a citation misses a contract key: {sorted(citation.keys())}"
        )
        assert isinstance(citation["n"], int) and citation["n"] >= 1
        assert isinstance(citation["node_id"], str) and citation["node_id"]
        assert isinstance(citation["label"], str) and citation["label"], (
            "a citation carries an empty label"
        )

    # n starts at 1 and rises with the rendered order.
    numbers = [int(citation["n"]) for citation in citations]
    assert numbers[0] == 1, "the citation numbering does not start at 1"
    assert numbers == sorted(numbers), "the citation numbers are not in the rendered order"
    assert len(set(numbers)) == len(numbers), "the citation numbers repeat"

    # The order follows the evidence as it was rendered.
    cited_ids = [str(citation["node_id"]) for citation in citations]
    backed = [node_id for node_id in cited_ids if node_id in evidence_ids]
    expected_order = [
        str(item["node_id"]) for item in evidence["kept"] if str(item["node_id"]) in backed
    ]
    assert backed == expected_order, (
        "the citations are not in the order the evidence was rendered"
    )

    # The page span comes from the EVIDENCE (the parts carry none).
    by_id = {str(citation["node_id"]): citation for citation in citations}
    alpha = by_id.get("node_civil-code-public_article_1318")
    assert alpha is not None, "a kept, cited node produced no citation"
    assert alpha["page_start"] == 17, "the citation page span is not the evidence page span"
    assert alpha["page_end"] == 18, "the citation page span is not the evidence page span"
    for part in parts:
        assert "page_start" not in part and "page_end" not in part, (
            "the fixture is wrong: the context parts must carry no page span"
        )

    # A node outside the evidence set is never given an invented page span.
    for citation in citations:
        node_id = str(citation["node_id"])
        if node_id not in evidence_ids:
            assert citation["page_start"] is None and citation["page_end"] is None, (
                f"a node outside the evidence set ({node_id}) was cited with an "
                "invented page span"
            )


# ---------------------------------------------------------------------------
# 6. The refusal shape
# ---------------------------------------------------------------------------


def test_generation_refusal_shape() -> None:
    """A failed gate returns the canned abstention, no citations, insufficient.

    api.md sec 3: the abstention returns the SAME AskResponse shape with
    answer_markdown = the canned text, citations = [], insufficient = true.
    The wording is pinned in CANNED_ABSTENTION (apps/api/agent/graph.py) and
    is imported here, never re-spelled.
    """
    try:
        from apps.api.generation.interface import LLMInterface
        from apps.api.generation.service import GenerationService
    except ImportError as exc:  # pragma: no cover - the RED phase
        pytest.fail(f"the generation package is not importable: {exc}")

    assert callable(getattr(LLMInterface, "generate", None)), (
        "LLMInterface does not expose generate()"
    )

    fake = _FakeLLM("An answer that must not reach the client.")
    service = GenerationService(cast("LLMInterface", fake))

    result = service.answer(
        _context(),
        "What does Article 1318 require?",
        ["What does Article 1318 require?"],
        _evidence(),
        gate_passed=False,
    )

    assert result["answer_markdown"] == CANNED_ABSTENTION
    assert result["citations"] == []
    assert result["insufficient"]


# ---------------------------------------------------------------------------
# 7. A dead backend is a typed 503
# ---------------------------------------------------------------------------


def test_generation_llm_unavailable_is_503() -> None:
    """An unreachable backend raises LLM_UNAVAILABLE / 503 (ADR-004).

    api.md sec 1: LLM_UNAVAILABLE is a 503. The error must be typed — it
    carries a code and a status, and the uniform error body — so the route
    layer can map it without string matching.
    """
    try:
        from apps.api.generation.interface import LLMInterface
    except ImportError as exc:  # pragma: no cover - the RED phase
        pytest.fail(f"apps.api.generation.interface.LLMInterface is not importable: {exc}")

    llm = LLMInterface(base_url=f"http://127.0.0.1:{_closed_port()}", timeout=5.0)

    with pytest.raises(Exception) as excinfo:
        llm.generate("Reply with the single word: ready.")

    error = excinfo.value
    assert getattr(error, "code", None) == "LLM_UNAVAILABLE", (
        f"a dead backend raised {error!r} instead of a typed LLM_UNAVAILABLE"
    )
    assert getattr(error, "status", None) == 503, (
        f"the LLM_UNAVAILABLE error carries status {getattr(error, 'status', None)}, not 503"
    )
    body = getattr(error, "body", None)
    if callable(body):
        payload = body()
        assert payload["error"]["code"] == "LLM_UNAVAILABLE"
        assert payload["error"]["message"], "the LLM_UNAVAILABLE error carries no message"


# ---------------------------------------------------------------------------
# 8. Every result records the model, backend, temperature and seed
# ---------------------------------------------------------------------------


def test_generation_records_model_backend_temperature_seed() -> None:
    """ADR-004: model id + backend + temperature + seed are recorded.

    A run whose numbers cannot be compared to another run is not a run:
    the recorded seed is what makes a generation reproducible, and the
    temperature of 0 is what makes it a measurement rather than a sample.
    """
    try:
        from apps.api.generation.interface import GenerationResult, LLMInterface
        from apps.api.generation.service import GenerationService
    except ImportError as exc:  # pragma: no cover - the RED phase
        pytest.fail(f"the generation package is not importable: {exc}")

    # (a) The interface exposes the ADR-004 surface.
    assert callable(getattr(LLMInterface, "generate", None)), (
        "LLMInterface does not expose generate()"
    )

    # (b) The result object records all four.
    recorded = GenerationResult(
        text="An answer.",
        model="qwen2.5:3b",
        backend="ollama",
        temperature=0.0,
        seed=42,
        prompt_tokens=13,
        completion_tokens=5,
    )
    assert recorded.model == "qwen2.5:3b"
    assert recorded.backend == "ollama"
    assert recorded.temperature == 0.0
    assert recorded.seed == 42

    # (c) The service reports all four on the answer it returns.
    fake = _FakeLLM(
        "Article 1318 provides that the demand for payment must be in writing [1].",
        model="recorded-model",
        backend="ollama",
        temperature=0.0,
        seed=42,
    )
    service = GenerationService(cast("LLMInterface", fake))

    result = service.answer(
        _context(),
        "What does Article 1318 require?",
        ["What does Article 1318 require?"],
        _evidence(),
        gate_passed=True,
    )

    assert result.get("model") == "recorded-model", "the answer does not record the model id"
    assert result.get("backend") == "ollama", "the answer does not record the backend"
    assert float(result.get("temperature", -1)) == 0.0, (
        "the answer does not record the temperature"
    )
    assert int(result.get("seed", -1)) == 42, "the answer does not record the seed"
    tokens_source = result.get("tokens_source")
    if tokens_source is not None:
        assert tokens_source in {"estimated", "provider"}, (
            f"an unknown tokens_source value: {tokens_source!r}"
        )


# ---------------------------------------------------------------------------
# 9. A dead backend never degrades into a fake answer
# ---------------------------------------------------------------------------


def test_generation_stream_yields_the_providers_deltas() -> None:
    """The stream yields the BACKEND'S deltas, not a slicing of the text.

    The Day-3 implementation cut a finished answer into 80-character pieces,
    which looked streamed but was not: the model had already finished before
    the first delta was produced. The deltas are now the ones the backend
    emitted, in its order, and they concatenate to exactly the answer.

    A fake llm records what it was asked for, so this needs no live model.
    """
    from apps.api.generation.interface import GenerationResult
    from apps.api.generation.service import GenerationService

    class _FakeStreamingLLM:
        model = "fake-model"
        backend = "ollama"
        temperature = 0.0
        seed = 42

        def __init__(self) -> None:
            self.deltas = ["The ", "answer ", "is ", "here."]

        def generate(
            self,
            prompt: str,
            stop: list[str] | None = None,
            max_tokens: int | None = None,
        ) -> GenerationResult:
            return GenerationResult(
                text="".join(self.deltas),
                model=self.model,
                backend=self.backend,
                temperature=self.temperature,
                seed=self.seed,
                prompt_tokens=4,
                completion_tokens=4,
            )

        def stream_result(
            self,
            prompt: str,
            stop: list[str] | None = None,
            max_tokens: int | None = None,
        ) -> tuple[GenerationResult, list[str]]:
            return self.generate(prompt, stop, max_tokens), list(self.deltas)

    llm = _FakeStreamingLLM()
    service = GenerationService(cast("LLMInterface", llm))
    result = service.answer(
        {"parts": [{"node_id": "n1", "text": "x" * 100, "score": 1.0}]},
        "q",
        ["q"],
        {"kept": [{"node_id": "n1", "title": "t", "page_start": 1, "page_end": 1}]},
        gate_passed=True,
    )
    # The deltas travel with the answer, individually.
    assert result["stream"] == ["The ", "answer ", "is ", "here."]
    assert "".join(result["stream"]) == result["answer_markdown"]


@pytest.fixture(scope="module")
def civil_code(tmp_path_factory: pytest.TempPathFactory) -> Any:
    """The real Civil Code, ingested once, for the tests that need a store.

    The house pattern (apps/api/tests/test_retrieval.py, test_agent.py): ingest
    the corpus into a tmp database, build the per-document indexes, and point
    the runtime lex path at this module's index root.
    """
    import os as _os

    from apps.api.ingestion.service import ingest_pdf
    from apps.api.retrieval.index import build_indexes

    repo_root = Path(__file__).resolve().parents[3]
    corpus = repo_root / "data" / "documents" / "civil-code-ra386-icnl.pdf"
    if not _os.path.exists(corpus):
        pytest.skip("corpus file missing: civil-code-ra386-icnl.pdf")
    db_path = str(tmp_path_factory.mktemp("generation") / "civil-code.db")
    index_root = tmp_path_factory.mktemp("generation-indexes")
    doc = ingest_pdf(str(corpus), slug="civil-code-public", db_path=db_path)
    build_indexes(db_path, doc.id, index_root=index_root)

    import apps.api.retrieval.scoring as _scoring

    original_root = _scoring.INDEX_ROOT
    _scoring.INDEX_ROOT = index_root
    yield SimpleNamespace(db=db_path, doc_id=doc.id)
    _scoring.INDEX_ROOT = original_root


def test_generation_ask_trace_records_the_run(civil_code: SimpleNamespace) -> None:
    """ADR-004: the run is recorded, so a run can be named and compared.

    "Model id + backend + temperature + seed recorded in every ask trace and
    eval run." The trace is the `queries` row: without it, two asks that
    differ only in the model produce indistinguishable records, and a run
    cannot be compared.
    """
    import sqlite3

    from apps.api.agent.ask import run_ask

    response = run_ask(
        civil_code.doc_id,
        "What are the requisites of contracts?",
        db_path=civil_code.db,
    )
    conn = sqlite3.connect(civil_code.db)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("SELECT * FROM queries").fetchall()
    finally:
        conn.close()
    assert rows, "the ask must leave a trace"
    row = rows[-1]
    assert row["document_id"] == civil_code.doc_id
    assert row["question"] == "What are the requisites of contracts?"
    # The run record. The model-free generator runs no model, so the run
    # parameters are absent - but the columns exist and the row is complete
    # in the observable facts.
    for column in (
        "model",
        "backend",
        "temperature",
        "seed",
        "prompt_version",
        "iterations",
        "evidence_ids",
        "tokens_in",
        "tokens_out",
        "tokens_source",
        "latency_ms",
        "insufficient",
    ):
        assert column in row.keys(), f"the trace must carry {column}"
    assert row["iterations"] == response["iterations"]
    assert row["insufficient"] == (1 if response["insufficient"] else 0)
    assert row["latency_ms"] >= 0
    # The evidence ids are recorded, so the run names what it answered from.
    import json

    assert json.loads(row["evidence_ids"]) == list(response["evidence_ids"])


def test_generation_never_degrades_to_a_fake_answer() -> None:
    """The service raises on a dead backend; it never returns a canned answer.

    ADR-004: "LLM-down degrades to 503 LLM_UNAVAILABLE, never to a fake
    answer." A service that swallowed the failure and returned the canned
    abstention (or a stub answer) would look like a working system, so this
    asserts the raise itself.
    """
    try:
        from apps.api.generation.interface import LLMInterface
        from apps.api.generation.service import GenerationService
    except ImportError as exc:  # pragma: no cover - the RED phase
        pytest.fail(f"the generation package is not importable: {exc}")

    llm = LLMInterface(base_url=f"http://127.0.0.1:{_closed_port()}", timeout=5.0)
    service = GenerationService(llm)

    with pytest.raises(Exception) as excinfo:
        service.answer(
            _context(),
            "What does Article 1318 require?",
            ["What does Article 1318 require?"],
            _evidence(),
            gate_passed=True,
        )

    error = excinfo.value
    assert getattr(error, "code", None) == "LLM_UNAVAILABLE", (
        "a dead backend did not raise LLM_UNAVAILABLE; the service must not "
        "answer from nothing"
    )
    assert getattr(error, "status", None) == 503


# ---------------------------------------------------------------------------
# 10. The real local model (opt-in)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    os.environ.get("TUKLAS_LIVE_LLM") != "1",
    reason="live local LLM test: set TUKLAS_LIVE_LLM=1 to run it",
)
def test_generation_real_model_answers() -> None:
    """Against the running local ollama: a real answer and positive token counts.

    This proves the wiring to a real local backend rather than a mock. It is
    opt-in because the first call to a model has a cold start of tens of
    seconds; the fast suite must stay fast.
    """
    try:
        from apps.api.generation.interface import LLMInterface
    except ImportError as exc:  # pragma: no cover - the RED phase
        pytest.fail(f"apps.api.generation.interface.LLMInterface is not importable: {exc}")

    llm = LLMInterface()
    result = llm.generate("Reply with the single word: ready.")

    assert result.text.strip(), "the local model returned an empty answer"
    assert result.prompt_tokens is not None and result.prompt_tokens > 0, (
        f"the local model reported no prompt tokens: {result.prompt_tokens!r}"
    )
    assert result.completion_tokens is not None and result.completion_tokens > 0, (
        f"the local model reported no completion tokens: {result.completion_tokens!r}"
    )
    assert result.model, "the local result recorded no model id"
    assert result.backend, "the local result recorded no backend"
    assert float(result.temperature) == 0.0
    assert int(result.seed) == 42
