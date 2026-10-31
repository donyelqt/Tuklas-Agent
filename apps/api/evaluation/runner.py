"""The eval runner: both arms, one snapshot, one config, one record.

evaluation.md §1: "same dataset, same questions, same golds, same
generation model, same judges -> both arms -> reported metrics. Anything
else is a demo, not an experiment."

So the runner takes ONE dataset snapshot and ONE config, runs BOTH arms
over the SAME questions, judges both arms' answers with the SAME blinded
judge, and writes the result in one transaction.

The runner is deliberately sequential and deterministic. The arms are run
question by question, in the dataset's order, one arm after the other, so
that:

* a question's two arms see the same corpus, the same model and the same
  seed, with nothing running concurrently to perturb the other's timings;
* the run is reproducible: the same config over the same snapshot produces
  the same retrieval metrics EXACTLY (evaluation.md §6), because the
  retrieval is a pure function of (corpus, query) and the order is fixed;
* the recorded latencies mean something. A parallel run would measure the
  contention of the run on the machine, which is not a property of either
  arm.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from apps.api.evaluation import spot_check, verification
from apps.api.evaluation.arms import (
    ARM_NAMES,
    ARM_TUKLAS,
    ARM_VECTOR,
    RETRIEVAL_INSTRUMENT_ANSWER_NODE_UNION,
    AnswerResult,
    Arm,
    RetrievalResult,
    TuklasArm,
    VectorArm,
)
from apps.api.evaluation.config import RunConfig
from apps.api.evaluation.dataset import Dataset, Question
from apps.api.evaluation.judge import (
    BatchedLLMJudge,
    Judge,
    judge_answer,
    spot_check_disagreement,
    spot_check_sample,
    verdicts_to_json,
)
from apps.api.evaluation.metrics import (
    GenerationRow,
    RetrievalRow,
    SystemRow,
    generation_aggregates,
    retrieval_aggregates,
    system_aggregates,
)
from apps.api.evaluation.scorer import (
    citation_supports,
    matched_claims,
    split_citation_claims,
)
from apps.api.evaluation.store import (
    EvaluationRun,
    RetrievalRun,
    new_evaluation_id,
    write_evaluation,
    write_evaluation_rows,
)

_LOG = logging.getLogger(__name__)

#: How many candidates a question's retrieval returns. The metric K values
#: go up to 10, so the window has to be at least that wide.
CANDIDATE_WINDOW = 20


@dataclass
class ArmResult:
    """What one arm produced for one question."""

    question: Question
    arm: str
    retrieval: RetrievalResult
    answer: AnswerResult
    claims_matched: int
    claims_total: int
    faithfulness: float | None
    judged_claims: int
    verdicts_json: str
    citations_valid: bool
    evidence_text: dict[str, str] = field(default_factory=dict)
    verification_record: dict[str, Any] = field(default_factory=dict)


class Runner:
    """Runs one evaluation: both arms over one snapshot, and records it."""

    def __init__(
        self,
        db_path: str,
        document_id: str,
        config: RunConfig,
        *,
        index_root: str | Path | None = None,
        generator: Any | None = None,
        judge: Judge | None = None,
    ) -> None:
        self.db_path = db_path
        self.document_id = document_id
        self.config = config
        self.index_root = Path(index_root) if index_root is not None else None
        self._generator = generator
        self._judge = judge

    # -- the collaborators -------------------------------------------------

    def generator(self) -> Any:
        """The generator every arm uses.

        ONE generator, shared: the arms must differ only in retrieval, and
        two generator instances would be two samplers even when they carry
        the same parameters.
        """
        if self._generator is None:
            from apps.api.generation.config import generator_from_env

            self._generator = generator_from_env()
        return self._generator

    def judge(self) -> Judge:
        """The judge both arms are scored with.

        The judge is BLINDED: it receives the claim and the evidence and
        nothing else, and the runner does not pass the arm to it. The
        judge prompt's version travels with the run.

        The judge scores an answer's claims in ONE model call rather than
        one call per claim. That is the difference between a run that
        finishes and a run that does not: an answer of 5 claims costs 5
        calls single-claim, a 72-question split over two arms costs about
        700, and on a local CPU model that is hours of wall clock. The
        verdicts are per claim either way, and a claim the model does not
        answer for scores 0 - the same as a parse failure.
        """
        if self._judge is None:
            from apps.api.generation.interface import LLMInterface

            self._judge = BatchedLLMJudge(
                LLMInterface(
                    backend=self.config.backend,
                    model=self.config.model,
                    temperature=self.config.temperature,
                    seed=self.config.seed,
                )
            )
        return self._judge

    def _judge_prompt_version(self) -> str:
        """The version of the prompt the judge ACTUALLY used.

        The run records the judge's own version, not the config's, because
        the config's value is what the run was ASKED to use and the
        judge's is what it USED. Recording the config's version while the
        judge runs a different prompt would make two scores look
        comparable when they came from two instruments.

        A config that names a version the judge does not have is a
        CONTRADICTION, and it is a loud failure: the run's own record
        would otherwise misdescribe the numbers it holds.
        """
        judge_version = str(getattr(self.judge(), "prompt_version", "") or "")
        configured = self.config.judge_prompt_version
        if judge_version and judge_version != configured:
            raise ValueError(
                f"the run config names judge_prompt_version {configured!r} but the "
                f"judge scores with {judge_version!r}. The two are different "
                f"prompts, so their scores are not comparable, and a run that "
                f"recorded one while using the other would misdescribe its own "
                f"numbers. Name the judge's version in the config."
            )
        return judge_version or configured

    def build_arms(self) -> dict[str, Arm]:
        """The two arms, in the order the config names them.

        The run config's WEIGHTS go to the vectorless arm, because the
        weights are retrieval.md §3's five-component weight vector and the
        dense arm has no such components. The vectorless arm must be scored
        with the weights the run pinned, or the run's own config id would
        name a weighting that was never applied to anything.
        """
        generator = self.generator()
        tuklas = TuklasArm(
            self.db_path,
            self.document_id,
            generator=generator,
            index_root=self.index_root,
            weights=self.config.weights,
        )
        vector = VectorArm(
            self.db_path,
            self.document_id,
            generator=generator,
        )
        return {ARM_TUKLAS: tuklas, ARM_VECTOR: vector}

    # -- one question, one arm --------------------------------------------

    def run_arm_question(self, arm: Arm, question: Question) -> ArmResult:
        """One arm's answer for one question, with its scores."""
        retrieval = arm.retrieve(question.question)
        answer = arm.answer(question.question)

        claims = question.expected_claims
        claims_matched = len(
            matched_claims(claims, answer.answer_markdown, threshold=self.config.fuzzy_threshold)
        )
        claims_total = len(claims)

        citation_node_ids = [
            str(citation.get("node_id"))
            for citation in answer.citations
            if isinstance(citation, dict) and citation.get("node_id")
        ]
        evidence_text = self._evidence_text(citation_node_ids)
        faithfulness, judged, verdicts = judge_answer(
            self.judge(),
            answer.answer_markdown,
            evidence_text,
            citation_node_ids,
        )
        citations_valid = self._citations_valid(answer, citation_node_ids)
        # The v2 verification layer checks the answer's claims against the
        # cited nodes. It is arm-blind by construction, so it is run here
        # rather than in the judge, and its record is carried on the result
        # so the store and the report can surface it. The layer reads each
        # cited node's HEADING (for the article check), not only its body,
        # so the evidence is the node-shaped dict the store yields.
        verification_record = verification.verify_answer(
            answerable=question.answerable,
            insufficient=answer.insufficient,
            answer_markdown=answer.answer_markdown,
            citations=answer.citations,
            evidence=self._evidence_nodes(citation_node_ids),
            gold_nodes=question.gold_nodes,
        ).to_record()
        return ArmResult(
            question=question,
            arm=arm.name,
            retrieval=retrieval,
            answer=answer,
            claims_matched=claims_matched,
            claims_total=claims_total,
            faithfulness=faithfulness,
            judged_claims=judged,
            verdicts_json=verdicts_to_json(verdicts),
            citations_valid=citations_valid,
            evidence_text=evidence_text,
            verification_record=verification_record,
        )

    def _evidence_text(self, node_ids: list[str]) -> dict[str, str]:
        """The text of the cited nodes, for the judge.

        The judge's evidence is the node text, and it is read from the
        store rather than from the answer: the answer carries the node
        ids, and the text behind an id is the store's.
        """
        if not node_ids:
            return {}
        from apps.api.ingestion import db as ingestion_db

        placeholders = ",".join("?" * len(node_ids))
        with ingestion_db.connect(self.db_path) as conn:
            rows = conn.execute(
                f"SELECT id, text FROM nodes WHERE id IN ({placeholders})",
                tuple(node_ids),
            ).fetchall()
        return {str(row["id"]): str(row["text"]) for row in rows}

    def _evidence_nodes(self, node_ids: list[str]) -> dict[str, verification.EvidenceNode]:
        """The cited nodes as the v2 verification layer reads them.

        The layer's article check reads each cited node's OWN HEADING, so
        the evidence it gets is the node-shaped record (id, title, text)
        rather than the text-only dict the judge gets. The two are read
        from the same store query shape; only the projection differs.
        """
        if not node_ids:
            return {}
        from apps.api.ingestion import db as ingestion_db

        placeholders = ",".join("?" * len(node_ids))
        with ingestion_db.connect(self.db_path) as conn:
            rows = conn.execute(
                f"SELECT id, title, text FROM nodes WHERE id IN ({placeholders})",
                tuple(node_ids),
            ).fetchall()
        return {
            str(row["id"]): verification.EvidenceNode(
                node_id=str(row["id"]),
                title=str(row["title"]),
                text=str(row["text"]),
            )
            for row in rows
        }

    def _citations_valid(self, answer: AnswerResult, citation_node_ids: list[str]) -> bool:
        """Whether the answer's citations are valid (evaluation.md §4).

        "Cited ids exist + support the attached claim; dangling id = failed
        answer." Three checks, all of them MECHANICAL:

        * the ids EXIST. A citation to a node the store does not have is a
          dangling id, and the answer fails.
        * the answer is a REFUSAL. An abstention carries no citations by
          contract (api.md §3), so it is not a failed citation list - it
          is the correct shape for a refusal.
        * the cited nodes SUPPORT the sentences that cite them. The answer
          is split into (marker, sentence) pairs, and each cited node's
          text is checked against the sentence its marker attaches it to,
          with the shared scorer's rule and the run's own fuzzy threshold.

        The support check is the half that was missing. Checking only that
        the ids exist cannot fail: the citation assembler builds the list
        from the kept evidence set, and the kept set is read from the
        nodes table, so the ids are in the table by construction. A check
        that cannot fail measures nothing, and a metric that reported 1.000
        for every row was reporting the shape of the answer rather than
        its accuracy.

        The support check is MECHANICAL and arm-blind on purpose: it reads
        a sentence and a node's text, and it cannot tell which arm
        produced them. The LLM judge is unchanged, and it still measures
        the OTHER half of the same definition - a model reading the
        evidence can recognise a paraphrase this string rule cannot.
        """
        if answer.insufficient:
            return not citation_node_ids
        if not citation_node_ids:
            return False
        from apps.api.ingestion import db as ingestion_db

        placeholders = ",".join("?" * len(citation_node_ids))
        with ingestion_db.connect(self.db_path) as conn:
            rows = conn.execute(
                f"SELECT id, text FROM nodes WHERE id IN ({placeholders})",
                tuple(citation_node_ids),
            ).fetchall()
        texts = {str(row["id"]): str(row["text"]) for row in rows}
        if len(texts) != len(set(citation_node_ids)):
            # A dangling id: the answer cites a node the store does not have.
            return False
        return self._citations_support_their_sentences(answer, texts)

    def _citations_support_their_sentences(
        self, answer: AnswerResult, node_texts: dict[str, str]
    ) -> bool:
        """Whether every citation in the answer supports the sentence it marks.

        The marker in the answer is the citation NUMBER the prompt showed
        the model (``generation/prompts.py`` numbers the rendered evidence
        from 1), and the citation list carries that number as its ``n``
        field. The mapping is therefore by NUMBER, not by list position: a
        citation whose number names no entry is a dangling marker and fails
        the answer.

        An answer whose sentences carry NO marker makes no citation claim,
        and there is nothing for the support check to fail. Such an answer
        still had to pass the existence check above, so it is not a free
        pass: it is an answer that cites nothing and claims nothing, and
        the abstention metric is what scores it.
        """
        by_number: dict[int, str] = {}
        for citation in answer.citations:
            if not isinstance(citation, dict):
                continue
            node_id = citation.get("node_id")
            if not node_id:
                continue
            number = citation.get("n")
            if not isinstance(number, int) or isinstance(number, bool):
                continue
            by_number[number] = str(node_id)

        claims = split_citation_claims(answer.answer_markdown)
        if not claims:
            return True

        threshold = self.config.fuzzy_threshold
        for claim in claims:
            node_id = by_number.get(claim.marker)
            if node_id is None:
                # The answer cites a number the citation list does not have.
                return False
            text = node_texts.get(node_id, "")
            if not citation_supports(claim.sentence, text, threshold=threshold):
                return False
        return True

    # -- the whole run -----------------------------------------------------

    def _applied_weights(self, result: ArmResult) -> dict[str, float]:
        """The weight snapshot that was APPLIED to this arm's retrieval.

        The row must record what was applied, not what was asked for. A
        row that records the config's weights while the arm scored with
        different ones is a row that misdescribes its own numbers, and
        evaluation.md §6's "weights snapshot" requirement is only true if
        the snapshot is the one that produced the ranking.

        The arm reports the snapshot it used, and the config's is the
        fallback for an arm that has no five-component weight vector of
        its own (the dense arm's ranking is a cosine over embeddings, and
        retrieval.md §3's weight vector does not apply to it).
        """
        applied = getattr(result.retrieval, "weights", None) or getattr(
            result.answer, "weights", None
        )
        if applied:
            return {str(key): float(value) for key, value in applied.items()}
        return self.config.weights

    def _rows_for(
        self,
        run_id: str,
        name: str,
        result: ArmResult,
    ) -> tuple[RetrievalRow, GenerationRow, SystemRow, RetrievalRun]:
        """One arm's answer for one question, as the four row shapes."""
        citation_node_ids = [
            str(citation.get("node_id"))
            for citation in result.answer.citations
            if isinstance(citation, dict) and citation.get("node_id")
        ]
        retrieval_row = RetrievalRow(
            question_id=result.question.id,
            arm=name,
            category=result.question.category,
            answerable=result.question.answerable,
            retrieved_node_ids=result.retrieval.node_ids,
            gold_node_ids=result.question.gold_nodes,
        )
        generation_row = GenerationRow(
            question_id=result.question.id,
            arm=name,
            category=result.question.category,
            answerable=result.question.answerable,
            answer_markdown=result.answer.answer_markdown,
            claims_matched=result.claims_matched,
            claims_total=result.claims_total,
            faithfulness=result.faithfulness,
            citations_valid=result.citations_valid,
            insufficient=result.answer.insufficient,
            citation_node_ids=citation_node_ids,
            judged_claims=result.judged_claims,
        )
        system_row = SystemRow(
            question_id=result.question.id,
            arm=name,
            retrieval_latency_ms=result.answer.retrieval_latency_ms,
            generation_latency_ms=result.answer.generation_latency_ms,
            total_latency_ms=result.answer.latency_ms,
            tokens_in=result.answer.tokens_in,
            tokens_out=result.answer.tokens_out,
            tool_iterations=result.answer.iterations,
        )
        store_row = RetrievalRun(
            evaluation_id=run_id,
            question_id=result.question.id,
            arm=name,
            category=result.question.category,
            answerable=result.question.answerable,
            retrieved_node_ids=result.retrieval.node_ids,
            gold_node_ids=result.question.gold_nodes,
            weights=self._applied_weights(result),
            normalization=str(self.config.config["normalization"]),
            latency_ms=result.retrieval.latency_ms,
            rank_first_hit=_rank_first_hit(
                result.retrieval.node_ids, result.question.gold_nodes
            ),
            gold_hit=_gold_hit(
                result.retrieval.node_ids, result.question.gold_nodes
            ),
            generation_latency_ms=result.answer.generation_latency_ms,
            total_latency_ms=result.answer.latency_ms,
            tokens_in=result.answer.tokens_in,
            tokens_out=result.answer.tokens_out,
            tokens_source=result.answer.tokens_source,
            tool_iterations=result.answer.iterations,
            insufficient=result.answer.insufficient,
            citations_valid=result.citations_valid,
            citation_node_ids=citation_node_ids,
            claims_matched=result.claims_matched,
            claims_total=result.claims_total,
            faithfulness=result.faithfulness,
            judge_verdicts=result.verdicts_json,
            answer_markdown=result.answer.answer_markdown,
            judge_prompt_version=self._judge_prompt_version(),
            verification_record=result.verification_record,
        )
        return retrieval_row, generation_row, system_row, store_row

    def _observed_lex_paths(self, results: dict[str, list[ArmResult]]) -> list[str]:
        """The lex paths the run's retrieval calls ACTUALLY took, deduped.

        Each entry is the rendered form the retrieval layer itself
        reported for one ``search_nodes`` call, carried on the arm's
        result. The runner does NOT re-derive the choice (a re-stat of
        ``bm25.json`` could disagree with what ran); it deduplicates the
        layer's own reports into the distinct set the run row records.

        An EMPTY list means no search reported a lex path — which for a
        run recorded before this field existed is the only honest value —
        and the store records that as not-recorded, never as a guess.
        """
        paths: list[str] = []
        for arm_results in results.values():
            for result in arm_results:
                for path in result.retrieval.lex_paths:
                    if path not in paths:
                        paths.append(path)
        return paths

    def run(
        self,
        dataset: Dataset,
        *,
        evaluation_id: str | None = None,
        spot_check_rescores: dict[str, list[int]] | None = None,
    ) -> dict[str, Any]:
        """Run both arms over ``dataset`` and record the result.

        Returns the report payload: the aggregates, the per-arm results
        and the run's own record.

        The per-question rows are WRITTEN AS THEY ARE PRODUCED, not at the
        end. A 72-question split over two arms takes long enough that an
        interrupted run would otherwise leave nothing at all in the store,
        because the run row and its metrics are written last. Writing
        incrementally means an interrupted run keeps the rows it did
        produce, and they are joinable on the same
        ``(evaluation_id, question_id, arm)`` key either way.
        """
        run_id = evaluation_id or new_evaluation_id()
        arms = self.build_arms()

        results: dict[str, list[ArmResult]] = {name: [] for name in arms}
        retrieval_rows: list[RetrievalRow] = []
        generation_rows: list[GenerationRow] = []
        system_rows: list[SystemRow] = []
        store_rows: list[RetrievalRun] = []
        for question in dataset.questions:
            for name, arm in arms.items():
                started = time.perf_counter()
                result = self.run_arm_question(arm, question)
                _LOG.info(
                    "question %s arm %s: %d ms (total %d ms)",
                    question.id,
                    name,
                    int((time.perf_counter() - started) * 1000),
                    result.answer.latency_ms,
                )
                results[name].append(result)
                retrieval_row, generation_row, system_row, store_row = self._rows_for(
                    run_id, name, result
                )
                retrieval_rows.append(retrieval_row)
                generation_rows.append(generation_row)
                system_rows.append(system_row)
                store_rows.append(store_row)
            # One write per QUESTION, not one per row: a per-row write
            # would cost a transaction per question, and a per-question
            # write still means an interrupted run keeps every question it
            # finished.
            write_evaluation_rows(self.db_path, store_rows[-len(arms) :])

        aggregates = self._aggregates(results, retrieval_rows, generation_rows, system_rows)
        aggregates["spot_check"] = self._spot_check(dataset, results, spot_check_rescores)
        if not aggregates["spot_check"].get("performed"):
            # No builder re-scores were supplied, so the check is performed
            # MECHANICALLY: the same versioned prompt, re-sent to the same
            # backend over the same evidence. That measures the judge's
            # SELF-CONSISTENCY, not a human's agreement with it, and the
            # result names the method so a reader knows which it is.
            mechanical = self._mechanical_spot_check(results)
            if mechanical is not None:
                aggregates["spot_check"] = mechanical

        run = EvaluationRun(
            id=run_id,
            dataset=dataset.name,
            dataset_snapshot=dataset.snapshot_id,
            config=self.config.to_dict(),
            config_id=self.config.config_id,
            code_version=self.config.code_version,
            model=self.config.model,
            backend=self.config.backend,
            temperature=self.config.temperature,
            seed=self.config.seed,
            judge_prompt_version=self._judge_prompt_version(),
            split_seed=dataset.seed,
            arms=list(arms),
            k=list(self.config.k),
            status="done",
            metrics=aggregates,
            retrieval_instrument=RETRIEVAL_INSTRUMENT_ANSWER_NODE_UNION,
            lex_paths=self._observed_lex_paths(results),
        )
        # The per-question rows were ALREADY written, one question at a
        # time, so this writes the run row and its metrics only. Writing
        # the rows again here would duplicate them.
        write_evaluation(self.db_path, run, [])
        return {
            "evaluation_id": run_id,
            "status": "done",
            "config": self.config.to_dict(),
            "dataset": dataset.to_dict(),
            "aggregates": aggregates,
            "results": results,
        }

    def _aggregates(
        self,
        results: dict[str, list[ArmResult]],
        retrieval_rows: list[RetrievalRow],
        generation_rows: list[GenerationRow],
        system_rows: list[SystemRow],
    ) -> dict[str, Any]:
        """The aggregates, PER ARM.

        The arms are aggregated SEPARATELY and never compared in a
        single number. The outcome of the comparison is unknown until it
        is measured, and a runner that emitted a "winner" field would be
        making a claim the spec forbids.
        """
        per_arm: dict[str, Any] = {}
        for name in results:
            arm_retrieval = [row for row in retrieval_rows if row.arm == name]
            arm_generation = [row for row in generation_rows if row.arm == name]
            arm_system = [row for row in system_rows if row.arm == name]
            per_arm[name] = {
                "retrieval": retrieval_aggregates(arm_retrieval, k_values=self.config.k),
                "generation": generation_aggregates(arm_generation),
                "system": system_aggregates(arm_system),
                "verification": _verification_aggregates(results[name]),
            }
        return {
            "arms": per_arm,
            "n_questions": len(next(iter(results.values()))) if results else 0,
            "n_arms": len(results),
        }

    def _mechanical_spot_check(
        self, results: dict[str, list[ArmResult]]
    ) -> dict[str, Any] | None:
        """Re-judge a stratified sample and measure the disagreement.

        The re-judging uses the SAME versioned prompt, the SAME backend
        and the SAME evidence the original used, so what the number
        measures is the judge's self-consistency. The result says so.

        A failure to re-judge is not a crash: the spot check is a
        measurement, and a measurement that could not be taken is
        reported as not taken.
        """
        rows: list[dict[str, Any]] = []
        evidence_by_row: dict[str, dict[str, str]] = {}
        for name, arm_results in results.items():
            for result in arm_results:
                key = f"{result.question.id}:{name}"
                rows.append(
                    {
                        "question_id": result.question.id,
                        "arm": name,
                        "answer_markdown": result.answer.answer_markdown,
                        "citation_node_ids": [
                            str(citation.get("node_id"))
                            for citation in result.answer.citations
                            if isinstance(citation, dict) and citation.get("node_id")
                        ],
                        "judge_verdicts": result.verdicts_json,
                    }
                )
                evidence_by_row[key] = result.evidence_text
        if not rows:
            return None
        try:
            outcome = spot_check.rescore_sample(
                self.judge(), rows, evidence_by_row=evidence_by_row
            )
        except Exception:
            _LOG.exception("the spot check could not be performed")
            return None
        payload = outcome.to_dict()
        payload["reason"] = (
            "the disagreement measured here is the judge's SELF-CONSISTENCY: the "
            "same versioned prompt was re-sent to the same backend over the same "
            "evidence. It is NOT the builder's human re-scoring that evaluation.md "
            "§4 asks for, and a builder re-score would be a different measurement."
        )
        return payload

    def _spot_check(
        self,
        dataset: Dataset,
        results: dict[str, list[ArmResult]],
        rescores: dict[str, list[int]] | None,
    ) -> dict[str, Any]:
        """The 20% spot check and its disagreement (evaluation.md §4).

        Two things can happen here, and the result says which.

        * A BUILDER's re-scores are supplied (``{"<question_id>:<arm>":
          [scores]}``). Then the disagreement is the builder's, which is
          what evaluation.md §4 asks for.
        * No builder re-scores are supplied. Then the check is performed
          MECHANICALLY: the same versioned prompt is re-sent to the same
          backend over the same evidence, and the disagreement measures the
          judge's SELF-CONSISTENCY. The result names the method, so a
          reader is never left guessing whether a number is a human
          judgement or a re-judging.

        When neither is possible the check is REPORTED as not performed,
        with the reason — an unmeasured disagreement is not 0%
        disagreement, and reporting it as 0% would be a fabricated metric.
        """
        sample = spot_check_sample([question.id for question in dataset.questions])
        if not rescores:
            return {
                "performed": False,
                "method": "none",
                "reason": "no builder re-scores were supplied for this run",
                "sample_size": len(sample),
                "sample": sample,
                "n_claims_compared": 0,
                "disagreement": None,
                "threshold": 0.15,
                "valid": None,
            }
        original: list[int] = []
        rejudged: list[int] = []
        for question_id in sample:
            for name in sorted(results):
                key = f"{question_id}:{name}"
                supplied = rescores.get(key)
                if supplied is None:
                    continue
                for result in results[name]:
                    if result.question.id != question_id:
                        continue
                    verdicts = json.loads(result.verdicts_json)
                    original.extend(int(verdict["score"]) for verdict in verdicts)
                    rejudged.extend(int(score) for score in supplied)
        if not original:
            return {
                "performed": False,
                "method": "none",
                "reason": "the builder's re-scores did not cover any sampled claim",
                "sample_size": len(sample),
                "sample": sample,
                "n_claims_compared": 0,
                "disagreement": None,
                "threshold": 0.15,
                "valid": None,
            }
        disagreement = spot_check_disagreement(original, rejudged)
        return {
            "performed": True,
            "method": "builder-rescore (human re-scoring)",
            "sample_size": len(sample),
            "sample": sample,
            "n_claims_compared": len(original),
            "disagreement": disagreement,
            "threshold": 0.15,
            "valid": disagreement <= 0.15,
        }


def _verification_aggregates(results: list[ArmResult]) -> dict[str, Any]:
    """The v2 verification aggregate, over the ANSWERABLE rows only.

    The verification layer runs on every answer, but the gate's pass rate
    is only meaningful over answerable rows: an unanswerable row passes
    as a refusal, so including it would dilute the rate with rows whose
    pass is a property of the question, not of the answer. The
    semantic_uncertainty rate is likewise over answerable rows, because
    an undecided check is a claim the checks could not decide and a
    refusal has no claim to be undecided about.
    """
    records = [
        result.verification_record
        for result in results
        if result.question.answerable and result.verification_record
    ]
    if not records:
        return {"n_answerable": 0, "n_verified": 0, "verified_rate": None}
    n = len(records)
    n_verified = sum(1 for record in records if record.get("answer_verified"))
    return {
        "n_answerable": n,
        "n_verified": n_verified,
        "verified_rate": n_verified / n,
        "verification_version": records[0].get("verification_version"),
        "answer_claim_checks": sum(
            int(record.get("answer_claim_checks", 0)) for record in records
        ),
        "answer_claims_supported": sum(
            int(record.get("answer_claims_supported", 0)) for record in records
        ),
        "answer_claims_unsupported": sum(
            int(record.get("answer_claims_unsupported", 0)) for record in records
        ),
        "answer_consistency_checks": sum(
            int(record.get("answer_consistency_checks", 0)) for record in records
        ),
        "answer_consistency_mismatches": sum(
            int(record.get("answer_consistency_mismatches", 0)) for record in records
        ),
        "answer_citations_unreferenced": sum(
            int(record.get("answer_citations_unreferenced", 0)) for record in records
        ),
        "n_semantic_uncertainty": sum(
            1 for record in records if record.get("answer_semantic_uncertainty")
        ),
    }


def _rank_first_hit(retrieved: list[str], gold: list[str]) -> int | None:
    """The 1-based rank of the first gold hit, or None."""
    gold_set = set(gold)
    for position, node_id in enumerate(retrieved, start=1):
        if node_id in gold_set:
            return position
    return None


def _gold_hit(retrieved: list[str], gold: list[str]) -> bool:
    """True when a gold node is in the top-5 (the primary K)."""
    gold_set = set(gold)
    return any(node_id in gold_set for node_id in retrieved[:5])


__all__ = [
    "ARM_NAMES",
    "CANDIDATE_WINDOW",
    "ArmResult",
    "Runner",
]
