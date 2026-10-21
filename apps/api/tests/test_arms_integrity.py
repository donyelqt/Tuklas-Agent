"""The two arms' INTEGRITY tests: what each arm measured must be stated.

evaluation.md §1: "same dataset, same questions, same golds, same
generation model, same judges -> both arms -> reported metrics". A run
whose two arms were measured over DIFFERENT surfaces, or whose retrieval
metric came from a DIFFERENT search than the answer, does not measure
what the report says it measures.

Three defects are pinned here, each of them VERIFIED by measurement
before it was fixed:

* **F1** — the retrieval metric and the answer came from two different
  searches. The metric was computed over a ranking of the WHOLE
  question; the answer was produced from a ranking of the SUB-QUESTIONS
  the agent actually searched for. The metric therefore described a
  retrieval the model never saw.
* **F5** — the dense arm embedded the title twice, because
  ``node.text`` already starts with ``node.title`` (the parser slices
  the body from the heading's own line onward). The duplication
  re-weighted provisions against containers.
* **F6** — the two arms retrieved over different node sets and
  different surfaces, undisclosed: the dense arm embedded EVERY node
  while the vectorless arm's BM25 corpus covers only leaf bodies and
  section intros, and the dense arm's query kept the identifier tokens
  the vectorless arm's lexical query strips.

The suite is MODEL-FREE and CORPUS-FREE: the corpus is a SYNTHETIC PDF
built with PyMuPDF (the convention ``apps/api/tests/test_api_routes.py``
uses), and the generation is the deterministic stub. The one test that
needs the real corpus is skipped when the corpus PDF is absent.
"""

from __future__ import annotations

from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest
from apps.api.evaluation import arms as arms_module

REPO_ROOT = Path(__file__).resolve().parents[3]

#: The real corpus, for the one test that needs it. The suite is
#: CORPUS-FREE without it.
CORPUS = REPO_ROOT / "data" / "documents" / "civil-code-ra386-icnl.pdf"

#: The synthetic statute's pages. The FIRST page is a CHAPTER heading with
#: its own intro text, so the corpus has a container node the vectorless
#: arm's lexical corpus does NOT cover — which is what makes the F6 corpus
#: test discriminate: before the fix the dense arm embedded the chapter
#: and the vectorless arm's lex surface could not reach it at all.
_SYNTHETIC_PAGES: list[tuple[str, str]] = [
    (
        "CHAPTER I. General Provisions",
        "This chapter states the general provisions of the statute. ",
    ),
    (
        "Article 1. Requisites of contracts.",
        "The requisites of contracts are consent, object, and certainty of the terms. "
        "A contract requires the consent of the parties, a lawful object, and certainty. ",
    ),
    (
        "Article 2. Succession.",
        "Succession is the transfer of the rights and obligations of a deceased person "
        "to the heirs or legatees by his will or by operation of law. ",
    ),
    (
        "Article 3. Ignorance of the law.",
        "Ignorance of the law excuses no one from compliance therewith. ",
    ),
    (
        "Article 4. Prescription.",
        "Prescription extinguishes a claim after the period laid down by law. The "
        "periods of prescription are enumerated in this article. ",
    ),
    (
        "Article 5. Damages.",
        "Damages are the sum of money which the court orders the defendant to pay. The "
        "kinds of damages are provided here. ",
    ),
    (
        "Article 6. Partition.",
        "Partition may be made by agreement between the parties or by judicial "
        "proceedings. Partition shall be governed by the Rules of Court. ",
    ),
    (
        "Article 7. Holographic wills.",
        "A person may execute a holographic will which must be entirely written, "
        "dated, and signed by the hand of the testator himself. ",
    ),
    (
        "Article 8. Usufruct.",
        "Usufruct gives a right to enjoy the property of another with the obligation "
        "of preserving its form and substance, unless the title otherwise provides. ",
    ),
]

#: The synthetic document's articles, by their printed number.
_ARTICLE_IDS: tuple[str, ...] = tuple(
    f"node_synth-doc_article_{index}" for index in range(1, 9)
)


def _fitz() -> ModuleType:
    import fitz

    return cast(ModuleType, fitz)


def _write_pdf(path: Path) -> None:
    """The synthetic statute: one page per heading, a heading and a body."""
    fitz = _fitz()
    doc = fitz.open()
    for heading, body in _SYNTHETIC_PAGES:
        page = doc.new_page()
        page.insert_textbox(
            fitz.Rect(50, 50, 560, 780), heading + "\n" + body * 3, fontsize=11
        )
    doc.save(path)
    doc.close()


@pytest.fixture(scope="module")
def corpus(tmp_path_factory: pytest.TempPathFactory) -> Any:
    """A synthetic document with a chapter, ingested, indexed, queryable."""
    import shutil

    import apps.api.retrieval.scoring as scoring
    from apps.api.ingestion.service import ingest_pdf
    from apps.api.retrieval.index import build_indexes

    root = tmp_path_factory.mktemp("arms-integrity")
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
            "index_root": str(index_root),
        }
    finally:
        scoring.INDEX_ROOT = original_root
        shutil.rmtree(root, ignore_errors=True)


def _vectorless_arm(corpus: Any) -> Any:
    """The vectorless arm over the synthetic corpus, with the stub generator."""
    from apps.api.agent.graph import StubGenerator
    from apps.api.evaluation.arms import TuklasArm

    return TuklasArm(
        corpus["db_path"],
        corpus["slug"],
        generator=StubGenerator(),
        index_root=Path(corpus["index_root"]),
    )


def _dense_arm(corpus: Any) -> Any:
    """The dense arm over the synthetic corpus, with the stub generator."""
    from apps.api.agent.graph import StubGenerator
    from apps.api.evaluation.arms import VectorArm

    return VectorArm(
        corpus["db_path"],
        corpus["slug"],
        generator=StubGenerator(),
    )


# ---------------------------------------------------------------------------
# F1 — the metric comes from the retrieval the ANSWER came from
# ---------------------------------------------------------------------------


def test_the_retrieval_metric_comes_from_the_answer_path(corpus: Any) -> None:
    """The arm's ranking IS the ranking the answer was produced from.

    The defect this pins: the metric was computed over a ranking of the
    WHOLE question, while the agent split the question into
    sub-questions and searched per sub-question. The metric therefore
    described a ranking the model never saw, and the reported retrieval
    numbers did not describe the retrieval the answer came from.

    The test builds the case the defect needs: a question whose
    whole-question ranking and whose per-sub-question rankings DIFFER.
    It then asserts that the arm's ranking is the sub-question union —
    the node ids the agent's own search calls returned, in the agent's
    order — and NOT the whole-question ranking.
    """
    from apps.api.agent.graph import _split_query
    from apps.api.retrieval import api as retrieval_api

    question = "What is succession, what is prescription, and what are damages?"
    sub_questions = _split_query(question)
    assert len(sub_questions) > 1, "the question must split, or there is no case"

    # The whole-question ranking: what the metric used to measure.
    whole_hits = retrieval_api.search_nodes(
        query=question,
        document_id=corpus["slug"],
        top_k=20,
        db_path=corpus["db_path"],
    )
    whole_ids = [hit.node_id for hit in whole_hits]

    # The per-sub-question union: what the answer actually rests on.
    union: list[str] = []
    for sub_question in sub_questions:
        hits = retrieval_api.search_nodes(
            query=sub_question,
            document_id=corpus["slug"],
            top_k=10,
            db_path=corpus["db_path"],
        )
        for hit in hits:
            matched = (
                hit.components.get("lex", 0.0) > 0.0
                or hit.components.get("ident", 0.0) > 0.0
            )
            if matched and hit.node_id not in union:
                union.append(hit.node_id)

    # The case must DISCRIMINATE, or the assertion below proves nothing.
    assert union, "the sub-questions must retrieve something"
    assert whole_ids[:5] != union[:5], (
        "the whole-question ranking and the sub-question ranking must differ "
        "for this test to discriminate"
    )

    arm = _vectorless_arm(corpus)
    retrieval = arm.retrieve(question)

    assert retrieval.node_ids == union, (
        "the metric must be computed over the retrieval the answer came from"
    )
    assert retrieval.node_ids != whole_ids[: len(retrieval.node_ids)], (
        "the metric must NOT be the whole-question ranking"
    )


def test_the_answer_and_the_metric_carry_the_same_node_ids(corpus: Any) -> None:
    """The arm's ranking and the answer's ranking are the SAME list.

    The runner reads the ranking for the metric and the answer for the
    generation, and the two must describe one retrieval. The arm runs
    the agent graph ONCE per question, so the two reads cannot diverge
    — and this test pins that they do not.
    """
    question = "What is succession, and what is partition?"
    arm = _vectorless_arm(corpus)
    retrieval = arm.retrieve(question)
    answer = arm.answer(question)

    assert retrieval.node_ids == answer.retrieved_node_ids
    assert answer.retrieved_node_ids, "the agent retrieved nothing"


def test_the_dense_arm_records_its_own_per_sub_question_results(corpus: Any) -> None:
    """The dense arm's metric comes from ITS search calls, symmetrically.

    The arms have to be measured the same way. If the vectorless arm's
    metric came from its answer path and the dense arm's from a separate
    search, the two numbers would not be comparable — which is the
    defect F1 was, on one side of the comparison.
    """
    from apps.api.agent.graph import _split_query

    question = "What is succession, what is prescription, and what are damages?"
    sub_questions = _split_query(question)
    assert len(sub_questions) > 1

    arm = _dense_arm(corpus)
    retrieval = arm.retrieve(question)
    answer = arm.answer(question)

    assert retrieval.node_ids == answer.retrieved_node_ids
    # The dense arm's node ids come from ITS OWN per-sub-question
    # searches, so they are a subset of the corpus it ranks over and a
    # union over the sub-questions.
    corpus_ids = {entry.node_id for entry in arm.corpus()}
    assert set(retrieval.node_ids) <= corpus_ids
    assert retrieval.node_ids, "the dense arm retrieved nothing"


def test_the_stored_rows_carry_the_answer_paths_ranking(corpus: Any) -> None:
    """The stored rows' shape is unchanged, and the list is the answer's.

    The store's ``retrieved_nodes`` column and the report's read of it
    are a JSON list of node id strings. The fix changes WHICH list that
    is, not the shape — so the assertion is on the shape AND on the
    list being EXACTLY the answer path's list.

    The list is compared against the arm's OWN answer path, read
    independently of the runner: the arm's ``answer()`` reports the node
    ids its own run retrieved, and the stored row must be that list, in
    that order. Before the fix the stored row was the whole-question
    ranking, which is a DIFFERENT list in a DIFFERENT order, so the
    equality fails.
    """
    import json

    from apps.api.agent.graph import StubGenerator
    from apps.api.evaluation import store as eval_store
    from apps.api.evaluation.config import load_run_config
    from apps.api.evaluation.dataset import Dataset, Question
    from apps.api.evaluation.judge import CannedJudge
    from apps.api.evaluation.runner import Runner

    config_payload = json.loads(
        (Path(__file__).resolve().parents[1] / "evaluation" / "fixtures" / "example-run-config.json").read_text(
            encoding="utf-8"
        )
    )
    config_payload.pop("config_id", None)
    config_payload["config"]["judge_prompt_version"] = CannedJudge().prompt_version
    config = load_run_config(config_payload)

    question_text = "What is succession, what is prescription, and what are damages?"
    question = Question(
        id="q001",
        question=question_text,
        document="synth-doc",
        category="multi-section",
        gold_nodes=["node_synth-doc_article_2", "node_synth-doc_article_4"],
        expected_claims=["succession", "prescription"],
    )
    dataset = Dataset(name="integrity", questions=[question], snapshot_id="integrity-1")
    runner = Runner(
        corpus["db_path"],
        corpus["slug"],
        config,
        index_root=Path(corpus["index_root"]),
        generator=StubGenerator(),
        judge=CannedJudge(),
    )
    outcome = runner.run(dataset)
    loaded = eval_store.load_evaluation(corpus["db_path"], outcome["evaluation_id"])
    assert loaded is not None
    rows = loaded["questions"]
    assert len(rows) == 2  # one question, two arms

    for row in rows:
        # The SHAPE is unchanged: a JSON list of node id strings.
        assert isinstance(row["retrieved_nodes"], list)
        assert all(isinstance(node, str) for node in row["retrieved_nodes"])
        assert row["retrieved_nodes"], row

    # The stored list must be the answer path's list, IN THE ANSWER
    # PATH'S ORDER, for BOTH arms.
    vectorless = _vectorless_arm(corpus)
    dense = _dense_arm(corpus)
    tuklas = next(row for row in rows if row["arm"] == "tuklas")
    vector = next(row for row in rows if row["arm"] == "vector")

    assert tuklas["retrieved_nodes"] == vectorless.answer(question_text).retrieved_node_ids
    assert vector["retrieved_nodes"] == dense.answer(question_text).retrieved_node_ids

    # And the two lists must DIFFER, or the assertion above would hold
    # for a whole-question ranking too.
    assert tuklas["retrieved_nodes"] != vector["retrieved_nodes"]


def test_the_metric_and_the_answer_come_from_ONE_agent_run(
    corpus: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The metric's list and the answer's list come from ONE agent run.

    This is the sharpest statement of the F1 fix, and it is the one
    that would fail if the arms ran the agent twice.

    The runner asks an arm for its retrieval and then for its answer,
    for the SAME question. If those two reads ran the agent separately,
    the metric would be computed over one retrieval and the answer
    would come from another — the defect, only relocated from "one from
    the agent and one from a direct call" to "one from each of two
    agent runs".

    The assertion is on the STATE OBJECT, not on the node ids. The
    retrieval is a pure function of (corpus, query), so two agent runs
    over the same question would produce the SAME node ids and a test
    on the ids alone would pass against the defect. The state object
    identifies the RUN, so counting distinct state objects is what
    actually distinguishes one run from two.

    The answer is read FIRST here, and the retrieval second, so this
    also pins that the two reads share a run regardless of the order
    the runner happens to use.
    """
    from apps.api.agent import graph as agent_graph

    states: list[int] = []
    original = agent_graph.run_agent

    def tagging_run_agent(
        document_id: str,
        question: str,
        **kwargs: Any,
    ) -> Any:
        state = original(document_id, question, **kwargs)
        states.append(id(state))
        return state

    monkeypatch.setattr(agent_graph, "run_agent", tagging_run_agent)

    question = "What is succession, and what is partition?"

    for arm in (_vectorless_arm(corpus), _dense_arm(corpus)):
        states.clear()
        # The answer is read FIRST, on purpose: the two reads must
        # share a run whichever order they arrive in.
        answer = arm.answer(question)
        retrieval = arm.retrieve(question)

        assert len(states) == 1, (
            f"the metric read and the answer read must come from ONE agent run, "
            f"but the graph ran {len(states)} times: {states}"
        )
        assert retrieval.node_ids == answer.retrieved_node_ids
        assert retrieval.node_ids, "the agent retrieved nothing"


def test_a_repeated_question_runs_the_agent_again(
    corpus: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same question asked twice runs the agent twice.

    The arm's cache exists so that ``retrieve`` and ``answer`` read ONE
    run for a question, not so that two OCCURRENCES of a question share
    one run. A dataset that asks the same question twice must measure
    that occurrence's own retrieval and its own latency, so the cache
    serves a run to each ROLE at most once — the retrieval read and the
    answer read.

    The assertion counts the agent's invocations, because the retrieved
    node ids are the same either way: the retrieval is a pure function
    of (corpus, query), so a test on the ids would pass against a cache
    that reuses the first occurrence's run.
    """
    from apps.api.agent import graph as agent_graph

    calls: list[str] = []
    original = agent_graph.run_agent

    def counting_run_agent(
        document_id: str,
        question: str,
        **kwargs: Any,
    ) -> Any:
        calls.append(question)
        return original(document_id, question, **kwargs)

    monkeypatch.setattr(agent_graph, "run_agent", counting_run_agent)

    question = "What is succession, and what is partition?"
    arm = _vectorless_arm(corpus)

    arm.retrieve(question)
    arm.answer(question)
    # The two reads of ONE question share one run.
    assert len(calls) == 1, calls

    # The SECOND occurrence of the same question must run the agent again.
    arm.retrieve(question)
    arm.answer(question)
    assert len(calls) == 2, calls


# ---------------------------------------------------------------------------
# F5 — the dense arm embeds the node's text ONCE
# ---------------------------------------------------------------------------


def test_the_dense_arm_does_not_embed_the_title_twice() -> None:
    """The embedded text does not repeat a title the text already carries.

    The defect this pins: the dense arm's embedded surface returned the
    title, a newline and then ``entry.text`` — but ``entry.text``
    ALREADY starts with the title (the parser slices the body from the
    heading's own line onward; measured: 2,578 of 2,579 Civil Code
    nodes). The heading was therefore embedded TWICE, which re-weighted
    provisions against containers: the title's share of the embedded
    text was 32.4% for articles with the duplicate against 52.9%
    without.

    The rule is read through the harness, which is the dense package's
    sole legal importer (architecture.md §2), so the test names the
    rule the arm actually applies.
    """
    title = "Article 1318. There is no contract without consent."
    text = f"{title}\nThere is no contract without the consent of the parties."

    embedded = arms_module.dense_embedded_text("n1", title, text)

    assert embedded == text, "the embedded text must be the node's own text, once"
    assert embedded.count(title) == 1, "the title must not be embedded twice"
    # The embedded surface must not carry a second copy of the heading
    # ahead of the body.
    assert not embedded.startswith(f"{title}\n{title}")


def test_the_dense_arm_embeds_the_heading_that_the_text_already_carries() -> None:
    """The heading is still IN the embedded text, exactly once.

    The fix removes the DUPLICATE, not the heading. A node's heading is
    part of its surface — the parser stores it at the start of the body
    — and dropping it would change the arm's surface in the other
    direction, which is a different defect.
    """
    title = "Article 1318. There is no contract without consent."
    text = f"{title}\nThere is no contract without the consent of the parties."

    embedded = arms_module.dense_embedded_text("n1", title, text)

    assert "There is no contract without consent." in embedded
    assert embedded.count("There is no contract without consent.") == 1


def test_the_dense_arm_embeds_a_text_that_does_not_carry_its_title() -> None:
    """A text with no title in it is embedded as it is.

    The rule is "embed the node's text ONCE", not "embed the text
    minus a duplicate of the title". A node whose body does not start
    with its heading is embedded without one, so the surface is the
    node's own text in every case.
    """
    title = "Article 1318. There is no contract without consent."
    text = "The body of a node whose heading is not part of its text."

    assert arms_module.dense_embedded_text("n1", title, text) == text


# ---------------------------------------------------------------------------
# F6 — the two arms score the same surface
# ---------------------------------------------------------------------------


def test_the_two_arms_rank_the_same_lexical_node_set(corpus: Any) -> None:
    """The dense arm's corpus IS the vectorless arm's lexical corpus.

    The defect this pins: the dense arm embedded EVERY node (2,579 on
    the Civil Code) while the vectorless arm's BM25 corpus covers only
    leaf bodies and section intros (2,392). The 131 chapters, 51
    titles, 4 books and the document root were reachable by one arm and
    absent from the other — a surface-availability difference, not a
    retrieval-mechanism difference, and an undisclosed one.

    The corpus is cut by the PRIMARY path's own rule
    (``retrieval.index.in_lex_corpus``), so the two cannot drift.
    """
    from apps.api.retrieval.index import in_lex_corpus

    vectorless = _vectorless_arm(corpus)
    dense = _dense_arm(corpus)

    lex_ids = set(vectorless._lex_node_ids())
    dense_ids = {entry.node_id for entry in dense.corpus()}

    assert dense_ids == lex_ids, (
        "the dense arm must rank over the node set the vectorless arm's "
        "lexical corpus covers"
    )
    # The rule is the primary path's, and the harness uses it.
    for entry in dense.corpus():
        assert in_lex_corpus("article", entry.text) or in_lex_corpus("section", entry.text)
    # The chapter is in the tree and NOT in the lexical corpus, so the
    # case discriminates: before the fix the dense arm embedded it.
    assert "node_synth-doc_chapter_i" not in dense_ids


def test_the_dense_arms_query_has_no_identifier_tokens() -> None:
    """The dense arm's embedded query carries no identifier tokens.

    The defect this pins: the vectorless arm's lexical query strips the
    query's identifier tokens (retrieval.md §3's disjointness rule),
    while the dense arm embedded the raw question. A question naming
    "Article 1318" therefore gave the dense arm the identifier on BOTH
    sides of the cosine and forbade the vectorless arm the lexical half
    of it — the identifier was present on one side of the comparison
    and absent on the other.

    The same normalizer strips the same tokens, so the two arms'
    queries carry the same surface.
    """
    from apps.api.retrieval.normalizer import label_tokens

    query = "What does Article 1318 require?"
    stripped = arms_module.identifier_free_query(query)

    assert "1318" not in stripped
    assert "article" not in stripped.lower()
    assert "require" in stripped.lower(), "the CONTENT words must survive"

    # The tokens the vectorless arm strips are exactly the ones the
    # dense arm strips: one normalizer, one rule.
    for token in label_tokens(query):
        assert token not in stripped.lower()


def test_the_dense_arm_embeds_the_stripped_query(corpus: Any) -> None:
    """The dense arm's SEARCH really embeds the stripped query.

    The helper alone would not prove anything: the arm could strip the
    query and then embed the raw one. This test drives the arm's own
    search tool and reads the query it recorded as the embedded one.
    """
    from apps.api.agent import state as agent_state

    arm = _dense_arm(corpus)
    tool = arm._dense_search_tool()
    state = agent_state.initial_state(
        "What does Article 1318 require?",
        corpus["slug"],
        max_iterations=12,
    )
    state["_db_path"] = corpus["db_path"]
    state = tool(state, {"query": "What does Article 1318 require?", "top_k": 5})

    entry = next(
        item
        for item in state["tool_results"]
        if item.get("tool") == "search_nodes"
    )
    embedded = str(entry["args"]["embedded_query"])
    assert "1318" not in embedded
    assert "article" not in embedded.lower()


def test_a_bare_identifier_query_strips_to_nothing(corpus: Any) -> None:
    """A query that IS an identifier strips to nothing, on both arms.

    The vectorless arm's lexical input is empty for a bare identifier
    ("Article 1318"), so its lex component is all-zero. The dense arm
    must see the same surface: an empty embedding, not the identifier.
    An empty string is a valid embedding input, so this is not a
    special case in the arm — it is the same surface the vectorless
    arm scores.
    """
    query = "Article 1318"
    stripped = arms_module.identifier_free_query(query)

    assert stripped == ""
    # The vectorless arm's own lexical input for the same query: the
    # identifier tokens are the whole query, so nothing survives.
    from apps.api.retrieval.normalizer import label_tokens
    from apps.api.retrieval.scoring import tokenize_for_bm25

    label_tokens_ = label_tokens(query)
    lex_tokens = [
        token for token in tokenize_for_bm25(query) if token not in label_tokens_
    ]
    assert lex_tokens == [], "the vectorless arm's lexical input is empty here"

    # The dense arm embeds the empty string, and the search still runs.
    from apps.api.agent import state as agent_state

    arm = _dense_arm(corpus)
    tool = arm._dense_search_tool()
    state = agent_state.initial_state(query, corpus["slug"], max_iterations=12)
    state["_db_path"] = corpus["db_path"]
    state = tool(state, {"query": query, "top_k": 5})
    entry = next(
        item for item in state["tool_results"] if item.get("tool") == "search_nodes"
    )
    assert str(entry["args"]["embedded_query"]) == ""


def test_both_arms_describe_their_corpus_size_and_surface(corpus: Any) -> None:
    """Both arms' describe() reports the corpus size and the surface.

    A reader comparing the two arms' numbers has to know the surfaces
    the numbers were measured over, and the report can only print what
    the arms describe.
    """
    vectorless = _vectorless_arm(corpus)
    dense = _dense_arm(corpus)

    for arm in (vectorless, dense):
        description = arm.describe()
        assert isinstance(description.get("n_nodes"), int)
        assert description["n_nodes"] > 0
        assert isinstance(description.get("n_lex_nodes"), int)
        assert description["n_lex_nodes"] > 0
        assert isinstance(description.get("surface"), str)
        assert description["surface"].strip()

    # The dense arm embeds the vectorless arm's lexical node set, so the
    # two numbers are the same number.
    assert dense.describe()["n_nodes"] == vectorless.describe()["n_lex_nodes"]
    # The vectorless arm RANKS more nodes than it lexes: its ident, title
    # and hier channels reach containers the lexical corpus does not.
    assert vectorless.describe()["n_nodes"] > vectorless.describe()["n_lex_nodes"]


def test_the_surfaces_are_described_without_claiming_a_winner() -> None:
    """Neither surface string claims the outcome of the comparison.

    The comparison's outcome is UNKNOWN, and a surface description that
    says one arm is better would state a result the harness has not
    measured.
    """
    for text in (arms_module.SURFACE_TUKLAS, arms_module.SURFACE_VECTOR):
        lowered = text.lower()
        for forbidden in (
            "outperforms",
            "beats",
            "better than",
            "superior",
            "most accurate",
            "best",
        ):
            assert forbidden not in lowered, forbidden
