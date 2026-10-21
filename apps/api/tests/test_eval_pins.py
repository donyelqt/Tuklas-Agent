"""The run's PINS must be the ones that were APPLIED (evaluation.md §6).

evaluation.md §6 says a run records "a weights snapshot" per question per
arm, and that the run record carries the bonus flags and the stoplist
version. A run record that carries a value the run did not apply is not
a record of the run; it is a record of what somebody asked for. Two
defects are pinned here, each of them VERIFIED by measurement before it
was fixed:

* **F2** — the pinned weights never reached the scorer. ``search_nodes``
  took no weights parameter and called ``score_nodes`` with the
  defaults, so the config's weights were validated, hashed into the
  ``config_id`` and recorded in every ``retrieval_runs`` row while
  affecting not a single score. Two configs with different weight
  snapshots produced identical runs under different ids.

* **F2b** — ``bonus_flags`` and ``stoplist_version`` were validated and
  hashed into the ``config_id`` while NOTHING read them. The retrieval
  layer has no phrase or fuzzy bonus implementation, and the agent's
  stoplist had no version name. The pins were inert: they changed the
  run's identity and described nothing.

* **F3** — ``citation_accuracy`` was a tautology. The check verified
  that the cited ids EXIST in the nodes table, but the ids came FROM
  the nodes table (the citation assembler builds them from the kept
  evidence set), so the check could not fail. It reported 1.000 for
  every one of the 192 stored rows, both arms, both splits, with the
  same visual weight as correctness and faithfulness. The spec's other
  half - "SUPPORT THE ATTACHED CLAIM" - was not implemented anywhere in
  the repository.

The suite is MODEL-FREE and CORPUS-FREE: the corpus is a SYNTHETIC PDF
built with PyMuPDF, the generation is the deterministic stub, and the
judge is the canned one.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest
from apps.api.agent import stoplist as stoplist_module
from apps.api.evaluation import arms as arms_module
from apps.api.evaluation import config as eval_config
from apps.api.evaluation import dataset as dataset_module
from apps.api.evaluation import judge as eval_judge
from apps.api.evaluation import scorer as eval_scorer
from apps.api.evaluation import store as eval_store
from apps.api.retrieval import scoring as scoring_module

REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURE_CONFIG = (
    Path(__file__).resolve().parents[1]
    / "evaluation"
    / "fixtures"
    / "example-run-config.json"
)
DEV_FIXTURE_CONFIG = (
    Path(__file__).resolve().parents[1]
    / "evaluation"
    / "fixtures"
    / "example-dev-run-config.json"
)
EVAL_CONFIG_SCRIPT = REPO_ROOT / "scripts" / "check_eval_config.py"
CI_FIXTURE_CONFIG = REPO_ROOT / "scripts" / "fixtures" / "example-run-config.json"

#: The synthetic statute's pages. Eight articles, each about a different
#: subject, so a weight vector can genuinely reorder them: the articles
#: are lexically distinguishable and none of them is a near-duplicate of
#: another.
_SYNTHETIC_PAGES: list[tuple[str, str]] = [
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


def _fitz() -> ModuleType:
    import fitz

    return cast(ModuleType, fitz)


def _write_pdf(path: Path) -> None:
    """The synthetic statute: one page per article, a heading and a body."""
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
    """A synthetic document, ingested, indexed, and ready to query."""
    import shutil

    from apps.api.ingestion.service import ingest_pdf
    from apps.api.retrieval.index import build_indexes

    root = tmp_path_factory.mktemp("pins")
    pdf_path = root / "synthetic.pdf"
    _write_pdf(pdf_path)

    db_path = str(root / "corpus.db")
    document = ingest_pdf(str(pdf_path), slug="synth-doc", db_path=db_path)
    index_root = root / "indexes"
    build_indexes(db_path, document.id, index_root=index_root)

    import apps.api.retrieval.scoring as scoring

    original_root = scoring.INDEX_ROOT
    scoring.INDEX_ROOT = Path(index_root)
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


def _config(weights: dict[str, float]) -> eval_config.RunConfig:
    """The fixture run config, with the given weights, validated."""
    payload = json.loads(FIXTURE_CONFIG.read_text(encoding="utf-8"))
    payload.pop("config_id", None)
    payload["config"]["weights"] = weights
    payload["config"]["judge_prompt_version"] = eval_judge.CannedJudge().prompt_version
    return eval_config.load_run_config(payload)


#: The five components, and one vector that only moves the STRUCTURAL
#: channel (hier) and one that only moves the LEXICAL one. The two differ
#: in which nodes they promote, which is what the F2 test measures.
_ALL_ON: dict[str, float] = {
    "lex": 1.0,
    "ident": 1.0,
    "title": 1.0,
    "ref": 1.0,
    "hier": 1.0,
}
_LEX_ONLY: dict[str, float] = {
    "lex": 1.0,
    "ident": 0.0,
    "title": 0.0,
    "ref": 0.0,
    "hier": 0.0,
}
_HIER_ONLY: dict[str, float] = {
    "lex": 0.0,
    "ident": 0.0,
    "title": 0.0,
    "ref": 0.0,
    "hier": 1.0,
}


# ---------------------------------------------------------------------------
# F2 - the pinned weights must reach the scorer
# ---------------------------------------------------------------------------


def test_a_different_weight_vector_changes_the_retrieved_order(corpus: Any) -> None:
    """Two weight vectors over the SAME question rank differently.

    This is the discriminating half of F2. Before the fix, ``search_nodes``
    took no weights at all and called ``score_nodes`` with the defaults,
    so the ranking was the same for every weight vector: the config's
    weights were hashed into the run's identity and applied to nothing.
    After the fix the ranking MOVES when the weights move, which is the
    only way to know the weights reached the scorer.

    The question names NO provision, so the identifier-precedence rule
    (ADR-005) does not pin one node to the top of the ranking: the whole
    order is free to move. A question that named a provision would leave
    the identified node first under every weighting, and the assertion
    would pass or fail for a reason that has nothing to do with the
    weights.
    """
    from apps.api.agent.graph import StubGenerator

    rankings: dict[str, list[str]] = {}
    for name, weights in (("all", _ALL_ON), ("lex", _LEX_ONLY), ("hier", _HIER_ONLY)):
        arm = arms_module.TuklasArm(
            corpus["db_path"],
            corpus["slug"],
            generator=StubGenerator(),
            index_root=Path(corpus["index_root"]),
            weights=weights,
        )
        result = arm.retrieve("What are the requisites of contracts?")
        rankings[name] = list(result.node_ids)

    # The weights changed the order. Not "the top node changed" (the
    # right answer stays first either way) but the ORDER of the ranking,
    # which is what a weight vector is supposed to control.
    assert rankings["lex"] != rankings["hier"], rankings
    assert rankings["all"] != rankings["hier"], rankings


def test_the_search_tool_passes_the_weights_through(corpus: Any) -> None:
    """``search_nodes`` scores with the weights it was handed.

    The seam the run depends on: the retrieval API hands the weight
    vector to the scorer, and the scorer clamps it rather than ignoring
    it. Before the fix the parameter did not exist, so a caller that
    pinned weights had nowhere to put them.
    """
    from apps.api.retrieval.api import search_nodes

    hits = search_nodes(
        query="What are the requisites of contracts?",
        document_id=corpus["slug"],
        top_k=10,
        db_path=corpus["db_path"],
        weights=_LEX_ONLY,
    )
    assert hits
    # The clamped weights are what the scorer used. The score of a node
    # is the weighted sum of its components, so a lex-only vector scores
    # the top node at its lex value.
    top = hits[0]
    assert top.components
    assert top.score == pytest.approx(
        sum(
            float(top.components.get(name, 0.0)) * weight
            for name, weight in _LEX_ONLY.items()
        )
    )


def test_the_recorded_snapshot_is_the_one_that_was_applied(corpus: Any) -> None:
    """The stored row carries the APPLIED weights, not the config's.

    The second half of F2. The config's weights and the applied weights
    are the same number here, because the runner now hands the config's
    weights to the arm. The test is what proves the chain is closed: a
    config that pins one weight vector produces rows carrying that
    vector, whatever the default would have been.

    It also pins the dense arm's row, which has no five-component weight
    vector of its own: the row records the run's weights, because the
    run's identity is the run's, and the dense ranking is not a
    five-component score. The report says so, and the row must match
    what the report says.
    """
    from apps.api.agent.graph import StubGenerator
    from apps.api.evaluation.runner import Runner

    config = _config(_LEX_ONLY)
    judge = eval_judge.CannedJudge()
    runner = Runner(
        corpus["db_path"],
        corpus["slug"],
        config,
        index_root=Path(corpus["index_root"]),
        generator=StubGenerator(),
        judge=judge,
    )
    dataset = dataset_module.Dataset(
        name="pins",
        snapshot_id="pins-1",
        questions=[
            dataset_module.Question(
                id="q001",
                question="What are the requisites of contracts?",
                document="synth-doc",
                category="direct-lookup",
                gold_nodes=["node_synth-doc_article_1"],
                expected_claims=["consent"],
            )
        ],
    )
    outcome = runner.run(dataset)
    assert outcome["status"] == "done"

    loaded = eval_store.load_evaluation(corpus["db_path"], outcome["evaluation_id"])
    assert loaded is not None
    rows = loaded["questions"]
    assert rows
    for row in rows:
        assert row["weights"] == _LEX_ONLY, row


def test_the_config_id_changes_when_the_weights_change() -> None:
    """Two weight vectors are two run identities.

    The point of the id is that "the same config" is checkable. If the
    weights did not reach the scorer, two ids would describe one run, and
    the id would be a label rather than an identity.
    """
    payload = json.loads(FIXTURE_CONFIG.read_text(encoding="utf-8"))
    payload.pop("config_id", None)
    first = eval_config.derive_config_id(payload)
    changed = copy.deepcopy(payload)
    changed["config"]["weights"] = _LEX_ONLY
    assert eval_config.derive_config_id(changed) != first


# ---------------------------------------------------------------------------
# F2b - the bonus flags and the stoplist version must be REAL pins
# ---------------------------------------------------------------------------


def test_the_retrieval_layer_applies_no_bonus() -> None:
    """The applied bonus state is declared in the retrieval layer itself.

    retrieval.md §3 names the two bonuses and says they are "off by
    default and enabled only via tuning", but it gives no magnitude, no
    component and no place in the five-term formula. The layer therefore
    implements NEITHER, and the state it applies is declared in ONE
    place, so the config checker can compare a config against it instead
    of against a second copy of it.
    """
    assert scoring_module.APPLIED_BONUS_FLAGS == {"phrase": False, "fuzzy": False}


def test_the_stoplist_has_a_version() -> None:
    """The stoplist is "versioned in code, not ad hoc" (retrieval.md §3).

    A list that is versioned in code but not NAMED is not versioned: a
    run record that pins a "stoplist version" has to name a version the
    code actually has, or the record describes a list nobody can
    identify.
    """
    assert stoplist_module.STOPLIST_VERSION == "fts5-english-v1"


def test_a_config_that_pins_an_unapplied_bonus_is_refused() -> None:
    """A run cannot RECORD a bonus the retrieval layer does not apply.

    This is the discriminating test for the F2b sub-item. Before the
    change, ``bonus_flags`` was checked for SHAPE only: a config that
    pinned ``phrase: true`` was accepted, hashed into the run's id and
    recorded in the run row, while the retrieval layer applied no bonus
    at all. The run's own record then described a bonus that was never
    applied.
    """
    payload = json.loads(FIXTURE_CONFIG.read_text(encoding="utf-8"))
    payload.pop("config_id", None)
    payload["config"]["bonus_flags"]["phrase"] = True
    violations, _ = eval_config.check_run_config(payload)
    assert any("phrase" in violation for violation in violations), violations


def test_a_config_that_pins_an_unknown_stoplist_version_is_refused() -> None:
    """A run cannot RECORD a stoplist version the code does not have.

    The discriminating test for the other half of the F2b sub-item.
    Before the change, ``stoplist_version`` was checked for being a
    non-empty string, so any string passed and the run row recorded a
    stoplist nobody could identify.
    """
    payload = json.loads(FIXTURE_CONFIG.read_text(encoding="utf-8"))
    payload.pop("config_id", None)
    payload["config"]["stoplist_version"] = "fts5-english-v2"
    violations, _ = eval_config.check_run_config(payload)
    assert any("stoplist_version" in violation for violation in violations), violations


def test_the_committed_fixtures_pin_the_state_the_code_has() -> None:
    """Both fixture configs and the CI fixture stay valid, and unchanged.

    The published run ids (``cfg_0f73ff26c0d38d54`` for the dev config
    and ``cfg_e86e8bfbc803c860`` for the test one) are derived from the
    fixture files' content, so the fixtures' CONTENT must not change:
    the pins are now ENFORCED rather than ignored, and enforcement that
    changed the config would change the id and orphan the published
    report.
    """
    for path, expected_id in (
        (FIXTURE_CONFIG, "cfg_e86e8bfbc803c860"),
        (DEV_FIXTURE_CONFIG, "cfg_0f73ff26c0d38d54"),
        (CI_FIXTURE_CONFIG, "cfg_9ba255191a96aaa9"),
    ):
        payload = json.loads(path.read_text(encoding="utf-8"))
        violations, config_id = eval_config.check_run_config(payload)
        assert violations == [], (path.name, violations)
        assert config_id == expected_id, path.name


def test_the_config_checker_script_reads_the_code_s_own_state() -> None:
    """The CI gate and the runner agree on the pins, because both read
    the code.

    The gate (``scripts/check_eval_config.py``) is a SEPARATE
    implementation of the config rules from the runner's
    (``apps/api/evaluation/config.py``), and the two must not drift. The
    script used to declare its own copy of every rule; the two pins that
    describe the CODE's state are read from the code instead, so a
    change to the retrieval layer's applied bonuses or to the stoplist's
    version cannot leave the gate accepting a config the runner refuses.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "pins_check_eval_config", EVAL_CONFIG_SCRIPT
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.STOPLIST_VERSION == stoplist_module.STOPLIST_VERSION
    assert module.APPLIED_BONUS_FLAGS == scoring_module.APPLIED_BONUS_FLAGS

    # And the script REFUSES the same configs the runner refuses.
    for field, value in (
        ("bonus_flags", {"phrase": True, "fuzzy": False}),
        ("stoplist_version", "fts5-english-v2"),
    ):
        payload = json.loads(FIXTURE_CONFIG.read_text(encoding="utf-8"))
        payload.pop("config_id", None)
        payload["config"][field] = value
        script_violations, _ = module.check_run_config(payload)
        runner_violations, _ = eval_config.check_run_config(payload)
        assert script_violations, field
        assert runner_violations, field


# ---------------------------------------------------------------------------
# F3 - citation accuracy must be a MEASUREMENT
# ---------------------------------------------------------------------------


def test_a_citation_pointing_at_an_unrelated_node_scores_zero() -> None:
    """The support check is the half that can fail.

    This is the discriminating test for F3. Before the change, the check
    only verified that the cited ids EXIST in the nodes table, and the
    ids came from the nodes table, so the check could not fail and
    ``citation_accuracy`` was 1.000 for every row.
    """
    sentence = "The requisites of contracts are consent, object, and certainty of the terms."
    supporting = (
        "Article 1. Requisites of contracts. The requisites of contracts are "
        "consent, object, and certainty of the terms."
    )
    unrelated = (
        "Article 2. Succession. Succession is the transfer of the rights and "
        "obligations of a deceased person to the heirs or legatees."
    )
    assert eval_scorer.citation_supports(sentence, supporting, threshold=85)
    assert not eval_scorer.citation_supports(sentence, unrelated, threshold=85)


def test_the_answer_is_split_into_marker_sentence_pairs() -> None:
    """A sentence with no marker is not a citation claim.

    The check is about the sentences that CITE something. A sentence with
    no marker asserts nothing about any node, so there is nothing for the
    support check to fail, and a sentence that carries no marker must not
    be turned into one.
    """
    markdown = (
        "The requisites of contracts are consent and object [1]. "
        "This sentence cites nothing at all. "
        "Succession passes to the heirs [2]."
    )
    claims = eval_scorer.split_citation_claims(markdown)
    assert [claim.marker for claim in claims] == [1, 2]
    assert "consent and object" in claims[0].sentence
    assert "This sentence cites nothing" not in " ".join(
        claim.sentence for claim in claims
    )


def test_the_runner_fails_an_answer_whose_citation_does_not_support_it(
    corpus: Any,
) -> None:
    """The RUNNER's check fails, not only the scorer's helper.

    The helper is where the rule lives, but the metric is computed on the
    runner's verdict, so the runner's verdict is what has to fail. The
    answer here cites a node that exists (so the old existence check
    passed) and says something the node does not support (so the support
    check fails).
    """
    from apps.api.evaluation.arms import AnswerResult
    from apps.api.evaluation.runner import Runner

    config = _config(_ALL_ON)
    runner = Runner(
        corpus["db_path"],
        corpus["slug"],
        config,
        index_root=Path(corpus["index_root"]),
        generator=None,
        judge=eval_judge.CannedJudge(),
    )

    answer = AnswerResult(
        answer_markdown=(
            "The penalty for late payment is a fine of one percent per day [1]."
        ),
        citations=[{"n": 1, "node_id": "node_synth-doc_article_1"}],
        evidence_ids=["node_synth-doc_article_1"],
        insufficient=False,
        iterations=1,
        latency_ms=1,
        retrieval_latency_ms=1,
        generation_latency_ms=0,
        tokens_in=0,
        tokens_out=0,
        tokens_source=None,
    )
    # The node EXISTS, so the existence half passes; the sentence it
    # carries is not supported by that node, so the answer fails.
    assert runner._citations_valid(answer, ["node_synth-doc_article_1"]) is False

    # And the supported version passes, so the check discriminates on the
    # SUPPORT and not on something else.
    supported = AnswerResult(
        answer_markdown=(
            "The requisites of contracts are consent, object, and certainty of the "
            "terms [1]."
        ),
        citations=[{"n": 1, "node_id": "node_synth-doc_article_1"}],
        evidence_ids=["node_synth-doc_article_1"],
        insufficient=False,
        iterations=1,
        latency_ms=1,
        retrieval_latency_ms=1,
        generation_latency_ms=0,
        tokens_in=0,
        tokens_out=0,
        tokens_source=None,
    )
    assert runner._citations_valid(supported, ["node_synth-doc_article_1"]) is True


def test_a_dangling_citation_still_fails(corpus: Any) -> None:
    """A citation to a node the store does not have fails the answer.

    The existence half of the check is not dropped by the support check:
    both halves of the spec's definition have to hold.
    """
    from apps.api.evaluation.arms import AnswerResult
    from apps.api.evaluation.runner import Runner

    config = _config(_ALL_ON)
    runner = Runner(
        corpus["db_path"],
        corpus["slug"],
        config,
        index_root=Path(corpus["index_root"]),
        generator=None,
        judge=eval_judge.CannedJudge(),
    )
    answer = AnswerResult(
        answer_markdown="The requisites of contracts are consent and object [1].",
        citations=[{"n": 1, "node_id": "node_does_not_exist"}],
        evidence_ids=[],
        insufficient=False,
        iterations=1,
        latency_ms=1,
        retrieval_latency_ms=1,
        generation_latency_ms=0,
        tokens_in=0,
        tokens_out=0,
        tokens_source=None,
    )
    assert runner._citations_valid(answer, ["node_does_not_exist"]) is False
