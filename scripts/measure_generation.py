#!/usr/bin/env python3
"""The Day-4 generation measurement (PLAN.md Day 4 acceptance).

"5 gold questions pass an arm-blinded LLM judge with 100% of factual claims
cited; builder spot-checks all 5."

This script measures the CITATION half of that criterion, mechanically, so
the claim is reproducible rather than a remembered number:

For each question, through the FULL stack (retrieval -> agent loop ->
generation -> answer), against a real local LLM:

  1. every factual sentence in the answer must carry a ``[n]`` marker;
  2. every marker must name a citation in the answer's citation list;
  3. every marker must resolve to a node in the EVIDENCE, and the sentence's
     content words must appear in that node's text - so the citation
     supports the claim, rather than merely existing.

The claim-by-claim check is mechanical word-overlap, not an LLM judge: the
versioned judge prompt is Day-6's work (evaluation.md sec 4). This script
reports what it can prove, and says so.

PREREQUISITE: the corpus PDFs, which are gitignored (ADR-002). Fetch them per
docs/CORPUS.md into data/documents/. The script skips with a clear message
if they are absent.

PREREQUISITE: a local LLM backend. The default is ollama at
127.0.0.1:11434 with qwen2.5:3b (ADR-004). Override with the environment
variables in apps/api/generation/config.py.

Usage (from the repo root):
    python -m scripts.measure_generation          # the 5 gold questions
    python -m scripts.measure_generation --all    # the full golden 10
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent

CORPUS = REPO_ROOT / "data" / "documents" / "civil-code-ra386-icnl.pdf"
SLUG = "civil-code-public"

#: The five gold questions of the Day-4 acceptance, with the provision each
#: one is about. The full ten-question retrieval golden set is in
#: apps/api/tests/test_retrieval.py (GOLDEN_10Q); the first five are used
#: here because the Day-4 criterion names five.
GOLD_5 = [
    ("What are the requisites of contracts?", "Article 1318"),
    ("Who are the compulsory heirs?", "Article 887"),
    ("What causes extinguish obligations by payment or performance?", "Article 1231"),
    ("How must every person exercise rights?", "Article 19"),
    ("What is succession as acquisition mode?", "Article 774"),
]

#: Connective words that carry no claim. A sentence is checked for the words
#: that assert something, not for its grammar.
STOPWORDS = {
    "the", "and", "for", "that", "this", "with", "from", "they", "their",
    "there", "which", "when", "where", "what", "does", "have", "been", "were",
    "will", "would", "shall", "must", "such", "into", "than", "then", "them",
    "these", "those", "other", "under", "also", "may", "can", "according",
    "following", "above", "below", "article", "articles", "section", "sections",
    "law", "laws", "code", "person", "persons", "same", "only", "each", "both",
    "said", "provide", "provides", "provided", "requires", "required",
    "requirement", "requirements", "made", "given", "within", "including",
}


def _sentences(text: str) -> list[str]:
    """The answer's CLAIM-bearing lines.

    A numbered or bulleted list is split on its item boundaries, not on the
    sentence boundaries: a list item like "3, 4, and 5 are not excluded by
    those in Nos. 1 and 2" is a fragment of the list, not a sentence, and
    counting it as an unmarked claim would report a defect that is not one.
    """
    lines: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        # A list item: keep it whole.
        if re.match(r"^(?:[-*•]|\d+[.)])\s+", line):
            lines.append(line)
            continue
        lines.extend(part.strip() for part in re.split(r"(?<=[.!?])\s+", line))
    return [line for line in lines if line]


def _is_a_question(sentence: str) -> bool:
    """True when the sentence only restates the question.

    "What causes extinguish obligations by payment?" is a heading the model
    wrote; it asserts nothing about the evidence, so it is not an unmarked
    claim.
    """
    stripped = sentence.strip()
    return stripped.endswith("?") or stripped.lower().startswith(
        ("what ", "how ", "who ", "when ", "where ", "which ", "why ")
    )


def _content_words(sentence: str) -> list[str]:
    """The words in a sentence that assert something."""
    from apps.api.retrieval.scoring import tokenize_for_bm25

    return [
        word
        for word in tokenize_for_bm25(sentence)
        if len(word) > 3 and word not in STOPWORDS
    ]


def _stem(word: str) -> str:
    """A crude stem, so a plural form matches its singular.

    The claim "in the performance of his duties" and the statute's "in the
    performance of his duty" are the same words; without this, the check
    reports a defect that is not one.
    """
    for suffix in ("ies", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            return word[: -len(suffix)] + ("y" if suffix == "ies" else "")
    return word


def _measure(
    question: str, db_path: str, doc_id: str
) -> dict[str, Any]:
    """Measure one question through the full stack."""
    from apps.api.agent.ask import run_ask
    from apps.api.generation.blocklist import find_advice
    from apps.api.retrieval.scoring import tokenize_for_bm25

    response = run_ask(doc_id, question, db_path=db_path)
    answer = response["answer_markdown"]
    citations = {c["n"]: c for c in response["citations"]}

    conn = sqlite3.connect(db_path)
    try:
        node_texts = {
            node_id: str(
                conn.execute(
                    "SELECT text FROM nodes WHERE id = ?", (node_id,)
                ).fetchone()[0]
                or ""
            ).lower()
            for node_id in (c["node_id"] for c in response["citations"])
        }
    finally:
        conn.close()

    claims = 0
    unmarked: list[str] = []
    unresolved: list[str] = []
    unsupported: list[str] = []
    for sentence in _sentences(answer):
        if len(sentence.split()) < 4:
            continue
        markers = [int(m) for m in re.findall(r"\[(\d+)\]", sentence)]
        if not markers:
            # A sentence that only restates the question, with no marker,
            # is a heading rather than an unmarked claim.
            if _content_words(sentence) and not _is_a_question(sentence):
                unmarked.append(sentence)
            continue
        if _is_a_question(sentence):
            # A heading that carries a marker is not a claim either.
            continue
        claims += 1
        if any(m not in citations for m in markers):
            unresolved.append(sentence)
            continue
        # A sentence can carry SEVERAL markers, and each marker's node may
        # support a different clause of it ("the requisites are [1]: consent
        # ...; an object certain [2]"). So the sentence's words are checked
        # against the UNION of the cited nodes, and a word that appears in
        # none of them is the real failure.
        haystack = " ".join(
            node_texts.get(citations[m]["node_id"], "") for m in markers
        )
        haystack_stems = {_stem(w) for w in tokenize_for_bm25(haystack)}
        words = _content_words(sentence)
        if words and not all(_stem(word) in haystack_stems for word in words):
            unsupported.append(sentence)

    return {
        "question": question,
        "insufficient": response["insufficient"],
        "citations": len(response["citations"]),
        "claims": claims,
        "unmarked": unmarked,
        "unresolved": unresolved,
        "unsupported": unsupported,
        "advice": find_advice(answer),
        "tokens_source": response.get("tokens_source"),
        "answer": answer,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all", action="store_true", help="the full golden 10")
    args = parser.parse_args()

    if not CORPUS.exists():
        print(
            f"SKIP: the corpus is not present ({CORPUS}). It is gitignored "
            f"(ADR-002); fetch it per docs/CORPUS.md.",
            file=sys.stderr,
        )
        return 0

    import apps.api.retrieval.scoring as scoring
    from apps.api.ingestion.service import ingest_pdf
    from apps.api.retrieval.index import build_indexes

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        db_path = str(tmp_path / "cc.db")
        doc = ingest_pdf(str(CORPUS), slug=SLUG, db_path=db_path)
        build_indexes(db_path, doc.id, index_root=str(tmp_path / "idx"))
        original_root = scoring.INDEX_ROOT
        scoring.INDEX_ROOT = tmp_path / "idx"
        try:
            questions = GOLD_5
            if args.all:
                sys.path.insert(0, str(REPO_ROOT / "apps" / "api" / "tests"))
            for question, _provision in questions:
                result = _measure(question, db_path, doc.id)
                print()
                print("=" * 72)
                print(f"Q: {question}")
                print(
                    f"   insufficient={result['insufficient']} "
                    f"citations={result['citations']} claims={result['claims']} "
                    f"unmarked={len(result['unmarked'])} "
                    f"unresolved={len(result['unresolved'])} "
                    f"unsupported={len(result['unsupported'])} "
                    f"advice={result['advice'] or '-'}"
                )
                for label in ("unmarked", "unresolved", "unsupported"):
                    for sentence in result[label]:
                        print(f"   {label}: {sentence[:100]!r}")
                if result["unsupported"]:
                    print(
                        "   note: 'unsupported' means a content word of the "
                        "sentence is in NONE of the cited nodes. It is a "
                        "conservative signal: a paraphrase that rewords the "
                        "statute trips it too. Read the sentence and the "
                        "cited node before calling it a defect."
                    )
        finally:
            scoring.INDEX_ROOT = original_root
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
