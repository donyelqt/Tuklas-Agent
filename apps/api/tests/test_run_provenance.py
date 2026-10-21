"""The run row's measurement provenance (docs/TODO.md, the OPEN GAP item).

Two facts a reader of a run row needs, recorded by the run itself:

* WHICH retrieval INSTRUMENT produced the metric — the versioned name
  ``answer-node-union-v1`` (the metric computed over the node ids the
  ANSWER was produced from, the union of the agent's per-sub-question
  ``search_nodes`` results), as distinct from the old
  ``whole-question-search-v0`` (a separate one-off search over the whole
  question, a ranking the model never saw).
* WHICH lex path produced the vectorless arm's lex scores — the
  per-document BM25 artifact (with its tokenizer version and a content
  hash) or the FTS5 fallback — as the RETRIEVAL LAYER reported it, not a
  re-derivation by the caller.

And the backward-compatibility half: a run recorded BEFORE these fields
existed reads back as SQL NULL for both. That NULL is the fact "not
recorded"; no code path or test invents a value for it. The change is
forward-looking only: it does NOT make the already-published runs
comparable, because neither of them records either fact.

The corpus is the SYNTHETIC PDF (the convention ``test_arms_integrity``
and ``test_evaluation`` use), so the suite is model-free and corpus-free.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest
from apps.api.evaluation import config as eval_config
from apps.api.evaluation import dataset as dataset_module
from apps.api.evaluation import judge as eval_judge
from apps.api.evaluation import store as eval_store
from apps.api.evaluation.arms import (
    RETRIEVAL_INSTRUMENT_ANSWER_NODE_UNION,
    RETRIEVAL_INSTRUMENT_WHOLE_QUESTION_SEARCH,
)
from apps.api.retrieval import scoring

FIXTURE_CONFIG = (
    Path(__file__).resolve().parents[1] / "evaluation" / "fixtures" / "example-run-config.json"
)


def _fitz() -> ModuleType:
    import fitz

    return cast(ModuleType, fitz)


_ARTICLES: list[tuple[str, str]] = [
    (
        "Article 1. Requisites of contracts.",
        "The requisites of contracts are consent, object, and certainty of the terms. ",
    ),
    (
        "Article 2. Succession.",
        "Succession is the transfer of the estate of a decedent to his heirs. ",
    ),
    (
        "Article 3. Ignorance of the law.",
        "Ignorance of the law excuses no one from compliance therewith. ",
    ),
    (
        "Article 4. Prescription.",
        "Prescription extinguishes a claim after the period fixed by law has run. ",
    ),
]


def _write_pdf(path: Path) -> None:
    fitz = _fitz()
    doc = fitz.open()
    for heading, body in _ARTICLES:
        page = doc.new_page()
        page.insert_textbox(fitz.Rect(50, 50, 560, 780), heading + "\n" + body * 3, fontsize=11)
    doc.save(path)
    doc.close()


@pytest.fixture(scope="module")
def corpus(tmp_path_factory: pytest.TempPathFactory) -> Any:
    """A synthetic document, ingested, indexed, ready to query."""
    from apps.api.ingestion.service import ingest_pdf
    from apps.api.retrieval.index import build_indexes

    root = tmp_path_factory.mktemp("provenance")
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


# ---------------------------------------------------------------------------
# 1. The lex path is reported by the code that chooses it
# ---------------------------------------------------------------------------


def test_score_nodes_reports_the_per_document_lex_path(corpus: Any) -> None:
    """With the artifact present, the layer reports the per-document path.

    The reported facts must be the artifact's OWN facts: the tokenizer
    version read out of ``bm25.json`` and a hash OF THAT FILE'S CONTENT —
    not a re-derivation by the caller.
    """
    artifact = Path(corpus["index_root"]) / corpus["doc_id"] / "bm25.json"
    assert artifact.exists(), "the fixture's index build must persist bm25.json"
    payload = json.loads(artifact.read_text(encoding="utf-8"))

    hits, lex_path = scoring.score_nodes(
        corpus["db_path"], corpus["doc_id"], "What are the requisites of contracts?"
    )
    assert hits, "the fixture must return scored nodes"
    assert lex_path.path == scoring.LEX_PATH_PER_DOCUMENT_BM25
    assert lex_path.tokenizer_version == payload["tokenizer"]
    assert lex_path.artifact_sha256_16 == hashlib.sha256(artifact.read_bytes()).hexdigest()[:16]
    # Every node of one call carries the same info: it is a property of
    # the CALL.
    for hit in hits:
        assert hit.lex_path == lex_path


def test_score_nodes_reports_the_fts5_fallback_when_the_artifact_is_absent(
    corpus: Any,
) -> None:
    """With no usable artifact, the layer reports the fallback — itself.

    The point of the reporting contract: the caller does not stat the
    file to decide. Here the artifact exists on disk in the fixture's
    index root for OTHER paths, but this document has none, and the
    layer's answer tracks what actually ran.
    """
    import os

    artifact = Path(corpus["index_root"]) / corpus["doc_id"] / "bm25.json"
    assert artifact.exists()
    # Simulate the artifact's absence for THIS document, exactly the
    # condition the published run was measured under.
    saved = artifact.read_bytes()
    artifact.unlink()
    scoring._BM25_CACHE.clear()
    try:
        hits, lex_path = scoring.score_nodes(
            corpus["db_path"], corpus["doc_id"], "What is prescription?"
        )
        assert hits
        assert lex_path == scoring.LEX_PATH_FTS5_FALLBACK_INFO
        assert lex_path.path == scoring.LEX_PATH_FTS5_FALLBACK
        assert lex_path.tokenizer_version is None
        assert lex_path.artifact_sha256_16 is None
    finally:
        artifact.write_bytes(saved)
        scoring._BM25_CACHE.clear()
        assert os.path.exists(artifact)


def test_a_stale_artifact_falls_back_and_reports_it(corpus: Any) -> None:
    """An artifact under an old tokenizer version is not usable, and the
    layer's report says the scores came from the fallback — which is the
    fact a reader of the run row needs, because the artifact's PRESENCE
    alone would have suggested otherwise."""
    artifact = Path(corpus["index_root"]) / corpus["doc_id"] / "bm25.json"
    saved = artifact.read_bytes()
    stale = json.loads(saved.decode("utf-8"))
    stale["tokenizer"] = "pre-fold-v1"
    artifact.write_text(json.dumps(stale), encoding="utf-8")
    scoring._BM25_CACHE.clear()
    try:
        _hits, lex_path = scoring.score_nodes(
            corpus["db_path"], corpus["doc_id"], "What is prescription?"
        )
        assert lex_path.path == scoring.LEX_PATH_FTS5_FALLBACK
    finally:
        artifact.write_bytes(saved)
        scoring._BM25_CACHE.clear()


def test_format_lex_path_renders_both_forms() -> None:
    """The rendered form is self-describing, per path."""
    per_doc = scoring.format_lex_path(
        scoring.LexPathInfo(
            path=scoring.LEX_PATH_PER_DOCUMENT_BM25,
            tokenizer_version="fold-alnum-v2",
            artifact_sha256_16="0123456789abcdef",
        )
    )
    assert per_doc == "per-document-bm25@0123456789abcdef@tokenizer=fold-alnum-v2"
    assert (
        scoring.format_lex_path(scoring.LEX_PATH_FTS5_FALLBACK_INFO) == "fts5-fallback"
    )


# ---------------------------------------------------------------------------
# 2. The instrument constants
# ---------------------------------------------------------------------------


def test_the_instrument_constants_distinguish_the_two_instruments() -> None:
    """The current and old instrument names are distinct versioned ids.

    The current one is what the harness records; the old one is named
    for the record only — no code path uses it — so a reader of an old
    run row has a name for what those numbers were measured over.
    """
    assert RETRIEVAL_INSTRUMENT_ANSWER_NODE_UNION == "answer-node-union-v1"
    assert RETRIEVAL_INSTRUMENT_WHOLE_QUESTION_SEARCH == "whole-question-search-v0"
    assert (
        RETRIEVAL_INSTRUMENT_ANSWER_NODE_UNION
        != RETRIEVAL_INSTRUMENT_WHOLE_QUESTION_SEARCH
    )


# ---------------------------------------------------------------------------
# 3. A run recorded AFTER the change carries both facts
# ---------------------------------------------------------------------------


def _run_config(judge: Any) -> eval_config.RunConfig:
    payload = json.loads(FIXTURE_CONFIG.read_text(encoding="utf-8"))
    payload["dataset_snapshot"] = "provenance-4"
    payload.pop("config_id", None)
    payload["config"]["judge_prompt_version"] = str(judge.prompt_version)
    return eval_config.load_run_config(payload)


def _provenance_dataset() -> dataset_module.Dataset:
    questions = [
        dataset_module.Question(
            id="q001",
            question="What are the requisites of contracts?",
            document="synth-doc",
            category="direct-lookup",
            gold_nodes=["node_synth-doc_article_1"],
            expected_claims=["consent", "object"],
        ),
        dataset_module.Question(
            id="q002",
            question="What is prescription?",
            document="synth-doc",
            category="definition",
            gold_nodes=["node_synth-doc_article_4"],
            expected_claims=["extinguishes", "claim"],
        ),
    ]
    return dataset_module.Dataset(
        name="provenance",
        questions=questions,
        snapshot_id="provenance-4",
    )


def _provenance_runner(corpus: Any) -> Any:
    from apps.api.agent.graph import StubGenerator
    from apps.api.evaluation.runner import Runner

    judge = eval_judge.CannedJudge()
    return Runner(
        corpus["db_path"],
        corpus["slug"],
        _run_config(judge),
        index_root=Path(corpus["index_root"]),
        generator=StubGenerator(),
        judge=judge,
    )


def test_a_new_run_records_the_instrument_and_the_layer_reported_lex_paths(
    corpus: Any,
) -> None:
    """A run recorded after the change carries BOTH facts, and the lex
    paths are what the retrieval layer actually reported for this
    corpus — verified against ``score_nodes``'s own report, not against
    a re-stat by the test."""
    runner = _provenance_runner(corpus)
    outcome = runner.run(_provenance_dataset())

    loaded = eval_store.load_evaluation(corpus["db_path"], outcome["evaluation_id"])
    assert loaded is not None
    run = loaded["run"]

    assert run["retrieval_instrument"] == RETRIEVAL_INSTRUMENT_ANSWER_NODE_UNION

    recorded = run["lex_paths"]
    assert isinstance(recorded, list) and recorded, "the run must record its lex paths"
    # What the layer ACTUALLY did for this document, straight from the
    # retrieval layer's own report:
    _hits, lex_path = scoring.score_nodes(
        corpus["db_path"], corpus["doc_id"], "What is prescription?"
    )
    expected = scoring.format_lex_path(lex_path)
    assert recorded == [expected]
    assert expected.startswith(scoring.LEX_PATH_PER_DOCUMENT_BM25)

    # And the raw row carries them as columns, not only through the join.
    conn = sqlite3.connect(corpus["db_path"])
    row = conn.execute(
        "SELECT retrieval_instrument, lex_paths FROM evaluation_runs WHERE id = ?",
        (outcome["evaluation_id"],),
    ).fetchone()
    conn.close()
    assert row[0] == RETRIEVAL_INSTRUMENT_ANSWER_NODE_UNION
    assert json.loads(row[1]) == [expected]


# ---------------------------------------------------------------------------
# 4. A run recorded BEFORE the change reads as not recorded
# ---------------------------------------------------------------------------


def _insert_legacy_run(db_path: str, evaluation_id: str) -> None:
    """A run row in the EXACT shape the pre-change code wrote: the 17
    columns, no provenance, so the two new columns stay SQL NULL."""
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO evaluation_runs (id, dataset, dataset_snapshot, config,"
        " config_id, code_version, model, backend, temperature, seed,"
        " judge_prompt_version, split_seed, arms, k, status, metrics, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            evaluation_id,
            "test",
            "v1-test",
            json.dumps({"legacy": True}),
            "cfg_legacy",
            "day6-eval-v1",
            "qwen2.5:3b",
            "ollama",
            0.0,
            42,
            "judge-v1-batch",
            42,
            '["tuklas", "vector"]',
            "[1, 3, 5, 10]",
            "done",
            json.dumps({"arms": {}}),
            "2026-10-21T00:00:00+00:00",
        ),
    )
    conn.commit()
    conn.close()


def test_a_legacy_run_reads_as_not_recorded_for_both_fields(corpus: Any) -> None:
    """NULL is the sentinel: passed through as NULL, never as an empty
    string, never as a path name, never back-filled."""
    evaluation_id = "eval_legacy01"
    _insert_legacy_run(corpus["db_path"], evaluation_id)

    loaded = eval_store.load_evaluation(corpus["db_path"], evaluation_id)
    assert loaded is not None
    run = loaded["run"]
    assert run["retrieval_instrument"] is None
    assert run["lex_paths"] is None

    conn = sqlite3.connect(corpus["db_path"])
    raw = conn.execute(
        "SELECT retrieval_instrument, lex_paths FROM evaluation_runs WHERE id = ?",
        (evaluation_id,),
    ).fetchone()
    conn.close()
    assert raw[0] is None and raw[1] is None


def test_a_new_run_that_records_no_lex_path_writes_null_not_an_empty_list() -> None:
    """Not-recorded and recorded-empty are DIFFERENT facts, and the store
    keeps them distinct: ``lex_paths=None`` writes SQL NULL, never "[]"."""
    import tempfile

    from apps.api.ingestion import db as ingestion_db

    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "fresh.db")
        run = eval_store.EvaluationRun(
            id="eval_noLexPath",
            dataset="test",
            dataset_snapshot="v1-test",
            config={},
            config_id="cfg_x",
            code_version="day6-eval-v1",
            model="m",
            backend="b",
            temperature=0.0,
            seed=42,
            judge_prompt_version="judge-v1-batch",
            split_seed=42,
            arms=["tuklas"],
            k=[5],
        )
        eval_store.write_evaluation(db_path, run, [])

        conn = sqlite3.connect(db_path)
        raw_instrument, raw_paths = conn.execute(
            "SELECT retrieval_instrument, lex_paths FROM evaluation_runs WHERE id = ?",
            ("eval_noLexPath",),
        ).fetchone()
        conn.close()
        assert raw_instrument is None
        assert raw_paths is None
        # And the distinction survives the migration machinery: a brand-
        # new database gets both columns from the same code path.
        with ingestion_db.connect(db_path) as conn:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(evaluation_runs)")}
        assert "retrieval_instrument" in columns
        assert "lex_paths" in columns
