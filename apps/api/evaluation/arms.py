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

**The metric comes from the ANSWER path, not from a second search.** The
agent splits a question into sub-questions and calls ``search_nodes``
once PER SUB-QUESTION, so the retrieval an answer was produced from is
the UNION of those calls' result ids, in the order the agent got them.
A separate search over the whole question produces a DIFFERENT ranking —
one the model never saw — and a metric computed over it describes a
retrieval that did not happen. So both arms answer through the agent
graph ONCE per question and the retrieval metric is computed over the
node ids that same run retrieved. The stored rows and the report read
that list, so the numbers and the answers come from one retrieval.

**The two arms score the SAME lexical surface.** The dense arm's corpus
is the node set the vectorless arm's BM25 corpus covers (leaf-node bodies
and section-intro nodes), and the dense arm's query carries the same
identifier-free surface the vectorless arm's lexical query has. The
rule that decides the corpus lives in the primary path
(``retrieval.index.in_lex_corpus``) and is imported by the harness, so
there is exactly ONE definition of it and the two arms cannot drift.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from apps.api.agent import graph as agent_graph
from apps.api.agent import state as agent_state
from apps.api.agent.tools import TOOL_TABLE
from apps.api.retrieval import normalizer
from apps.api.retrieval.index import in_lex_corpus
from apps.api.retrieval.scoring import (
    WEIGHT_DEFAULTS,
    ScoredNode,
    format_lex_path,
)

_LOG = logging.getLogger(__name__)

#: The two arm identifiers, exactly as api.md §4 names them.
ARM_TUKLAS = "tuklas"
ARM_VECTOR = "vector"
ARM_NAMES: tuple[str, ...] = (ARM_TUKLAS, ARM_VECTOR)

#: How many candidates an arm returns. The vectorless arm's own retrieval
#: tool clamps at 20; the dense arm is clamped to the same number so the
#: two arms are compared over the same candidate window.
TOP_K = 20

#: The vectorless arm's surface, in one sentence, for the run config and
#: the report. It names WHAT the arm scores, because a reader comparing
#: the two arms' numbers has to know the surfaces they were scored over.
SURFACE_TUKLAS = (
    "per-document BM25 over leaf-node bodies and section-intro nodes, with the "
    "query's identifier tokens stripped from the lexical input"
)

#: The dense arm's surface, in one sentence, for the run config and the
#: report.
SURFACE_VECTOR = (
    "one dense embedding per node over the node's own text (which begins with "
    "its heading line), scored by cosine similarity over the same lexical "
    "node set the vectorless arm's BM25 corpus covers"
)


#: The retrieval INSTRUMENT that produced the metric THIS harness records:
#: the metric is computed over the node ids the ANSWER was produced from,
#: the union of the agent's per-sub-question ``search_nodes`` results.
#:
#: This is the instrument the current code implements; the constant exists
#: so the run row carries a versioned NAME for it rather than a fact a
#: reader has to infer from the code version. A PREVIOUS harness computed
#: the metric over a separate, one-off search over the whole question — a
#: ranking the model never saw. The two measure different retrievals, so
#: their numbers are not comparable, and a run row recorded under either
#: instrument names which one it used:
RETRIEVAL_INSTRUMENT_ANSWER_NODE_UNION = "answer-node-union-v1"

#: The OLD instrument, named for the record: a separate one-off search
#: over the whole question, a ranking the agent never saw. NO code path
#: uses it any more; it exists so a reader of an old run row — or of
#: this repo's history — has a name for what those numbers were
#: measured over, and so the current constant's meaning is defined
#: against its counterpart rather than against nothing.
RETRIEVAL_INSTRUMENT_WHOLE_QUESTION_SEARCH = "whole-question-search-v0"


@dataclass(frozen=True)
class RetrievalResult:
    """One arm's retrieval for one question.

    ``node_ids`` is the retrieval the ANSWER was produced from: the union
    of the per-sub-question ``search_nodes`` result ids, in the order the
    agent received them. It is NOT a separate search over the whole
    question — that ranking is one the model never saw, and a metric
    computed over it would describe a retrieval that did not happen.

    ``weights`` is the weight snapshot that produced the ranking, for the
    arm that has one. The dense arm has no five-component weight vector,
    so its ``weights`` is None.
    """

    node_ids: list[str]
    latency_ms: int
    components: dict[str, dict[str, float]] = field(default_factory=dict)
    weights: dict[str, float] | None = None
    #: The rendered lex paths the run's ``search_nodes`` calls took, as
    #: the retrieval layer reported them. Empty when no search ran; the
    #: caller records an empty list as not-recorded rather than guessing.
    lex_paths: list[str] = field(default_factory=list)

    def head(self, k: int) -> list[str]:
        """The top-``k`` node ids."""
        return self.node_ids[:k]


@dataclass(frozen=True)
class AnswerResult:
    """One arm's answer for one question.

    ``retrieved_node_ids`` is the retrieval the answer came from, and it
    is the SAME list as the arm's ``RetrievalResult.node_ids``: the agent
    runs once per question and both carry its node ids, so the metric and
    the answer can never come from two different searches.
    """

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
    retrieved_node_ids: list[str] = field(default_factory=list)
    weights: dict[str, float] | None = None
    #: Same fact as the retrieval path's: the lex paths the run's
    #: ``search_nodes`` calls took, as the layer reported them.
    lex_paths: list[str] = field(default_factory=list)


class Arm(Protocol):
    """What the runner needs from an arm."""

    name: str

    def retrieve(self, question: str, *, max_iterations: int = 12) -> RetrievalResult:
        """The ranked node ids for a question."""
        ...

    def answer(self, question: str, *, max_iterations: int = 12) -> AnswerResult:
        """The answer for a question."""
        ...

    def describe(self) -> dict[str, Any]:
        """The arm's own description, for the run config."""
        ...


# ---------------------------------------------------------------------------
# helpers shared by both arms
# ---------------------------------------------------------------------------


def dense_embedded_text(node_id: str, title: str, text: str) -> str:
    """The text the dense arm embeds for one node.

    The rule is the dense arm's own — it lives in ``experiments/vector/``
    and this module is the package's sole legal importer
    (architecture.md §2), so the harness NAMES the rule rather than
    restating it. A restatement would be a second rule that drifts, and
    the whole point of the rule is that it is ONE.

    The rule: the node's own text, ONCE. ``node.text`` already begins
    with the node's heading (the parser slices the body from the
    heading's own line onward), so a surface that prepended the title
    embedded the heading twice.
    """
    from experiments.vector.index import CorpusEntry, embeddable_text

    return embeddable_text(
        CorpusEntry(node_id=node_id, title=title, text=text)
    )


def identifier_free_query(query: str) -> str:
    """The query with its IDENTIFIER tokens removed.

    The vectorless arm's lexical query is the query minus the tokens an
    identifier span consumed (retrieval.md §3's disjoint-inputs rule:
    an identifier term fires in exactly one component, the symbolic one).
    The dense arm has ONE channel, so its query WAS the raw question —
    and a question naming "Article 1318" therefore carried the
    identifier on BOTH sides of the dense cosine while the vectorless
    arm was forbidden the lexical half of it. That is an undisclosed
    asymmetry in the surfaces, and it biases the comparison in a
    direction nobody chose.

    So the dense arm strips the same tokens, with the same normalizer
    the vectorless arm uses: ``normalizer.label_tokens`` is the ONE
    definition of "the words an identifier span consumed", and the two
    arms cannot drift apart on it.

    The word that CARRIES the identifier is dropped with its number
    ("article 1318" -> both words), because the normalizer consumed both:
    the label span is matched as a unit, and the vectorless arm's own
    lexical input has neither word. Stripping only the digits would
    leave a bare "article" on one side and nothing on the other, which
    is the same asymmetry in a smaller disguise.

    A query that IS an identifier and nothing else ("Article 1318")
    strips to nothing. The vectorless arm's lexical input is then empty
    too — its lex component is all-zero — and an empty embedding is a
    different vector from a non-empty one, so the dense arm embeds an
    EMPTY STRING rather than the identifier. That is the same surface
    the vectorless arm sees: neither arm gets a lexical signal from a
    bare identifier.
    """
    label_tokens = normalizer.label_tokens(query)
    if not label_tokens:
        return query
    kept = [
        word
        for word in re.split(r"(\W+)", query)
        if not (word.isalnum() and word.lower() in label_tokens)
    ]
    return "".join(kept).strip()


def _retrieved_node_ids(state: dict[str, Any]) -> list[str]:
    """The node ids the agent's search retrieved, in the agent's order.

    The agent splits a question into sub-questions and calls
    ``search_nodes`` once per sub-question, so the retrieval an answer
    rests on is the union of those calls' result ids. The union keeps
    the agent's ORDER (first sub-question's hits first) and drops
    repeats, because a node that ranks for two sub-questions is one
    retrieved node, and the metric's rank of the first gold hit has to
    be the rank the agent saw.

    The ids are read from the agent's own tool trace — the same records
    the agent's relevance test and its evidence gate read — so the
    metric cannot describe a different retrieval than the one the answer
    came from.
    """
    ordered: list[str] = []
    seen: set[str] = set()
    for entry in state.get("tool_results", []) or []:
        if not isinstance(entry, dict):
            continue
        if entry.get("tool") != "search_nodes":
            continue
        for node_id in entry.get("result_ids", []) or []:
            key = str(node_id)
            if key in seen:
                continue
            seen.add(key)
            ordered.append(key)
    return ordered


def _hit_components(state: dict[str, Any]) -> dict[str, dict[str, float]]:
    """The per-node component scores of the hits the agent retrieved.

    The components are read from the agent's own hit list, so the
    component breakdown a run records describes the same retrieval the
    metric is computed over.
    """
    components: dict[str, dict[str, float]] = {}
    for hit in state.get("_hits", []) or []:
        node_id = str(getattr(hit, "node_id", ""))
        if not node_id:
            continue
        raw = getattr(hit, "components", {}) or {}
        components[node_id] = {str(key): float(value) for key, value in raw.items()}
    return components


def _search_latency(state: dict[str, Any]) -> int:
    """The time the agent's ``search_nodes`` calls took, in milliseconds.

    Only the SEARCH calls count here, not every tool call: this is the
    latency of the retrieval that produced the ranking, and the arm's
    broader retrieval cost (inspect, expand) is reported separately by
    the answer path's own split.
    """
    total = 0.0
    for entry in state.get("tool_results", []) or []:
        if not isinstance(entry, dict) or entry.get("tool") != "search_nodes":
            continue
        latency = entry.get("latency_ms")
        if isinstance(latency, int | float):
            total += float(latency)
    return int(total)


def _lex_paths(state: dict[str, Any]) -> list[str]:
    """The lex paths the run's ``search_nodes`` calls actually took.

    Each entry is the rendered form of the lex-path info the RETRIEVAL
    LAYER returned for one call (``scoring.format_lex_path``) — recorded
    on the call's own trace by the tool wrapper, so this reads what ran
    rather than re-deriving it. A run makes several calls (one per
    sub-question), and they can in principle disagree — an artifact that
    exists for one document and not another — so the list is kept whole
    and the run row records it as the JSON list. An empty list means the
    agent ran no search (or ran one whose trace predates the reporting),
    and the run row's lex-path field is left NULL rather than guessed.
    """
    paths: list[str] = []
    for entry in state.get("tool_results", []) or []:
        if not isinstance(entry, dict) or entry.get("tool") != "search_nodes":
            continue
        info = entry.get("lex_path")
        if info is None:
            continue
        rendered = format_lex_path(info)
        if rendered not in paths:
            paths.append(rendered)
    return paths


@dataclass(frozen=True)
class _AgentRun:
    """One agent run, and what it retrieved."""

    state: agent_state.AgentState
    total_ms: int
    retrieval_ms: int
    search_latency_ms: int
    node_ids: list[str]
    components: dict[str, dict[str, float]]
    lex_paths: list[str] = field(default_factory=list)


class _RunCache:
    """The last agent run, keyed by the question it was run for.

    The runner asks an arm for its retrieval and then for its answer,
    for the SAME question. Without this cache the agent would run twice
    and the metric would be computed over one retrieval while the answer
    came from another — the exact defect this module exists to remove.

    One entry is enough: the runner is sequential and asks about one
    question at a time, so the next question evicts the last. A larger
    cache would only hold runs nobody asks about again.

    A run serves each of its two ROLES at most once: the retrieval read
    and the answer read. So a question that appears TWICE in a dataset
    runs the agent twice, and each occurrence reports its OWN latency
    rather than the first occurrence's.
    """

    def __init__(self) -> None:
        self._key: tuple[str, int] | None = None
        self._run: _AgentRun | None = None
        self._served: set[str] = set()

    def get(self, question: str, max_iterations: int, role: str) -> _AgentRun | None:
        """The cached run for this question, if this role has not read it."""
        if self._key != (question, max_iterations) or role in self._served:
            return None
        self._served.add(role)
        return self._run

    def put(self, question: str, max_iterations: int, run: _AgentRun, role: str) -> None:
        """Remember the run for this question, read by ``role``."""
        self._key = (question, max_iterations)
        self._run = run
        self._served = {role}


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

    **The agent runs ONCE per question.** ``retrieve`` and ``answer``
    both read the SAME run, so the retrieval metric and the answer come
    from one retrieval. The runner asks for the two in that order, for
    the same question, so a one-entry cache is enough to guarantee it.
    """

    name = ARM_TUKLAS

    def __init__(
        self,
        db_path: str,
        document_id: str,
        *,
        generator: Any | None = None,
        index_root: Any | None = None,
        weights: dict[str, float] | None = None,
    ) -> None:
        self.db_path = db_path
        self.document_id = document_id
        self._generator = generator
        #: The weight snapshot this arm ranks with. The default is the
        #: retrieval layer's own defaults (retrieval.md §3), so an arm
        #: built without a config measures the product as it ships.
        self.weights: dict[str, float] = (
            dict(weights) if weights is not None else dict(WEIGHT_DEFAULTS)
        )
        self._runs = _RunCache()
        if index_root is not None:
            # The per-document BM25 artifact lives under the index root
            # (retrieval.md §2). A run against a store other than the
            # default one must point the retrieval layer at the matching
            # index root, or the lex component would silently fall back to
            # the FTS5 path and score differently.
            from apps.api.retrieval import scoring

            scoring.INDEX_ROOT = index_root

    def retrieve(self, question: str, *, max_iterations: int = 12) -> RetrievalResult:
        """The vectorless ranking the ANSWER was produced from.

        The node ids are the union of the per-sub-question
        ``search_nodes`` result ids, in the agent's order — NOT a
        separate search over the whole question. The agent splits a
        question into sub-questions and retrieves per sub-question, so a
        whole-question ranking is a ranking the model never saw; a
        metric over it would describe a retrieval that did not happen.
        """
        run = self._run_for(question, max_iterations=max_iterations, role="retrieve")
        return RetrievalResult(
            node_ids=list(run.node_ids),
            latency_ms=run.search_latency_ms,
            components=run.components,
            weights=dict(self.weights),
            lex_paths=list(run.lex_paths),
        )

    def answer(self, question: str, *, max_iterations: int = 12) -> AnswerResult:
        """The agent's answer, with the latencies split.

        The split is measured, not estimated: the retrieval time is the
        sum of the ``search_nodes`` tool latencies the agent's own trace
        recorded, and the generation time is the total minus that. The
        agent's trace carries the per-call latency of every tool wrapper,
        so the split comes from the run itself.
        """
        run = self._run_for(question, max_iterations=max_iterations, role="answer")
        return AnswerResult(
            answer_markdown=str(run.state.get("answer_md", "")),
            citations=list(run.state.get("citations", []) or []),
            evidence_ids=list(run.state.get("evidence_ids", []) or []),
            insufficient=bool(run.state.get("insufficient", False)),
            iterations=int(run.state.get("tool_iterations", 0)),
            latency_ms=run.total_ms,
            retrieval_latency_ms=run.retrieval_ms,
            generation_latency_ms=max(0, run.total_ms - run.retrieval_ms),
            tokens_in=int(run.state.get("_gen_prompt_tokens", 0) or 0),
            tokens_out=int(run.state.get("_gen_completion_tokens", 0) or 0),
            tokens_source=run.state.get("_gen_tokens_source"),
            retrieved_node_ids=list(run.node_ids),
            weights=dict(self.weights),
            lex_paths=list(run.lex_paths),
        )

    def _run_for(self, question: str, *, max_iterations: int, role: str) -> _AgentRun:
        """The agent run for this question, running the graph at most once.

        The ``role`` names which read is asking: ``retrieve`` and
        ``answer`` are the two reads of ONE question, and they share a
        run. A third read of the same question — the same question asked
        a second time — is a NEW run.
        """
        cached = self._runs.get(question, max_iterations, role)
        if cached is not None:
            return cached
        started = time.perf_counter()
        state = self._run(question, max_iterations=max_iterations)
        total_ms = int((time.perf_counter() - started) * 1000)
        run = _AgentRun(
            state=state,
            total_ms=total_ms,
            retrieval_ms=_retrieval_latency(dict(state)),
            search_latency_ms=_search_latency(dict(state)),
            node_ids=_retrieved_node_ids(dict(state)),
            components=_hit_components(dict(state)),
            lex_paths=_lex_paths(dict(state)),
        )
        self._runs.put(question, max_iterations, run, role)
        return run

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
            weights=self.weights,
        )

    def describe(self) -> dict[str, Any]:
        """The arm's own description, for the run config.

        The vectorless arm's identity is its SCORING: the five
        retrieval.md §3 components, the identifier precedence of ADR-005,
        and the absence of any dense machinery. The last two lines are
        the ones a reader comparing the arms needs.

        The arm also reports the SIZE of the surface it scores and WHAT
        that surface is, because a reader comparing the two arms' numbers
        has to know the surfaces the numbers were measured over. The
        vectorless arm RANKS every node in the tree (its ident, title and
        hier channels reach containers the lexical corpus does not), and
        its LEXICAL corpus is the smaller set: leaf-node bodies and
        section-intro nodes. The dense arm scores the same lexical node
        set, so the two numbers a reader needs are comparable.
        """
        nodes = self._node_ids()
        lex_nodes = self._lex_node_ids()
        return {
            "algorithm": "vectorless-five-component",
            "components": ["lex", "ident", "title", "ref", "hier"],
            "ident_component": "art 1318 -> exact normalized article id",
            "identifier_precedence": True,
            "dense": False,
            "n_nodes": len(nodes),
            "n_lex_nodes": len(lex_nodes),
            "surface": SURFACE_TUKLAS,
        }

    def _node_ids(self) -> list[str]:
        """Every node id of the document, in tree order."""
        with _connect(self.db_path) as conn:
            document = conn.execute(
                "SELECT id FROM documents WHERE id = ? OR slug = ?",
                (self.document_id, self.document_id),
            ).fetchone()
            if document is None:
                return []
            rows = conn.execute(
                "SELECT id FROM nodes WHERE document_id = ? ORDER BY depth, position",
                (str(document["id"]),),
            ).fetchall()
        return [str(row["id"]) for row in rows]

    def _lex_node_ids(self) -> list[str]:
        """The node ids of the document's lexical corpus, in tree order.

        The rule is the primary path's own
        (``retrieval.index.in_lex_corpus``), so the number here is the
        number the BM25 corpus was built from and the number the dense
        arm's corpus is cut to.
        """
        with _connect(self.db_path) as conn:
            document = conn.execute(
                "SELECT id FROM documents WHERE id = ? OR slug = ?",
                (self.document_id, self.document_id),
            ).fetchone()
            if document is None:
                return []
            rows = conn.execute(
                "SELECT id, type, text FROM nodes WHERE document_id = ? ORDER BY depth, position",
                (str(document["id"]),),
            ).fetchall()
        return [
            str(row["id"])
            for row in rows
            if in_lex_corpus(str(row["type"]), str(row["text"]))
        ]


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

    **The corpus is the vectorless arm's lexical node set.** The dense
    arm used to embed EVERY node, while the vectorless arm's BM25 corpus
    covers only leaf-node bodies and section-intro nodes. The 131
    chapters, 51 titles, 4 books and the document root were therefore
    reachable by one arm and absent from the other — a surface
    availability difference, not a retrieval-mechanism difference, and an
    undisclosed one. The corpus is now cut by the primary path's own rule
    (``retrieval.index.in_lex_corpus``), so the two arms score the same
    node set by construction.

    **The query carries the same surface as the vectorless arm's lexical
    query.** The vectorless arm strips the query's identifier tokens from
    its lexical input (retrieval.md §3's disjointness rule), so a
    question naming "Article 1318" gave the dense arm the identifier on
    BOTH sides of the cosine and forbade the vectorless arm the lexical
    half of it. The dense arm strips the same tokens now, with the same
    normalizer, so the identifier is absent from both sides or present on
    both sides.

    **The agent runs ONCE per question**, exactly as in the vectorless
    arm, so the metric and the answer come from one retrieval.
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
        self._runs = _RunCache()

    # -- the corpus --------------------------------------------------------

    def corpus(self) -> list[Any]:
        """The document's LEXICAL nodes, as the dense arm's corpus entries.

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

        The corpus is cut to the node set the vectorless arm's BM25
        corpus covers, by the primary path's own rule. The rule is
        IMPORTED, not restated: architecture.md §2 gives
        ``experiments/vector/`` no imports from the primary path, so the
        rule could not live there, and a second copy in the harness would
        be a second rule that drifts. The harness reads the rule and
        passes the rule-derived node set IN, which is the "the corpus
        comes IN, not out" contract ``experiments/vector/index.py``
        already documents.
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
                "SELECT id, title, text, type, page_start, depth, position FROM nodes"
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
            if in_lex_corpus(str(row["type"]), str(row["text"]))
        ]
        return self._corpus

    def index(self) -> Any:
        """The dense index, built on first use."""
        if self._vector_index is None:
            from experiments.vector.index import build_index

            self._vector_index = build_index(self.corpus())
        return self._vector_index

    def describe(self) -> dict[str, Any]:
        """The arm's own description, for the run config.

        The arm reports the SIZE of the corpus it embeds and the SURFACE
        it scores, so a reader comparing the two arms' numbers knows the
        surfaces the numbers were measured over. The dense arm's corpus
        is the vectorless arm's lexical node set, so its ``n_nodes`` and
        the vectorless arm's ``n_lex_nodes`` are the same number.
        """
        description: dict[str, Any] = self.index().describe()
        description["n_nodes"] = len(self.corpus())
        description["n_lex_nodes"] = len(self.corpus())
        description["surface"] = SURFACE_VECTOR
        return description

    # -- the interface -----------------------------------------------------

    def retrieve(self, question: str, *, max_iterations: int = 12) -> RetrievalResult:
        """The dense ranking the ANSWER was produced from.

        The node ids are the union of the per-sub-question
        ``search_nodes`` result ids, in the agent's order — the same
        retrieval the answer rests on, exactly as in the vectorless arm.
        """
        run = self._run_for(question, max_iterations=max_iterations, role="retrieve")
        return RetrievalResult(
            node_ids=list(run.node_ids),
            latency_ms=run.search_latency_ms,
            components=run.components,
            lex_paths=list(run.lex_paths),
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
        run = self._run_for(question, max_iterations=max_iterations, role="answer")
        state = run.state
        return AnswerResult(
            answer_markdown=str(state.get("answer_md", "")),
            citations=list(state.get("citations", []) or []),
            evidence_ids=list(state.get("evidence_ids", []) or []),
            insufficient=bool(state.get("insufficient", False)),
            iterations=int(state.get("tool_iterations", 0)),
            latency_ms=run.total_ms,
            retrieval_latency_ms=run.retrieval_ms,
            generation_latency_ms=max(0, run.total_ms - run.retrieval_ms),
            tokens_in=int(state.get("_gen_prompt_tokens", 0) or 0),
            tokens_out=int(state.get("_gen_completion_tokens", 0) or 0),
            tokens_source=state.get("_gen_tokens_source"),
            retrieved_node_ids=list(run.node_ids),
            lex_paths=list(run.lex_paths),
        )

    def _run_for(self, question: str, *, max_iterations: int, role: str) -> _AgentRun:
        """The agent run for this question, running the graph at most once.

        The ``role`` names which read is asking: ``retrieve`` and
        ``answer`` are the two reads of ONE question, and they share a
        run. A third read of the same question — the same question asked
        a second time — is a NEW run.
        """
        cached = self._runs.get(question, max_iterations, role)
        if cached is not None:
            return cached
        started = time.perf_counter()
        state = self._run(question, max_iterations=max_iterations)
        total_ms = int((time.perf_counter() - started) * 1000)
        run = _AgentRun(
            state=state,
            total_ms=total_ms,
            retrieval_ms=_retrieval_latency(dict(state)),
            search_latency_ms=_search_latency(dict(state)),
            node_ids=_retrieved_node_ids(dict(state)),
            components=_hit_components(dict(state)),
            lex_paths=_lex_paths(dict(state)),
        )
        self._runs.put(question, max_iterations, run, role)
        return run

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

        **The query is stripped of its identifier tokens**, exactly as the
        vectorless arm's lexical query is. The vectorless arm removes
        them from its lexical input (retrieval.md §3's disjointness
        rule), so an identifier term fires in exactly one component there.
        The dense arm embeds its query whole, so without this the
        identifier was present on BOTH sides of the dense cosine while
        the vectorless arm was forbidden the lexical half of it — an
        asymmetry in the SURFACES, not in the mechanism, and one nobody
        chose. The same normalizer strips the same tokens, so the two
        arms' queries carry the same surface.

        The trace records BOTH texts: the raw query under ``query`` (the
        sub-question, which is the key the evidence gate looks the hits
        up by) and the stripped query under ``embedded_query`` (the text
        that was actually embedded). A trace that recorded only the raw
        question would describe a search that did not happen.
        """
        index = self.index()

        def dense_search_nodes(state: agent_state.AgentState, args: dict[str, Any]) -> agent_state.AgentState:
            raw_query = str(args["query"])
            query = identifier_free_query(raw_query)
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
            # The hits are filed under the RAW sub-question, because that is
            # the key ``select_evidence`` looks them up by: the evidence
            # gate reads the hits of each sub-question, and the sub-question
            # is the planner's text, not the stripped query that was
            # embedded. Filing them under the stripped text would leave the
            # gate without the hits of every identifier-bearing
            # sub-question.
            by_question = dict(state.get("_hits_by_question", {}))
            by_question.setdefault(raw_query, []).extend(matched)
            state["_hits_by_question"] = by_question
            state["_hits"] = list(state.get("_hits", [])) + list(matched)
            state["_searched"] = list(state.get("_searched", [])) + [raw_query]
            state["tool_iterations"] = int(state.get("tool_iterations", 0)) + 1
            branch_id = f"search_nodes#{state['tool_iterations']}"
            state["tool_results"].append(
                {
                    "tool": "search_nodes",
                    "args": {"query": raw_query, "top_k": top_k, "embedded_query": query},
                    "result_ids": hit_ids,
                    "relevant": bool(hit_ids),
                    "latency_ms": round(latency, 3),
                }
            )
            state["trace"].append(
                {
                    "tool": "search_nodes",
                    "args": {"query": raw_query, "top_k": top_k, "embedded_query": query},
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
    "RETRIEVAL_INSTRUMENT_ANSWER_NODE_UNION",
    "RETRIEVAL_INSTRUMENT_WHOLE_QUESTION_SEARCH",
    "SURFACE_TUKLAS",
    "SURFACE_VECTOR",
    "TOP_K",
    "AnswerResult",
    "Arm",
    "RetrievalResult",
    "TuklasArm",
    "VectorArm",
    "dense_embedded_text",
    "identifier_free_query",
]
