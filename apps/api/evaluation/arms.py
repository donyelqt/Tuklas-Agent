"""The two arms, behind one interface (evaluation.md §1, ADR-003).

    Arm A  "tuklas"  — the vectorless agent: BM25 + structural + reference,
                        with identifier precedence (ADR-005).
    Arm B  "vector"  — the dense baseline under ``experiments/vector/``.

The runner consumes ONE interface from both, so the arms differ only in
retrieval and generation. The interface is deliberately narrow:

* :meth:`Arm.retrieve` returns the ranked node ids for a question, plus the
  retrieval latency.
* :meth:`Arm.answer` returns an answer for a question, plus the generation
  latency, the token counts and the iteration count.

**The asymmetry that a reader of the numbers must know.** Arm A applies
identifier precedence: a query naming a provision puts that provision
first, and the arm has an ``ident`` component that no dense arm can have
(ADR-005: "the dense arm has no ident component at all"). The two arms
therefore differ in more than the retrieval MECHANISM — they differ in the
SIGNALS they can use. The ``direct-lookup`` category is the one where that
difference is sharpest, and it is not a like-for-like result. The outcome of
the comparison is UNKNOWN and nothing here assumes it.

**Arm B's answer path is the same agent.** The point of the experiment is
that the ARMS differ in retrieval; the generation must be the same model
with the same backend, the same temperature and the same seed
(evaluation.md §1: "Different models across arms = invalid run"). Arm B
therefore runs the SAME agent graph over the SAME corpus, with one
substitution: its ``search_nodes`` returns the dense ranking instead of
the vectorless one. The implementation is by TOOL SUBSTITUTION, not by a
second agent — a second agent would differ in more than retrieval, and
that is exactly the confound the experiment forbids.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from apps.api.agent import graph as agent_graph
from apps.api.agent import state as agent_state
from apps.api.agent.tools import TOOL_TABLE
from apps.api.retrieval import api as retrieval_api
from apps.api.retrieval.scoring import ScoredNode

_LOG = logging.getLogger(__name__)

#: The two arm identifiers, exactly as api.md §4 names them.
ARM_TUKLAS = "tuklas"
ARM_VECTOR = "vector"
ARM_NAMES: tuple[str, ...] = (ARM_TUKLAS, ARM_VECTOR)

#: How many candidates an arm returns. The vectorless arm's own retrieval
#: tool clamps at 20; the dense arm is clamped to the same number so the
#: two arms are compared over the same candidate window.
TOP_K = 20


@dataclass(frozen=True)
class RetrievalResult:
    """One arm's retrieval for one question."""

    node_ids: list[str]
    latency_ms: int
    components: dict[str, dict[str, float]] = field(default_factory=dict)

    def head(self, k: int) -> list[str]:
        """The top-``k`` node ids."""
        return self.node_ids[:k]


@dataclass(frozen=True)
class AnswerResult:
    """One arm's answer for one question."""

    answer_markdown: str
    citations: list[dict[str, Any]]
    evidence_ids: list[str]
    insufficient: bool
    iterations: int
    latency_ms: int
    retrieval_latency_ms: int
    generation_latency_ms: int
    tokens_in: int
    tokens_out: int
    tokens_source: str | None


class Arm(Protocol):
    """What the runner needs from an arm."""

    name: str

    def retrieve(self, question: str) -> RetrievalResult:
        """The ranked node ids for a question."""
        ...

    def answer(self, question: str, *, max_iterations: int = 12) -> AnswerResult:
        """The answer for a question."""
        ...

    def describe(self) -> dict[str, Any]:
        """The arm's own description, for the run config."""
        ...


# ---------------------------------------------------------------------------
# Arm A — the vectorless agent
# ---------------------------------------------------------------------------


class TuklasArm:
    """Arm A: the vectorless agent, exactly as the product runs it.

    The retrieval is the product's own ``search_nodes`` — BM25 + ident +
    title + ref + hier, with identifier precedence — and the answer is the
    product's own agent graph. Nothing here is an evaluation-only
    reimplementation, because an evaluation-only reimplementation would
    measure something the product does not do.
    """

    name = ARM_TUKLAS

    def __init__(
        self,
        db_path: str,
        document_id: str,
        *,
        generator: Any | None = None,
        index_root: Any | None = None,
    ) -> None:
        self.db_path = db_path
        self.document_id = document_id
        self._generator = generator
        if index_root is not None:
            # The per-document BM25 artifact lives under the index root
            # (retrieval.md §2). A run against a store other than the
            # default one must point the retrieval layer at the matching
            # index root, or the lex component would silently fall back to
            # the FTS5 path and score differently.
            from apps.api.retrieval import scoring

            scoring.INDEX_ROOT = index_root

    def retrieve(self, question: str) -> RetrievalResult:
        """The vectorless ranking, with the retrieval latency measured."""
        started = time.perf_counter()
        hits: list[ScoredNode] = retrieval_api.search_nodes(
            query=question,
            document_id=self.document_id,
            top_k=TOP_K,
            db_path=self.db_path,
        )
        latency_ms = int((time.perf_counter() - started) * 1000)
        return RetrievalResult(
            node_ids=[hit.node_id for hit in hits],
            latency_ms=latency_ms,
            components={hit.node_id: dict(hit.components) for hit in hits},
        )

    def answer(self, question: str, *, max_iterations: int = 12) -> AnswerResult:
        """The agent's answer, with the latencies split.

        The split is measured, not estimated: the retrieval time is the
        sum of the ``search_nodes`` tool latencies the agent's own trace
        recorded, and the generation time is the total minus that. The
        agent's trace carries the per-call latency of every tool wrapper,
        so the split comes from the run itself.
        """
        started = time.perf_counter()
        state = self._run(question, max_iterations=max_iterations)
        total_ms = int((time.perf_counter() - started) * 1000)
        retrieval_ms = _retrieval_latency(dict(state))
        return AnswerResult(
            answer_markdown=str(state.get("answer_md", "")),
            citations=list(state.get("citations", []) or []),
            evidence_ids=list(state.get("evidence_ids", []) or []),
            insufficient=bool(state.get("insufficient", False)),
            iterations=int(state.get("tool_iterations", 0)),
            latency_ms=total_ms,
            retrieval_latency_ms=retrieval_ms,
            generation_latency_ms=max(0, total_ms - retrieval_ms),
            tokens_in=int(state.get("_gen_prompt_tokens", 0) or 0),
            tokens_out=int(state.get("_gen_completion_tokens", 0) or 0),
            tokens_source=state.get("_gen_tokens_source"),
        )

    def _run(self, question: str, *, max_iterations: int) -> agent_state.AgentState:
        """Run the agent graph, with the generator the caller chose."""
        generator = self._generator
        if generator is None:
            from apps.api.generation.config import generator_from_env

            generator = generator_from_env()
        return agent_graph.run_agent(
            self.document_id,
            question,
            max_iterations=max_iterations,
            db_path=self.db_path,
            generator=generator,
        )

    def describe(self) -> dict[str, Any]:
        """The arm's own description, for the run config.

        The vectorless arm's identity is its SCORING: the five
        retrieval.md §3 components, the identifier precedence of ADR-005,
        and the absence of any dense machinery. The last two lines are
        the ones a reader comparing the arms needs.
        """
        return {
            "algorithm": "vectorless-five-component",
            "components": ["lex", "ident", "title", "ref", "hier"],
            "ident_component": "art 1318 -> exact normalized article id",
            "identifier_precedence": True,
            "dense": False,
        }


# ---------------------------------------------------------------------------
# Arm B — the dense baseline
# ---------------------------------------------------------------------------


class VectorArm:
    """Arm B: the dense baseline, with the SAME agent over the SAME corpus.

    The agent graph is the product's own graph. The ONE substitution is the
    ``search_nodes`` tool: it returns the dense ranking instead of the
    vectorless one. Everything downstream — the planner, the inspector, the
    evidence gate, the context builder, the generation model — is the same
    code with the same parameters.

    That is the whole point of the parity boundary (ADR-003, ADR-004): if
    Arm B ran its own planner or its own generation, a difference in the
    numbers would not be attributable to retrieval.
    """

    name = ARM_VECTOR

    def __init__(
        self,
        db_path: str,
        document_id: str,
        *,
        vector_index: Any | None = None,
        generator: Any | None = None,
    ) -> None:
        self.db_path = db_path
        self.document_id = document_id
        self._vector_index = vector_index
        self._generator = generator
        self._corpus: list[Any] = []

    # -- the corpus --------------------------------------------------------

    def corpus(self) -> list[Any]:
        """The document's nodes, as the dense arm's corpus entries.

        The harness reads the store — the dense arm must not, because
        architecture.md §2 gives ``experiments/vector/`` no imports from
        the primary path.

        ``document_id`` may be an id OR a slug, exactly as the
        retrieval layer's own tools accept (retrieval.md §5: "resolves
        documents by id OR slug"). The two arms must retrieve over the
        SAME nodes, so the dense arm resolves the slug the same way the
        vectorless one does — a dense arm that silently indexed an empty
        corpus would answer every question with a refusal, and the run
        would report a number that meant nothing.
        """
        if self._corpus:
            return self._corpus
        from experiments.vector.index import CorpusEntry

        with _connect(self.db_path) as conn:
            document = conn.execute(
                "SELECT id FROM documents WHERE id = ? OR slug = ?",
                (self.document_id, self.document_id),
            ).fetchone()
            if document is None:
                return []
            rows = conn.execute(
                "SELECT id, title, text, page_start, depth, position FROM nodes"
                " WHERE document_id = ? ORDER BY depth, position",
                (str(document["id"]),),
            ).fetchall()
        self._corpus = [
            CorpusEntry(
                node_id=str(row["id"]),
                title=str(row["title"]),
                text=str(row["text"]),
                page_start=int(row["page_start"]) if row["page_start"] is not None else None,
                depth=int(row["depth"]),
                position=int(row["position"]),
            )
            for row in rows
        ]
        return self._corpus

    def index(self) -> Any:
        """The dense index, built on first use."""
        if self._vector_index is None:
            from experiments.vector.index import build_index

            self._vector_index = build_index(self.corpus())
        return self._vector_index

    def describe(self) -> dict[str, Any]:
        """The arm's own description, for the run config."""
        description: dict[str, Any] = self.index().describe()
        return description

    # -- the interface -----------------------------------------------------

    def retrieve(self, question: str) -> RetrievalResult:
        """The dense ranking, with the retrieval latency measured."""
        started = time.perf_counter()
        hits = self.index().search(question, top_k=TOP_K)
        latency_ms = int((time.perf_counter() - started) * 1000)
        return RetrievalResult(
            node_ids=[str(hit["node_id"]) for hit in hits],
            latency_ms=latency_ms,
            components={str(hit["node_id"]): dict(hit["components"]) for hit in hits},
        )

    def answer(self, question: str, *, max_iterations: int = 12) -> AnswerResult:
        """The agent's answer, with the dense retrieval substituted in.

        The agent's own tool table is used, with ONE entry replaced. The
        replacement is a wrapper around the dense index that returns the
        same hit objects the vectorless tool returns — ``ScoredNode`` —
        so the agent's own relevance test (which reads
        ``components["lex"]`` and ``components["ident"]``) and the
        evidence gate behave identically for both arms.
        """
        started = time.perf_counter()
        state = self._run(question, max_iterations=max_iterations)
        total_ms = int((time.perf_counter() - started) * 1000)
        retrieval_ms = _retrieval_latency(dict(state))
        return AnswerResult(
            answer_markdown=str(state.get("answer_md", "")),
            citations=list(state.get("citations", []) or []),
            evidence_ids=list(state.get("evidence_ids", []) or []),
            insufficient=bool(state.get("insufficient", False)),
            iterations=int(state.get("tool_iterations", 0)),
            latency_ms=total_ms,
            retrieval_latency_ms=retrieval_ms,
            generation_latency_ms=max(0, total_ms - retrieval_ms),
            tokens_in=int(state.get("_gen_prompt_tokens", 0) or 0),
            tokens_out=int(state.get("_gen_completion_tokens", 0) or 0),
            tokens_source=state.get("_gen_tokens_source"),
        )

    def _run(self, question: str, *, max_iterations: int) -> agent_state.AgentState:
        """Run the agent graph with the dense ``search_nodes`` in place."""
        generator = self._generator
        if generator is None:
            from apps.api.generation.config import generator_from_env

            generator = generator_from_env()
        dense_search = self._dense_search_tool()
        original = TOOL_TABLE["search_nodes"]
        TOOL_TABLE["search_nodes"] = dense_search
        try:
            return agent_graph.run_agent(
                self.document_id,
                question,
                max_iterations=max_iterations,
                db_path=self.db_path,
                generator=generator,
            )
        finally:
            TOOL_TABLE["search_nodes"] = original

    def _dense_search_tool(self) -> Any:
        """A ``search_nodes`` tool wrapper backed by the dense index.

        The wrapper mirrors the vectorless one in what it RETURNS (a list
        of ``ScoredNode``) and in what it RECORDS (the tool trace entry
        and the budget increment). It is a substitution of the RETRIEVAL,
        not a fork of the agent.
        """
        index = self.index()

        def dense_search_nodes(state: agent_state.AgentState, args: dict[str, Any]) -> agent_state.AgentState:
            query = str(args["query"])
            top_k = min(20, max(1, int(args.get("top_k", 10))))
            db_path = state.get("_db_path")
            started = time.perf_counter()
            hits = index.search(query, top_k=top_k)
            latency = (time.perf_counter() - started) * 1000.0
            scored = [
                ScoredNode(
                    node_id=str(hit["node_id"]),
                    title=str(hit["title"]),
                    snippet=str(hit["snippet"]),
                    score=float(hit["score"]),
                    components={key: float(value) for key, value in hit["components"].items()},
                    page_start=hit["page_start"],
                    depth=int(hit.get("depth", 0)),
                    position=int(hit.get("position", 0)),
                )
                for hit in hits
            ]
            matched = [
                hit
                for hit in scored
                if hit.components.get("lex", 0.0) > 0.0
                or hit.components.get("ident", 0.0) > 0.0
            ]
            hit_ids = [hit.node_id for hit in matched]
            by_question = dict(state.get("_hits_by_question", {}))
            by_question.setdefault(query, []).extend(matched)
            state["_hits_by_question"] = by_question
            state["_hits"] = list(state.get("_hits", [])) + list(matched)
            state["_searched"] = list(state.get("_searched", [])) + [query]
            state["tool_iterations"] = int(state.get("tool_iterations", 0)) + 1
            branch_id = f"search_nodes#{state['tool_iterations']}"
            state["tool_results"].append(
                {
                    "tool": "search_nodes",
                    "args": {"query": query, "top_k": top_k},
                    "result_ids": hit_ids,
                    "relevant": bool(hit_ids),
                    "latency_ms": round(latency, 3),
                }
            )
            state["trace"].append(
                {
                    "tool": "search_nodes",
                    "args": {"query": query, "top_k": top_k},
                    "result_ids": hit_ids,
                    "relevant": bool(hit_ids),
                    "branch_id": branch_id,
                    "latency_ms": round(latency, 3),
                }
            )
            if db_path is None:
                state["_db_path"] = db_path
            return state

        return dense_search_nodes


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _connect(db_path: str) -> Any:
    """A read connection to the store, in the ingestion module's style."""
    from apps.api.ingestion import db as ingestion_db

    return ingestion_db.connect(db_path)


def _retrieval_latency(state: dict[str, Any]) -> int:
    """The retrieval time inside one agent run, in milliseconds.

    The agent's trace carries the measured latency of every tool wrapper
    call. The retrieval time is the sum of the ``search_nodes`` calls'
    latencies; the rest of the tool time (inspect, expand) is retrieval
    too, so the sum of every TOOL call's latency is the retrieval cost,
    and the generation cost is what the total has left over.

    The split is a MEASUREMENT of the run, and it is reported as such: a
    run whose tool trace is empty (no search happened) has a retrieval
    latency of 0, not a guess.
    """
    total = 0.0
    for entry in state.get("trace", []) or []:
        if not isinstance(entry, dict):
            continue
        if "tool" not in entry:
            continue
        latency = entry.get("latency_ms")
        if isinstance(latency, int | float):
            total += float(latency)
    return int(total)


__all__ = [
    "ARM_NAMES",
    "ARM_TUKLAS",
    "ARM_VECTOR",
    "TOP_K",
    "AnswerResult",
    "Arm",
    "RetrievalResult",
    "TuklasArm",
    "VectorArm",
]
