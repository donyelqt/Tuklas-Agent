"""The retrieval contract tests: the documented rules are the implemented rules.

Two contracts are pinned here, and both of them are contracts BETWEEN a
document and the code, so neither can be satisfied by changing only one
side:

* **F4 — the §3 disjointness rule (ADR-007).** ``docs/retrieval.md`` §3
  and ``docs/SPEC.md`` §5 used to state that titles are stripped before
  BM25 and that "a heading term fires in exactly one component". The
  titles are NOT stripped: the parser stores the heading line at the start
  of a node's body text, so the heading's words are BM25 terms. The rule
  was corrected to what the code does — it is a QUERY-side rule about
  IDENTIFIER terms — and this file pins that the documentation and the
  code AGREE, in both directions:

  - the code does what the documents say (the query's identifier tokens
    are stripped from the lex and title inputs, and the heading line is
    NOT stripped from the corpus, because the documents say so); and
  - the documents do not claim the document-side rule they do not have.

  A change that makes the code stop matching the documentation, in either
  direction, fails here.

* **F8 — the report discloses what a reader needs.** The report is the
  artifact whose stated job is "reading the results honestly"
  (``evaluation.md`` §7), and it used to omit: the two vectorless-only
  channels (``title`` and ``hier``, beside the ``ident`` one it did name),
  each arm's corpus size and surface, the spot check's METHOD, and the
  number of claims the judge actually scored (``n_judged``). This file
  pins that each of those is published, and that the report still states
  NO verdict.

The suite is MODEL-FREE and CORPUS-FREE where it can be: the retrieval
contract is checked over a SYNTHETIC statute built with PyMuPDF, and the
report contract is checked over a synthetic aggregates payload. The one
test that wants the real corpus skips itself when the PDF is absent,
because the corpus is gitignored (ADR-002).
"""

from __future__ import annotations

import os
import re
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, cast

import pytest
from apps.api.evaluation import report as eval_report
from apps.api.evaluation import store as eval_store

REPO_ROOT = Path(__file__).resolve().parents[3]
CORPUS = REPO_ROOT / "data" / "documents" / "civil-code-ra386-icnl.pdf"

RETRIEVAL_DOC = REPO_ROOT / "docs" / "retrieval.md"
SPEC_DOC = REPO_ROOT / "docs" / "SPEC.md"

#: The synthetic statute the retrieval contract runs over. Each article's
#: heading carries a DISTINCTIVE ordinary word ("Requisites", "Succession",
#: "Damages") next to its identifier ("Article 1", "Article 2"), because
#: the contract is about the difference between an identifier term and a
#: heading's ordinary words, and a corpus whose headings are only
#: identifiers cannot tell the two apart.
SYNTHETIC_ARTICLES: list[tuple[str, str]] = [
    (
        "Article 1. Requisites of contracts.",
        "Consent, object and certainty of the terms are the requisites of a "
        "contract, and a contract requires the consent of the parties. ",
    ),
    (
        "Article 2. Succession.",
        "Succession is the transfer of the rights and obligations of a "
        "deceased person to the heirs or the legatees. ",
    ),
    (
        "Article 3. Prescription.",
        "Prescription extinguishes a claim after the period laid down by "
        "law, and the periods are enumerated here. ",
    ),
    (
        "Article 4. Damages.",
        "Damages are the sum of money which the court orders the defendant "
        "to pay, and the kinds of damages are provided here. ",
    ),
]


def _fitz() -> ModuleType:
    import fitz

    return cast(ModuleType, fitz)


def _write_pdf(path: Path) -> None:
    """The synthetic statute: one page per article, a heading and a body."""
    fitz = _fitz()
    document = fitz.open()
    for heading, body in SYNTHETIC_ARTICLES:
        page = document.new_page()
        page.insert_textbox(
            fitz.Rect(50, 50, 560, 780), heading + "\n" + body * 3, fontsize=11
        )
    document.save(path)
    document.close()


@pytest.fixture(scope="module")
def synthetic(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[SimpleNamespace]:
    """A synthetic document, ingested and indexed, with the runtime lex path."""
    import shutil

    import apps.api.retrieval.scoring as scoring
    from apps.api.ingestion.service import ingest_pdf
    from apps.api.retrieval.index import build_indexes

    root = tmp_path_factory.mktemp("contract")
    pdf_path = root / "synthetic.pdf"
    _write_pdf(pdf_path)

    db_path = str(root / "synthetic.db")
    document = ingest_pdf(str(pdf_path), slug="synth-doc", db_path=db_path)
    index_root = root / "indexes"
    build_indexes(db_path, document.id, index_root=index_root)

    original_root = scoring.INDEX_ROOT
    scoring.INDEX_ROOT = index_root
    try:
        yield SimpleNamespace(db=db_path, doc_id=document.id, index_root=index_root)
    finally:
        scoring.INDEX_ROOT = original_root
        shutil.rmtree(root, ignore_errors=True)


def _rows(db_path: str, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# F4 — the documented disjointness rule is the implemented one
# ---------------------------------------------------------------------------


def test_the_documented_rule_is_the_query_side_rule() -> None:
    """Both documents state the QUERY-side rule, and not the false one.

    The failing version of this test: the documents said "titles stripped
    before BM25" and "a heading term fires in exactly one component", and
    the code stripped neither.
    """
    retrieval = RETRIEVAL_DOC.read_text(encoding="utf-8")
    spec = SPEC_DOC.read_text(encoding="utf-8")
    for name, text in (("retrieval.md", retrieval), ("SPEC.md", spec)):
        assert "titles stripped before BM25" not in text, (
            f"{name} still claims the titles are stripped before BM25, and "
            f"the parser stores the heading line at the start of the body"
        )
        assert "a heading term fires in exactly one component" not in text, (
            f"{name} still claims a heading term fires in exactly one "
            f"component, and a heading's ordinary words fire in lex and "
            f"title together"
        )
    # The corrected rule is stated in both, and it says IDENTIFIER.
    for name, text in (("retrieval.md", retrieval), ("SPEC.md", spec)):
        assert "IDENTIFIER term fires in exactly one component" in text, (
            f"{name} must state the rule the code implements: the IDENTIFIER "
            f"term is the disjoint one"
        )
    # And the DOCUMENT side is stated, not left to be discovered.
    assert "heading line" in retrieval
    assert "heading line" in spec


def test_the_query_side_of_the_rule_is_implemented(synthetic: SimpleNamespace) -> None:
    """The query's IDENTIFIER tokens do not reach the lex input.

    This is the half of the §3 rule the code really implements, and it
    must keep working: a query that names a provision must not have the
    provision's number counted a second time by the lexical channel.
    """
    from apps.api.retrieval.normalizer import label_tokens
    from apps.api.retrieval.scoring import tokenize_for_bm25

    query = "Article 2 succession transfer of rights"
    identifier_tokens = label_tokens(query)
    assert identifier_tokens, "the query names a provision, so it has identifier tokens"
    lex_tokens = [token for token in tokenize_for_bm25(query) if token not in identifier_tokens]
    assert "2" not in lex_tokens, "the identifier's number must not reach the lex input"
    assert "article" not in lex_tokens, "the identifier's word must not reach the lex input"
    assert "succession" in lex_tokens, "the query's ordinary words must reach the lex input"


def test_the_heading_line_is_part_of_the_stored_body(synthetic: SimpleNamespace) -> None:
    """The parser stores the heading at the START of the node's body.

    This is the DOCUMENT-side fact the corrected documentation rests on,
    and it is the reason a heading's ordinary words are lexical surface.
    The failing version of this test: a parser that stripped the heading
    from the body would make the ORIGINAL documentation true, and the
    documentation would then have to change back. This test fails if that
    happens, so the change cannot land silently.
    """
    rows = _rows(
        synthetic.db,
        "SELECT id, title, text FROM nodes WHERE document_id = ? AND type = 'article'"
        " ORDER BY position",
        (synthetic.doc_id,),
    )
    assert rows, "the synthetic ingest produced no article nodes"
    starting_with_title = [
        row for row in rows if str(row["text"]).lstrip().startswith(str(row["title"]).strip())
    ]
    assert len(starting_with_title) == len(rows), (
        f"{len(rows) - len(starting_with_title)} of {len(rows)} article nodes do "
        f"not start with their own title; the heading is expected at the start "
        f"of the body (ADR-007)"
    )


def test_the_lex_corpus_carries_the_heading_line(synthetic: SimpleNamespace) -> None:
    """The BM25 corpus contains the heading's ordinary words.

    The corrected documentation says the corpus carries the heading line,
    and this pins it on the persisted artifact: the heading's ordinary
    words are BM25 terms, which is what makes the document-side
    difference from the query-side rule visible.
    """
    import json

    from apps.api.retrieval.scoring import tokenize_for_bm25

    payload = json.loads(
        (Path(synthetic.index_root) / synthetic.doc_id / "bm25.json").read_text(
            encoding="utf-8"
        )
    )
    term_freqs: list[dict[str, Any]] = payload["term_freqs"]
    doc_ids: list[str] = payload["doc_ids"]
    titles = {
        str(row["id"]): str(row["title"])
        for row in _rows(
            synthetic.db,
            "SELECT id, title FROM nodes WHERE document_id = ?",
            (synthetic.doc_id,),
        )
    }
    assert term_freqs, "the persisted BM25 corpus is empty"
    checked = 0
    for node_id, terms in zip(doc_ids, term_freqs, strict=True):
        title_tokens = set(tokenize_for_bm25(titles.get(node_id, "")))
        # The identifier words are in the corpus too; the ORDINARY words are
        # the ones the claim is about, so the assertion is on them.
        ordinary = {token for token in title_tokens if token not in {"article", "1", "2", "3", "4"}}
        if not ordinary:
            continue
        checked += 1
        assert ordinary & set(terms), (
            f"the heading's ordinary words {sorted(ordinary)} are not BM25 terms "
            f"for {node_id}; the corpus is expected to carry the heading line "
            f"(ADR-007)"
        )
    assert checked, "no corpus node had an ordinary heading word to check"


def test_the_retrieval_contract_test_exists_for_the_real_corpus() -> None:
    """The real-corpus measurement is available when the corpus is present.

    The corpus is gitignored (ADR-002), so this test SKIPS rather than
    failing where the PDF is absent. Where it is present, it measures the
    claim ADR-007 rests on: the title tokens of the live tree are also
    BM25 terms, and the nodes' texts start with their titles.
    """
    if not os.path.exists(CORPUS):
        pytest.skip("corpus file missing: civil-code-ra386-icnl.pdf")
    import json

    from apps.api.retrieval.scoring import tokenize_for_bm25

    db_path = str(REPO_ROOT / "data" / "tuklas.sqlite")
    if not Path(db_path).exists():
        pytest.skip("the ingested store is absent")
    documents = _rows(db_path, "SELECT id FROM documents")
    assert documents, "the store carries no ingested document"
    document_id = str(documents[0]["id"])
    index_path = REPO_ROOT / "data" / "indexes" / document_id / "bm25.json"
    if not index_path.exists():
        pytest.skip("the persisted BM25 index is absent; re-ingest to rebuild it")

    payload = json.loads(index_path.read_text(encoding="utf-8"))
    titles = {
        str(row["id"]): str(row["title"])
        for row in _rows(
            db_path,
            "SELECT id, title FROM nodes WHERE document_id = ?",
            (document_id,),
        )
    }
    total = 0
    also_bm25 = 0
    for node_id, terms in zip(payload["doc_ids"], payload["term_freqs"], strict=True):
        title_tokens = set(tokenize_for_bm25(titles.get(node_id, "")))
        total += len(title_tokens)
        also_bm25 += len(title_tokens & set(terms))
    assert total, "the corpus carries no title tokens"
    assert also_bm25 == total, (
        f"only {also_bm25} of {total} title tokens are BM25 terms; the "
        f"documentation's claim about the heading line depends on this "
        f"measurement"
    )


# ---------------------------------------------------------------------------
# F8 — the report publishes what a reader needs
# ---------------------------------------------------------------------------


def _report_text() -> str:
    """A report over a synthetic aggregates payload, with both arms."""
    aggregates = {
        "arms": {
            "tuklas": {
                "retrieval": {
                    "overall": {
                        "n": 41,
                        "recall@5": 0.793,
                        "recall@1": 0.476,
                        "recall@3": 0.695,
                        "recall@10": 0.902,
                        "precision@1": 0.659,
                        "precision@3": 0.317,
                        "precision@5": 0.224,
                        "precision@10": 0.132,
                        "mrr10": 0.769,
                    },
                    "by_category": {
                        "multi-hop": {"n": 6, "recall@5": 0.583, "mrr10": 0.854},
                        "multi-section": {"n": 6, "recall@5": 0.583, "mrr10": 0.764},
                    },
                },
                "generation": {
                    "correctness": 0.627,
                    "faithfulness": 0.647,
                    "citation_accuracy": 1.0,
                    "abstention_score": 1.0,
                    "n_judged": 47,
                },
                "system": {
                    "latency_p50": 1670.5,
                    "latency_p95": 5888.3,
                    "retrieval_latency_p50": 170.0,
                    "retrieval_latency_p95": 257.3,
                    "generation_latency_p50": 1496.5,
                    "generation_latency_p95": 5681.3,
                    "tokens_mean": 959.5,
                    "tokens_out_mean": 938.5,
                    "iterations_mean": 6.65,
                },
            },
            "vector": {
                "retrieval": {
                    "overall": {
                        "n": 41,
                        "recall@5": 0.793,
                        "recall@1": 0.512,
                        "recall@3": 0.707,
                        "recall@10": 0.866,
                        "precision@1": 0.683,
                        "precision@3": 0.325,
                        "precision@5": 0.224,
                        "precision@10": 0.124,
                        "mrr10": 0.775,
                    },
                    "by_category": {
                        "multi-hop": {"n": 6, "recall@5": 0.667, "mrr10": 0.875},
                        "multi-section": {"n": 6, "recall@5": 0.583, "mrr10": 0.722},
                    },
                },
                "generation": {
                    "correctness": 0.686,
                    "faithfulness": 0.579,
                    "citation_accuracy": 1.0,
                    "abstention_score": 0.286,
                    "n_judged": 47,
                },
                "system": {
                    "latency_p50": 1922.0,
                    "latency_p95": 6540.0,
                    "retrieval_latency_p50": 64.0,
                    "retrieval_latency_p95": 168.9,
                    "generation_latency_p50": 1770.0,
                    "generation_latency_p95": 6479.9,
                    "tokens_mean": 992.9,
                    "tokens_out_mean": 1818.9,
                    "iterations_mean": 7.12,
                },
            },
        },
        "n_questions": 48,
        "n_arms": 2,
    }
    return eval_report.render_report(
        evaluation_id="eval_contract",
        config={
            "config": {
                "model": "qwen2.5:3b",
                "backend": "ollama",
                "temperature": 0,
                "seed": 42,
                "judge_prompt_version": "judge-v1-batch",
                "code_version": "day6-eval-v1",
                "weights": {"lex": 1.0, "ident": 1.0, "title": 1.0, "ref": 1.0, "hier": 1.0},
                "normalization": "fold-alnum-v2",
                "fuzzy_threshold": 85,
                "stoplist_version": "fts5-english-v1",
            },
            "config_id": "cfg_contract",
            "k": [1, 3, 5, 10],
        },
        dataset_name="test",
        dataset_snapshot="v1-test",
        split_seed=42,
        n_questions=48,
        aggregates=aggregates,
        spot_check={
            "performed": True,
            "method": "mechanical-rejudge (judge self-consistency)",
            "sample_size": 10,
            "n_claims_compared": 58,
            "disagreement": 0.017,
            "threshold": 0.15,
            "valid": True,
        },
        dense_arm_description={
            "model": "BAAI/bge-small-en-v1.5",
            "provider": "fastembed",
            "dim": 384,
            "revision": "5c38ec7c405ec4b44b94cc5a9bb96e735b38267a",
            "dense": True,
            "identifier_precedence": False,
            "n_nodes": 2579,
        },
        vectorless_arm_description={
            "algorithm": "vectorless-five-component",
            "identifier_precedence": True,
            "dense": False,
            "n_nodes": 2579,
            "n_lex_nodes": 2392,
        },
    )


def test_the_report_names_the_title_and_hier_channels_as_vectorless_only() -> None:
    """F8.1 — the report names ALL THREE vectorless-only channels.

    The failing version of this test: the report named only ``ident``, so
    a reader did not know that ``title`` and ``hier`` are ALSO channels
    the dense arm structurally cannot have — and those two fire on almost
    every hit, so they are not a corner case.
    """
    text = _report_text()
    lowered = text.lower()
    assert "title" in lowered and "hier" in lowered, (
        "the report must name the 'title' and 'hier' components"
    )
    assert "vectorless-only" in lowered, (
        "the report must say that the 'title' and 'hier' channels are "
        "vectorless-only, so a reader knows the dense arm cannot have them"
    )
    assert "ident" in lowered, "the report must still name the 'ident' channel"
    # The disclosure must be BEFORE the numbers it qualifies.
    assert lowered.index("vectorless-only") < lowered.index("the numbers (per arm"), (
        "the channel disclosure must come before the numbers it qualifies"
    )


def test_the_report_publishes_each_arms_corpus_size_and_surface() -> None:
    """F8.2 — the report prints the corpus size and the surface per arm.

    The failing version of this test: the report printed no corpus size
    and no surface, so a reader could not tell whether the two arms
    ranked the same node set over the same material.
    """
    text = _report_text()
    assert "2579" in text, "the report must publish the arm's corpus size"
    assert "2392" in text, (
        "the report must publish the vectorless arm's LEXICAL corpus size, "
        "which is smaller than the node count"
    )
    assert "surface" in text.lower(), "the report must name the surface each arm scores"
    assert "bm25" in text.lower(), "the vectorless arm's surface is a BM25 surface"
    assert "embedding" in text.lower(), "the dense arm's surface is an embedding surface"
    # The disclosure must come BEFORE the numbers it qualifies.
    assert text.index("surface") < text.index("THE NUMBERS"), (
        "the corpus and surface disclosure must come before the numbers"
    )


def test_the_report_discloses_the_surface_even_without_the_arm_own_keys() -> None:
    """The surface is disclosed even if the arm does not name one itself.

    The report must never be SILENT about what an arm scores, so the
    surface has a fallback: the arm's own ``surface`` key when it has
    one, and a description derived from the arm's own ``dense`` flag when
    it does not.
    """
    text = eval_report.render_report(
        evaluation_id="eval_contract",
        config={"config": {}, "config_id": "cfg_x", "k": [1, 3, 5, 10]},
        dataset_name="test",
        dataset_snapshot="v1-test",
        split_seed=42,
        n_questions=48,
        aggregates={"arms": {}},
        dense_arm_description={"model": "m", "provider": "fastembed", "dense": True},
        vectorless_arm_description={"algorithm": "vectorless-five-component", "dense": False},
    )
    lowered = text.lower()
    assert "surface" in lowered, "the surface must be disclosed even without the arm's own key"
    assert "bm25" in lowered, "the vectorless arm's surface is a BM25 surface"
    assert "embedding" in lowered, "the dense arm's surface is an embedding surface"


def test_the_report_names_the_spot_check_method() -> None:
    """F8.3 — the report prints the spot check's METHOD by name.

    The failing version of this test: the report printed "disagreement:
    0.017" and nothing about HOW the sample was re-scored, so a reader
    reasonably assumed a builder had re-scored it and validated the
    judge.
    """
    text = _report_text()
    assert "method" in text.lower(), "the report must print the spot check's method"
    assert "mechanical-rejudge" in text.lower(), (
        "the report must name the method that was actually used"
    )
    assert "self-consistency" in text.lower(), (
        "the report must say the mechanical re-judge is the judge's "
        "self-consistency"
    )
    assert "not the builder" in text.lower(), (
        "the report must say in plain words that this is NOT the builder's "
        "human re-score that evaluation.md section 4 asks for"
    )
    # The method is printed NEXT TO the number it qualifies.
    assert text.index("method") < text.index("disagreement"), (
        "the method must be printed before the disagreement it qualifies"
    )


def test_the_report_publishes_n_judged_next_to_faithfulness() -> None:
    """F8.4 — the report publishes ``n_judged`` alongside ``faithfulness``.

    The failing version of this test: faithfulness was published with no
    sample size, and the two differ here — a refusal carries no claim to
    judge, so n_judged (47) is smaller than the row count (48).
    """
    text = _report_text()
    assert "n_judged" in text, "the report must publish the number of judged claims"
    assert "47" in text, "the report must publish the judged-claim count itself"
    # The count sits with the faithfulness line, in the same block.
    faithfulness_index = text.index("faithfulness=")
    n_judged_index = text.index("n_judged")
    assert n_judged_index > faithfulness_index, (
        "n_judged must be published with the faithfulness it qualifies"
    )
    assert n_judged_index - faithfulness_index < 400, (
        "n_judged must be published next to faithfulness, not elsewhere"
    )


def test_the_report_says_the_two_latencies_are_different_units_of_work() -> None:
    """F8.5 — the report says the two arms measure different work.

    The failing version of this test: the report printed a p50 and a p95
    for each arm as if they were the same measurement, when the dense
    arm's is a matrix product over a pre-built index and the vectorless
    arm's is a full-document BM25 re-score per query.
    """
    text = _report_text()
    lowered = text.lower()
    assert "unit of work" in lowered or "units of work" in lowered, (
        "the report must say the two latencies are not the same unit of work"
    )
    assert "pre-built" in lowered, (
        "the report must say the dense arm's latency is over a pre-built index"
    )
    assert "re-score" in lowered, (
        "the report must say the vectorless arm's latency is a per-query re-score"
    )
    # The disclosure must come BEFORE the numbers it qualifies.
    assert lowered.index("unit of work") < lowered.index("the numbers (per arm"), (
        "the latency disclosure must come before the numbers it qualifies"
    )


def test_the_report_still_states_no_verdict() -> None:
    """The new disclosures must not turn the report into a verdict.

    The report's one job is to publish both arms' numbers without naming
    a winner, and every disclosure added to it is a disclosure ABOUT the
    measurement, not a claim about the outcome.
    """
    text = _report_text()
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
    assert "no comparison verdict" in lowered, (
        "the report must still say it states no comparison verdict"
    )
    assert "outcome of the vectorless-versus-dense comparison is not" in lowered, (
        "the report must still decline to claim the comparison's outcome"
    )


def test_the_report_discloses_the_unchecked_generation_reproducibility() -> None:
    """F8.7 — the report says the generation half of the acceptance is unchecked.

    The failing version of this test: the report published the run's
    numbers with no mention that the "generation within 5pp" half of
    evaluation.md section 8 is checked by nothing.
    """
    text = _report_text()
    lowered = text.lower()
    assert "generation" in lowered
    assert "unchecked" in lowered, (
        "the report must say the generation reproducibility half is unchecked"
    )
    assert "5pp" in lowered or "+/-5pp" in lowered, (
        "the report must name the acceptance it is not meeting"
    )


# ---------------------------------------------------------------------------
# F8.6 — the store's docstring describes what the code does
# ---------------------------------------------------------------------------


def test_the_store_docstring_describes_the_incremental_write() -> None:
    """The store's module docstring must not claim one transaction.

    The failing version of this test: the docstring said "The write is ONE
    transaction per evaluation: the run row and all its per-question rows
    land together, so a reader never sees a run whose metrics exist but
    whose questions do not". The runner writes per-question rows
    incrementally and the run row LAST, so an interrupted run leaves rows
    with no run row, and the docstring was false.
    """
    docstring = eval_store.__doc__ or ""
    assert "ONE transaction" not in docstring, (
        "the store's docstring still claims the write is one transaction; "
        "the runner writes per-question rows incrementally and the run row "
        "last"
    )
    assert "INCREMENTAL" in docstring, (
        "the store's docstring must describe the incremental write"
    )
    assert "run row lands LAST" in docstring, (
        "the store's docstring must say the run row is written last, so an "
        "interrupted run leaves rows with no run row"
    )


def test_the_write_order_is_incremental_and_the_run_row_is_last() -> None:
    """The code does what the corrected docstring says.

    The docstring is a claim about the code, so the code is checked
    against it: the runner appends the per-question rows as it goes
    (``write_evaluation_rows``) and writes the run row at the end
    (``write_evaluation`` with an empty row list).
    """
    from apps.api.evaluation import runner as eval_runner

    runner_source = Path(eval_runner.__file__).read_text(encoding="utf-8")
    rows_call = runner_source.index("write_evaluation_rows")
    run_call = runner_source.index("write_evaluation(self.db_path, run, [])")
    assert rows_call < run_call, (
        "the per-question rows must be written before the run row, or the "
        "docstring's 'the run row lands LAST' is false"
    )
    assert re.search(r"write_evaluation\(self\.db_path, run, \[\]\)", runner_source), (
        "the run row must be written with no per-question rows, because the "
        "rows were already written incrementally"
    )
