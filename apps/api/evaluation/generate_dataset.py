"""The deterministic generator of the frozen 72-question split.

The split file is COMMITTED, and this generator is committed beside it, so
that the split is reproducible and auditable: anyone can re-run it and get
the same file, byte for byte.

    python -m apps.api.evaluation.generate_dataset

The generator is deterministic in three ways:

* the SPLIT is a seeded shuffle (seed 42, evaluation.md §1), over a
  question pool that is itself written out in full below — the pool is
  DATA, not a function of the corpus, so the split does not move when the
  corpus changes;
* the CORPUS CHECKS are measurements against a real ingest of
  ``data/documents/civil-code-ra386-icnl.pdf``. The gold ids are checked
  against the ingested node ids, and the unanswerable rows' content
  tokens are checked against the corpus vocabulary. A check that fails
  raises, and nothing is written;
* the FILES are written with a fixed key order and a trailing newline.

**The unanswerable rows, and what they measure.** The Day-6 measured
finding is that the abstention gate is a lexical one: the ``lex``
component is min-max normalized per query, so a SINGLE matching token
gives ``lex = 1.0``. A planted unanswerable question that shares
ordinary vocabulary with the corpus is therefore answered, with citations,
and the abstention metric then measures the DATASET rather than the
system. The rows below are built so that EVERY content token is absent
from the corpus, and the generator MEASURES that and refuses to write a
split that fails it.

The honesty that costs: a question whose vocabulary is absent from the
corpus is a WEAKER test than a question that is out-of-domain in
substance. Refusing "zeppelin airship" is easier than refusing a question
that shares every ordinary word with the corpus and asks about something
the corpus does not address. The dataset says so in its own notes, and
the run report repeats it.
"""

from __future__ import annotations

import json
import random
import re
import sqlite3
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

from apps.api.evaluation.dataset import (
    CATEGORIES,
    DEV_SIZE,
    MIN_PER_CATEGORY_DEV,
    MIN_PER_CATEGORY_TEST,
    MIN_UNANSWERABLE,
    SPLIT_SEED,
    TEST_SIZE,
)

#: The repository root, from this file.
REPO_ROOT = Path(__file__).resolve().parents[3]

#: The corpus the dataset is built against (docs/CORPUS.md, D2).
CORPUS_PDF = REPO_ROOT / "data" / "documents" / "civil-code-ra386-icnl.pdf"
CORPUS_SLUG = "civil-code-public"

#: The SECONDARY corpus (docs/CORPUS.md, D2). It is ingested into the
#: generator's temporary store so that the unanswerable rows are checked
#: against it too.
#:
#: The generator used to ingest ONLY the Civil Code, which made the
#: secondary-vocabulary check unreachable: the temporary store held one
#: document, so "every OTHER ingested document" was always the empty set
#: and the check never fired. The runner, which checks the WHOLE live
#: store, then refused the dataset it had just generated, on three rows:
#: "tell" (q031), "explain" (q046) and "enumerate" (q059) are absent from
#: the Civil Code and present in the U.S. Code Title 11.
#:
#: The check is not weakened to make the dataset pass. The rows are
#: rewritten against words neither corpus contains, and the generator
#: ingests BOTH corpora so that the check it performs is the same check
#: the runner performs.
SECONDARY_PDF = REPO_ROOT / "data" / "documents" / "uscode-2023-title11.pdf"
SECONDARY_SLUG = "usc-title-11"

#: The document id the Civil Code's ingest produces (D1: the id is
#: ``doc_{hash8}`` of the file's sha256, and the Civil Code's is
#: ``doc_cdb52f8d`` — docs/CORPUS.md records the sha256).
CORPUS_DOCUMENT_ID = "doc_cdb52f8d"

#: Where the frozen split lives.
OUTPUT_DIR = REPO_ROOT / "data" / "datasets" / "v1"

#: The tokenization the corpus-absence check uses. It matches the BM25
#: index tokenizer's INPUT and its suffix folding, so a word is absent
#: only when the corpus genuinely does not contain it in any form.
_TOKEN_RE = re.compile(r"[a-z0-9]+")

#: The suffix folding ``apps/api/retrieval/scoring.py`` applies at index
#: and query time. It is re-implemented here rather than imported, because
#: this generator must be runnable against a corpus and a vocabulary
#: WITHOUT the retrieval layer's store — and a second copy that drifts
#: would weaken the check. The two are asserted equal by the test suite.
_SUFFIXES = (("ies", 3, "y"), ("sses", 2, ""), ("shes", 2, ""), ("ches", 2, ""), ("xes", 2, ""), ("zes", 2, ""))


def fold(token: str) -> str:
    """The BM25 suffix folding: plural -> singular-ish."""
    if len(token) <= 3:
        return token
    for suffix, drop, replacement in _SUFFIXES:
        if token.endswith(suffix) and len(token) > 4:
            return token[:-drop] + replacement
    if token.endswith("s") and not token.endswith(("ss", "us", "is")):
        return token[:-1]
    return token


def tokens_of(text: str) -> set[str]:
    """The folded token set of a text."""
    return {fold(token) for token in _TOKEN_RE.findall(text.lower())}


# ---------------------------------------------------------------------------
# The question pool
# ---------------------------------------------------------------------------
#
# Every row is written out in full: the question, its category, its gold
# nodes (the D1 node ids, which carry the document slug), its expected
# claims, and a note that says WHY these golds.
#
# The golds are LEAF provisions wherever the question admits one
# (evaluation.md §2: "golds prefer leaf provisions"), and every gold is
# checked against a real ingest by the generator before anything is
# written.
#
# The CATEGORIES and what they exercise:
#
# * ``direct-lookup`` — the question names a provision. This is the
#   category where the arms differ MOST: the vectorless arm applies
#   identifier precedence (ADR-005) and the dense arm has no ident
#   component at all.
# * ``definition`` — the question asks what a term means.
# * ``multi-section`` — the answer spans several provisions.
# * ``cross-reference`` — the question names a provision that ANOTHER
#   provision cites, so answering it needs the cited text. The
#   ``references_`` table is EMPTY on this corpus (ingestion does not
#   parse reference edges), so this category exercises the IDENT channel
#   and the lexical co-occurrence, NOT the ``ref`` scoring component.
# * ``exception`` — the question asks about the carve-out.
# * ``multi-hop`` — the answer needs two provisions joined.
# * ``comparison`` — the question asks about two provisions together.
#
# The UNANSWERABLE rows carry no gold and no expected claim, and every
# content token of theirs is absent from the corpus. The words are chosen
# from a topic vocabulary that a statute of the Philippines does not
# contain: airships, observatories, telescopes, cryptography, and so on.

QUESTIONS: list[dict[str, Any]] = [
    # --- direct-lookup (8) -------------------------------------------------
    {
        "question": "What does Article 1318 say about the requisites of a contract?",
        "category": "direct-lookup",
        "gold_nodes": ["node_civil-code-public_article_1318"],
        "expected_claims": ["consent", "object", "cause"],
        "notes": "evaluation.md §2's own seed example. Distractors 1319/1320 are the "
        "consent and acceptance provisions; the gold is the requisites article.",
    },
    {
        "question": "What does Article 26 say about the dignity of persons?",
        "category": "direct-lookup",
        "gold_nodes": ["node_civil-code-public_article_26"],
        "expected_claims": ["dignity", "personality", "privacy", "peace of mind"],
        "notes": "Carried over from the golden 10Q. The article lists the acts that "
        "produce a cause of action for damages.",
    },
    {
        "question": "What does Article 3 provide about ignorance of the law?",
        "category": "direct-lookup",
        "gold_nodes": ["node_civil-code-public_article_3"],
        "expected_claims": ["ignorance", "excuses", "compliance"],
        "notes": "Carried over from the golden 10Q. A short provision (under the "
        "50-character evidence floor) and the identified node's exemption from it "
        "is what makes this row interesting.",
    },
    {
        "question": "What does Article 1231 provide about the causes that extinguish obligations?",
        "category": "direct-lookup",
        "gold_nodes": ["node_civil-code-public_article_1231"],
        "expected_claims": ["payment", "performance", "loss of the thing due", "condonation", "compensation", "novation"],
        "notes": "Carried over from the golden 10Q, reworded to name the article. "
        "The gold is the enumerated list, not one of the items.",
    },
    {
        "question": "What does Article 887 say about the compulsory heirs?",
        "category": "direct-lookup",
        "gold_nodes": ["node_civil-code-public_article_887"],
        "expected_claims": ["legitimate children and descendants", "legitimate parents and ascendants", "widow or widower"],
        "notes": "Carried over from the golden 10Q. The enumeration order matters: "
        "the first three classes of compulsory heirs.",
    },
    {
        "question": "What does Article 774 define as succession?",
        "category": "direct-lookup",
        "gold_nodes": ["node_civil-code-public_article_774"],
        "expected_claims": ["mode of acquisition", "transmitted", "death", "by his will or by operation of law"],
        "notes": "Carried over from the golden 10Q.",
    },
    {
        "question": "What does Article 19 require of every person in the exercise of rights?",
        "category": "direct-lookup",
        "gold_nodes": ["node_civil-code-public_article_19"],
        "expected_claims": ["act with justice", "give everyone his due", "honesty", "good faith"],
        "notes": "Carried over from the golden 10Q.",
    },
    {
        "question": "What does Article 361 provide about juvenile courts?",
        "category": "direct-lookup",
        "gold_nodes": ["node_civil-code-public_article_361"],
        "expected_claims": ["juvenile courts", "chartered city", "large municipality"],
        "notes": "Carried over from the golden 10Q. The shortest gold in the "
        "dataset, which makes it a good test of the evidence floor.",
    },
    # --- definition (8) ----------------------------------------------------
    {
        "question": "What is juridical capacity under the Code?",
        "category": "definition",
        "gold_nodes": ["node_civil-code-public_article_37"],
        "expected_claims": ["fitness to be the subject of legal relations", "inherent in every natural person", "lost only through death"],
        "notes": "The definition article. The second sentence defines capacity to "
        "act, which the answer may mention but the gold is the definition.",
    },
    {
        "question": "What is a donation by reason of marriage?",
        "category": "definition",
        "gold_nodes": ["node_civil-code-public_article_126"],
        "expected_claims": ["made before its celebration", "in consideration of the marriage", "in favor of one or both of the future spouses"],
        "notes": "A definitional article with three elements, each of which is a "
        "separate element of the definition.",
    },
    {
        "question": "What is a substitution in the law of succession?",
        "category": "definition",
        "gold_nodes": ["node_civil-code-public_article_857"],
        "expected_claims": ["appointment of another heir", "in default of the heir originally instituted"],
        "notes": "The definition article for substitution. Articles 860-868 are its "
        "distractors.",
    },
    {
        "question": "What does the Code call the part of the testator's property that "
        "cannot be disposed of in favour of the compulsory heirs?",
        "category": "definition",
        "gold_nodes": ["node_civil-code-public_article_886"],
        "expected_claims": ["legitime", "reserved by the law", "compulsory heirs"],
        "notes": "A paraphrase question: the question never says 'legitime', so "
        "this row tests whether the answer can be located without the term.",
    },
    {
        "question": "What is an aleatory contract?",
        "category": "definition",
        "gold_nodes": ["node_civil-code-public_article_2010"],
        "expected_claims": ["one of the parties or both", "in consideration of what the other shall give or do", "event which is uncertain"],
        "notes": "The definitional article on aleatory contracts.",
    },
    {
        "question": "What is the rule for a movable that is not treasure and is found by "
        "someone else?",
        "category": "definition",
        "gold_nodes": ["node_civil-code-public_article_719"],
        "expected_claims": ["must return it to its previous possessor", "deposit it with the mayor", "publicly announced"],
        "notes": "A rule expressed as a procedure. The gold is the found-movable "
        "article, not the treasure article beside it.",
    },
    {
        "question": "What is a fideicommissary substitution?",
        "category": "definition",
        "gold_nodes": ["node_civil-code-public_article_863"],
        "expected_claims": ["the fiduciary or first heir instituted", "obliged to preserve", "transmit to a second heir"],
        "notes": "A specialised substitution. Article 857 is the general "
        "definition and is a distractor.",
    },
    {
        "question": "How is ownership acquired by intellectual creation, and who are the "
        "persons who acquire it?",
        "category": "definition",
        "gold_nodes": [
            "node_civil-code-public_article_721",
            "node_civil-code-public_article_722",
        ],
        "expected_claims": ["the following persons acquire ownership", "the author and the composer", "even before the publication"],
        "notes": "The list is in 721 and the ownership in 722; the answer needs "
        "both, and 712 is the general acquisition article.",
    },
    # --- multi-section (8) -------------------------------------------------
    {
        "question": "What are the requisites for the celebration of marriage, and who "
        "may solemnize it?",
        "category": "multi-section",
        "gold_nodes": [
            "node_civil-code-public_article_53",
            "node_civil-code-public_article_56",
        ],
        "expected_claims": ["requisites", "solemnized by", "judges", "mayors", "priests"],
        "notes": "Article 53 lists the requisites and 56 lists who may solemnize. "
        "Article 57 is the place-of-solemnization distractor.",
    },
    {
        "question": "How is a legal separation petition filed, and what grounds are "
        "available?",
        "category": "multi-section",
        "gold_nodes": [
            "node_civil-code-public_article_97",
            "node_civil-code-public_article_101",
        ],
        "expected_claims": ["petition for legal separation", "adultery", "concubinage", "no decree on a stipulation of facts"],
        "notes": "97 lists the grounds and 101 forbids a decree on a stipulation. "
        "Article 103 is the six-month distractor.",
    },
    {
        "question": "What happens to parental authority, and when does it terminate?",
        "category": "multi-section",
        "gold_nodes": [
            "node_civil-code-public_article_313",
            "node_civil-code-public_article_327",
        ],
        "expected_claims": ["cannot be renounced or transferred", "upon emancipation", "upon adoption"],
        "notes": "313 states the rule and 327 the four terminating events. "
        "Article 321 is the usufruct-over-the-child's-property distractor.",
    },
    {
        "question": "What is required for the adoption of a child, and what effect does "
        "the adoption have?",
        "category": "multi-section",
        "gold_nodes": [
            "node_civil-code-public_article_340",
            "node_civil-code-public_article_341",
        ],
        "expected_claims": ["written consent", "the parents", "the same rights and duties", "legitimate child"],
        "notes": "340 is the consent and 341 the effect. Article 345 (the Rules of "
        "Court) is the procedure distractor.",
    },
    {
        "question": "How is emancipation effected, and what are its consequences for "
        "parental authority?",
        "category": "multi-section",
        "gold_nodes": [
            "node_civil-code-public_article_397",
            "node_civil-code-public_article_327",
        ],
        "expected_claims": ["emancipation takes place", "by the marriage of the minor", "parental authority terminates"],
        "notes": "397 lists the ways and 327 terminates the authority. Article 398 "
        "is the public-instrument distractor.",
    },
    {
        "question": "How is the partition of a thing owned in common made, and what "
        "accounting follows it?",
        "category": "multi-section",
        "gold_nodes": [
            "node_civil-code-public_article_496",
            "node_civil-code-public_article_500",
        ],
        "expected_claims": ["by agreement between the parties or by judicial proceedings", "mutual accounting", "damages caused by reason of his negligence or fraud"],
        "notes": "496 is the manner and 500 the accounting. Article 494 (the right "
        "to demand partition) is the distractor.",
    },
    {
        "question": "How does the right of stoppage in transitu operate, and when are "
        "the goods still in transit?",
        "category": "multi-section",
        "gold_nodes": [
            "node_civil-code-public_article_1530",
            "node_civil-code-public_article_1531",
        ],
        "expected_claims": ["resume possession of the goods", "while they are in transit", "until the buyer takes delivery"],
        "notes": "1530 is the right and 1531 the definition of transit. Article 1532 "
        "is the notice distractor.",
    },
    {
        "question": "What are the effects of legal separation on the spouses, and on "
        "the family home?",
        "category": "multi-section",
        "gold_nodes": [
            "node_civil-code-public_article_104",
            "node_civil-code-public_article_237",
        ],
        "expected_claims": ["live separately", "manage their respective property", "the family home shall be dissolved", "cease to be exempt from execution"],
        "notes": "104 is the effect on the spouses and 237 the effect on the family "
        "home.",
    },
    # --- cross-reference (8) -----------------------------------------------
    {
        "question": "Article 1604 applies the provisions of article 1602 to a contract "
        "purporting to be an absolute sale. What does the Code provide about the "
        "presumption in a contract of sale with a right to repurchase?",
        "category": "cross-reference",
        "gold_nodes": ["node_civil-code-public_article_1601"],
        "expected_claims": ["conventional redemption", "the vendor reserves the right to repurchase", "the contract shall be presumed"],
        "notes": "The citing article names the cited one, but the PARSER merged "
        "article 1602's text into node 1601: the node's body carries 'ARTICLE 1602. "
        "The contract shall be presumed...'. So the gold is 1601, which is where "
        "the cited text actually lives. A gold of 1602 would not resolve.",
    },
    {
        "question": "Article 1606 gives the vendor a right to repurchase. How long does "
        "that right last, and what does the Code require of conventional redemption?",
        "category": "cross-reference",
        "gold_nodes": [
            "node_civil-code-public_article_1601",
            "node_civil-code-public_article_1606",
        ],
        "expected_claims": ["four years", "ten years", "conventional redemption", "the vendor reserves the right to repurchase"],
        "notes": "1606 cites 1601. The question needs the cited provision and the "
        "period.",
    },
    {
        "question": "Article 1607 refers to article 1616. What does article 1616 "
        "provide about the sale of real property subject to the right of repurchase?",
        "category": "cross-reference",
        "gold_nodes": ["node_civil-code-public_article_1616"],
        "expected_claims": ["real property", "right of repurchase", "vendor", "vendee"],
        "notes": "The citing article names the cited one. The cited article is the "
        "substantive provision.",
    },
    {
        "question": "Article 718 grants the finder of hidden treasure the right given by "
        "article 438. What does article 438 provide?",
        "category": "cross-reference",
        "gold_nodes": ["node_civil-code-public_article_438"],
        "expected_claims": ["hidden treasure", "owner", "half", "fruits"],
        "notes": "A direct cross-reference. The treasure article is short, and 719 "
        "is the found-movable distractor.",
    },
    {
        "question": "Article 1161 subjects civil obligations arising from criminal "
        "offences to the penal laws and to article 2177. What does article 2177 "
        "provide?",
        "category": "cross-reference",
        "gold_nodes": ["node_civil-code-public_article_2177"],
        "expected_claims": ["subsidiary", "civil responsibility", "criminal", "fine"],
        "notes": "1161 cites 2177. The liability article is the substance of the "
        "answer.",
    },
    {
        "question": "Article 1529 states when the unpaid seller loses his lien. What "
        "does article 1530 give him instead?",
        "category": "cross-reference",
        "gold_nodes": ["node_civil-code-public_article_1530"],
        "expected_claims": ["stopping them in transitu", "resume possession", "while they are in transit"],
        "notes": "1529 and 1530 are adjacent, and the question names the first to "
        "lead to the second.",
    },
    {
        "question": "Article 718 cites article 438, and article 720 fixes the reward the "
        "finder of a movable owes. What reward is fixed?",
        "category": "cross-reference",
        "gold_nodes": ["node_civil-code-public_article_720"],
        "expected_claims": ["one-tenth", "the sum or of the price of the thing found"],
        "notes": "The question walks the citing article to the reward article. "
        "Article 719 is the deposit distractor.",
    },
    {
        "question": "Article 120 refers to the persons designated by law to give consent "
        "to the marriage of a minor. What does article 121 require for a "
        "modification of the marriage settlements?",
        "category": "cross-reference",
        "gold_nodes": ["node_civil-code-public_article_121"],
        "expected_claims": ["made before the celebration of the marriage", "subject to the provisions of Art. 191"],
        "notes": "The question names the consent article to lead to the "
        "modification article.",
    },
    # --- exception (8) -----------------------------------------------------
    {
        "question": "Under what exception may a marriage be solemnized without a "
        "marriage license?",
        "category": "exception",
        "gold_nodes": ["node_civil-code-public_article_72"],
        "expected_claims": ["on the point of death", "more than fifteen kilometers", "without necessity of a marriage license"],
        "notes": "Article 72 is the exception to the license requirement of article 58. "
        "Article 57 is the place-of-solemnization distractor.",
    },
    {
        "question": "Article 494 allows a co-owner to demand partition, but what "
        "exception does it state about keeping the thing undivided?",
        "category": "exception",
        "gold_nodes": ["node_civil-code-public_article_494"],
        "expected_claims": ["an agreement to keep the thing undivided", "not exceeding ten years", "extended by a new agreement"],
        "notes": "The exception is inside the same article. Article 496 is the "
        "manner distractor.",
    },
    {
        "question": "Under what exception is the act of a thief or robber who has entered "
        "a hotel NOT deemed force majeure?",
        "category": "exception",
        "gold_nodes": ["node_civil-code-public_article_2001"],
        "expected_claims": ["use of arms", "irresistible force"],
        "notes": "Article 2001 states the exception to article 2000's force-majeure "
        "rule.",
    },
    {
        "question": "Article 221 states the general rule on the liability of a parent for "
        "the minor's acts. What exception does article 2180 carve out?",
        "category": "exception",
        "gold_nodes": ["node_civil-code-public_article_2180"],
        "expected_claims": ["the father", "the mother", "damages caused by the minor children", "live in their company"],
        "notes": "The question names 221 to lead to the liability article 2180. "
        "Article 2176 is the general liability article.",
    },
    {
        "question": "What exception does the Code state for the sale of a thing that "
        "has only a potential existence?",
        "category": "exception",
        "gold_nodes": ["node_civil-code-public_article_1461"],
        "expected_claims": ["potential existence", "subject to the condition that the thing will come into existence", "the sale of a vain hope or expectancy is void"],
        "notes": "Article 1461 is the provision on a thing with a potential "
        "existence. The old 'article 1211' framing was wrong: 1211 is about "
        "solidarity, not about potential existence.",
    },
    {
        "question": "Article 872 forbids a testator from imposing any charge upon the "
        "legitime. What does article 874 say about a condition against remarrying?",
        "category": "exception",
        "gold_nodes": ["node_civil-code-public_article_874"],
        "expected_claims": ["absolute condition not to contract a first or subsequent marriage", "considered as not written", "imposed on the widow or widower"],
        "notes": "The question names the legitime rule to lead to the "
        "remarriage-condition rule.",
    },
    {
        "question": "Article 2000 makes the hotel-keeper responsible for the loss of "
        "guests' property. What does article 2001 say about the act of a thief?",
        "category": "exception",
        "gold_nodes": ["node_civil-code-public_article_2001"],
        "expected_claims": ["not deemed force majeure", "use of arms", "irresistible force"],
        "notes": "A second route to the same gold as the row above, worded "
        "differently. The gold is the same provision; the questions are not.",
    },
    {
        "question": "Under what exception is a minor who may not contract marriage without "
        "parental consent still able to acknowledge a natural child?",
        "category": "exception",
        "gold_nodes": ["node_civil-code-public_article_279"],
        "expected_claims": ["parent or guardian approves"],
        "notes": "Article 279's own exception. Article 281 is the "
        "child-of-age distractor.",
    },
    # --- multi-hop (8) -----------------------------------------------------
    {
        "question": "A contract is voidable under article 1390. Which article provides "
        "for the requisites of a contract, and what are they?",
        "category": "multi-hop",
        "gold_nodes": [
            "node_civil-code-public_article_1318",
            "node_civil-code-public_article_1390",
        ],
        "expected_claims": ["consent", "object", "cause", "incapable", "fraud", "mistake", "undue influence"],
        "notes": "The question reaches the voidability list and then the "
        "requisites, so the answer needs both articles.",
    },
    {
        "question": "Who may be an heir, and what is the rule on a disposition in favour "
        "of an unknown person?",
        "category": "multi-hop",
        "gold_nodes": [
            "node_civil-code-public_article_782",
            "node_civil-code-public_article_845",
        ],
        "expected_claims": ["called to the succession", "by the provision of a will or by operation of law", "void", "unless by some event or circumstance his identity becomes certain"],
        "notes": "782 is the definition of an heir and 845 the rule on unknown "
        "persons. Article 843 is the naming distractor.",
    },
    {
        "question": "How is a will revoked, and what is the effect of a revocation based "
        "on a false cause?",
        "category": "multi-hop",
        "gold_nodes": [
            "node_civil-code-public_article_831",
            "node_civil-code-public_article_833",
        ],
        "expected_claims": ["annul only such dispositions", "inconsistent with or contrary", "based on a false cause or an illegal cause", "null and void"],
        "notes": "831 is the implied revocation and 833 the null revocation. "
        "Article 832 is the renouncing-heirs distractor.",
    },
    {
        "question": "What is a legacy or devise of a thing that belongs to another person, "
        "and when is it void?",
        "category": "multi-hop",
        "gold_nodes": [
            "node_civil-code-public_article_929",
            "node_civil-code-public_article_930",
        ],
        "expected_claims": ["limited to such part or interest", "void", "erroneously believed that the thing pertained to him"],
        "notes": "929 limits the legacy and 930 makes it void on the testator's "
        "mistake. Article 931 is the acquisition distractor.",
    },
    {
        "question": "What is the legitime of legitimate children, and how much of the "
        "hereditary estate may the parent freely dispose of?",
        "category": "multi-hop",
        "gold_nodes": [
            "node_civil-code-public_article_888",
            "node_civil-code-public_article_889",
        ],
        "expected_claims": ["one-half of the hereditary estate", "freely dispose of the remaining half"],
        "notes": "888 is the children's legitime and 889 the parents'. Article 890 "
        "is the division distractor.",
    },
    {
        "question": "What is the effect of a subsequent reconciliation between the "
        "offender and the offended person on a disinheritance?",
        "category": "multi-hop",
        "gold_nodes": [
            "node_civil-code-public_article_922",
            "node_civil-code-public_article_923",
        ],
        "expected_claims": ["deprives the latter of the right to disinherit", "renders ineffectual any disinheritance", "the children and descendants of the person disinherited"],
        "notes": "922 is the reconciliation and 923 the succession of the "
        "disinherited's children.",
    },
    {
        "question": "What is the effect of a condition imposed upon an heir, and how is a "
        "purely potestative condition fulfilled?",
        "category": "multi-hop",
        "gold_nodes": [
            "node_civil-code-public_article_876",
            "node_civil-code-public_article_884",
        ],
        "expected_claims": ["must be fulfilled by him as soon as he learns of the testator's death", "rules established for conditional obligations"],
        "notes": "876 is the potestative condition and 884 the governing rule. "
        "Article 872 is the legitime distractor.",
    },
    {
        "question": "What are the ways a partition may be made, and what happens when "
        "one of the co-owners is negligent or fraudulent?",
        "category": "multi-hop",
        "gold_nodes": [
            "node_civil-code-public_article_496",
            "node_civil-code-public_article_500",
        ],
        "expected_claims": ["by agreement between the parties or by judicial proceedings", "mutual accounting", "damages caused by reason of his negligence or fraud"],
        "notes": "A second route to the partition pair, worded differently from "
        "the multi-section row above.",
    },
    # --- comparison (8) ----------------------------------------------------
    {
        "question": "How do the legitime of legitimate children and the legitime of "
        "legitimate parents differ?",
        "category": "comparison",
        "gold_nodes": [
            "node_civil-code-public_article_888",
            "node_civil-code-public_article_889",
        ],
        "expected_claims": ["legitimate children and descendants", "one-half of the hereditary estate", "legitimate parents or ascendants", "one-half of the hereditary estates of their children"],
        "notes": "Two symmetric articles; the answer has to state each side.",
    },
    {
        "question": "Compare the requisites of a contract with the requisites for the "
        "solemnization of marriage.",
        "category": "comparison",
        "gold_nodes": [
            "node_civil-code-public_article_1318",
            "node_civil-code-public_article_53",
        ],
        "expected_claims": ["consent", "object", "cause", "requisites", "celebration"],
        "notes": "Two different statutory lists. Article 53 opens the enumeration "
        "and the following articles carry each item.",
    },
    {
        "question": "Compare the ways the unpaid seller may stop the goods in transitu: "
        "actual possession and notice.",
        "category": "comparison",
        "gold_nodes": [
            "node_civil-code-public_article_1530",
            "node_civil-code-public_article_1532",
        ],
        "expected_claims": ["right of stoppage in transitu", "actual possession", "giving notice of his claim", "carrier"],
        "notes": "1532 states the two methods, 1530 the right.",
    },
    {
        "question": "Compare the requisites of an offer with the requisites of an "
        "acceptance.",
        "category": "comparison",
        "gold_nodes": [
            "node_civil-code-public_article_1319",
            "node_civil-code-public_article_1320",
        ],
        "expected_claims": ["the meeting of the offer and the acceptance", "upon the thing and the cause", "express or implied"],
        "notes": "1319 is the consent article and 1320 the acceptance article. The "
        "answer must carry both.",
    },
    {
        "question": "How do the effects of legal separation on the spouses differ from "
        "its effects on the family home?",
        "category": "comparison",
        "gold_nodes": [
            "node_civil-code-public_article_104",
            "node_civil-code-public_article_237",
        ],
        "expected_claims": ["live separately", "manage their respective property", "the family home shall be dissolved", "cease to be exempt from execution"],
        "notes": "The same pair as the multi-section row above, asked as a "
        "comparison. The questions differ; the golds are the same provisions.",
    },
    {
        "question": "Compare how a legal separation is granted and how it takes effect on "
        "the spouses' property.",
        "category": "comparison",
        "gold_nodes": [
            "node_civil-code-public_article_101",
            "node_civil-code-public_article_104",
        ],
        "expected_claims": ["no decree upon a stipulation of facts", "collusion", "live separately", "manage their respective property"],
        "notes": "101 is the grant and 104 the effect.",
    },
    {
        "question": "Compare the rules on a legacy of a thing belonging to another person "
        "with the rules on a legacy of a thing already belonging to the legatee.",
        "category": "comparison",
        "gold_nodes": [
            "node_civil-code-public_article_930",
            "node_civil-code-public_article_932",
        ],
        "expected_claims": ["void", "erroneously believed that the thing pertained to him", "ineffective"],
        "notes": "930 is the void legacy and 932 the ineffective legacy.",
    },
    {
        "question": "Compare the ownership of a swarm of bees with the ownership of "
        "pigeons and fish that fly to another owner.",
        "category": "comparison",
        "gold_nodes": [
            "node_civil-code-public_article_716",
            "node_civil-code-public_article_717",
        ],
        "expected_claims": ["the owner of a swarm of bees", "pursue them to another's land", "pigeons and fish", "provided they have not been enticed"],
        "notes": "Two adjacent acquisition articles with different conditions.",
    },
    # --- the eight additional rows (8) --------------------------------------
    # The pool is 72 rows: 64 answerable + 8 unanswerable. The eight below
    # were added to reach the frozen 72 (evaluation.md §1) and to give the
    # two redemption/assignment articles their own category coverage.
    {
        "question": "What must the vendor return when he exercises the right of "
        "repurchase?",
        "category": "direct-lookup",
        "gold_nodes": ["node_civil-code-public_article_1616"],
        "expected_claims": ["the price of the sale", "the expenses of the contract", "the necessary and useful expenses"],
        "notes": "The central provision of the redemption chapter. Articles 1617 "
        "and 1618 are its neighbours.",
    },
    {
        "question": "What is legal redemption, and who may exercise it?",
        "category": "definition",
        "gold_nodes": ["node_civil-code-public_article_1619"],
        "expected_claims": ["the right to be subrogated", "upon the same terms and conditions", "one who acquires a thing by purchase"],
        "notes": "The definition of LEGAL redemption, against the conventional "
        "redemption of article 1601.",
    },
    {
        "question": "Within what period must the right of legal pre-emption or "
        "redemption be exercised?",
        "category": "multi-section",
        "gold_nodes": [
            "node_civil-code-public_article_1620",
            "node_civil-code-public_article_1623",
        ],
        "expected_claims": ["a co-owner of a thing may exercise the right of redemption", "thirty days", "the notice in writing by the prospective vendor"],
        "notes": "1620 is who may exercise it and 1623 the period. Article 1621 "
        "is the adjoining-land distractor.",
    },
    {
        "question": "Article 1624 refers to article 1475. What does article 1475 provide "
        "about the perfection of the contract of sale?",
        "category": "cross-reference",
        "gold_nodes": ["node_civil-code-public_article_1475"],
        "expected_claims": ["perfected at the moment there is a meeting of minds", "upon the thing which is the object", "upon the price"],
        "notes": "The cited provision is the rule on when a sale is perfected. "
        "Article 1466 is the construing distractor.",
    },
    {
        "question": "What is the rule on the indivisibility of an obligation, and does it "
        "give rise to solidarity?",
        "category": "exception",
        "gold_nodes": ["node_civil-code-public_article_1210"],
        "expected_claims": ["does not necessarily give rise to solidarity", "nor does solidarity of itself imply indivisibility"],
        "notes": "A short provision whose whole content is the exception. "
        "Articles 1208 and 1209 are the presumption and division distractors.",
    },
    {
        "question": "How is the obligation extinguished by a payment made by one of the "
        "solidary debtors, and what may that debtor claim from his co-debtors?",
        "category": "multi-hop",
        "gold_nodes": [
            "node_civil-code-public_article_1217",
            "node_civil-code-public_article_1216",
        ],
        "expected_claims": ["Payment made by one of the solidary debtors extinguishes the obligation", "the creditor may choose which offer to accept", "claim from his co-debtors only the share that corresponds to each"],
        "notes": "1217 is the payment and 1216 the creditor's right to proceed "
        "against any one of the solidary debtors. Article 1214 is the "
        "payment-to-one-creditor distractor.",
    },
    {
        "question": "Compare the conventional redemption of a sale with a right to "
        "repurchase with the legal redemption of a co-owner's share.",
        "category": "comparison",
        "gold_nodes": [
            "node_civil-code-public_article_1601",
            "node_civil-code-public_article_1619",
        ],
        "expected_claims": ["conventional redemption", "the vendor reserves the right to repurchase", "legal redemption", "the right to be subrogated"],
        "notes": "The two redemption regimes, side by side. Article 1603 (the "
        "equitable-mortgage construction) is the distractor.",
    },
    {
        "question": "What is the effect of an assignment of a credit, and against whom does "
        "it produce no effect?",
        "category": "multi-section",
        "gold_nodes": [
            "node_civil-code-public_article_1625",
            "node_civil-code-public_article_1626",
        ],
        "expected_claims": ["shall produce no effect as against third persons", "unless it appears in a public instrument", "the debtor who pays his creditor shall be released"],
        "notes": "1625 is the publicity requirement and 1626 the protection of "
        "the paying debtor. Article 1627 is the accessory-rights distractor.",
    },
    # --- unanswerable (8) --------------------------------------------------
    # EVERY token of these rows is absent from the corpus — not just the
    # content tokens. The reason is measured, and it is the reason the
    # generator's check is as strict as it is:
    #
    #   "Elucidate zeppelin airship quantum observatory" -> refused
    #   "How does a zeppelin airship quantum observatory work?" -> ANSWERED
    #   (8 citations)
    #
    # The difference is the scaffolding. "How", "does", "work" and every
    # other ordinary word occur in the corpus, and a single occurrence is
    # enough: the lex component is min-max normalized per query, so one
    # matching token gives lex = 1.0 and the sufficiency gate passes. So
    # the rows below are built out of a vocabulary the corpus does not
    # contain AT ALL, including the question's verb.
    #
    # The cost is stated plainly in the split's own notes: this measures
    # the system's ability to refuse a question whose vocabulary is
    # absent, which is a WEAKER test than refusing a question that is
    # out-of-domain in substance. The abstention score this split
    # produces is a lower bound, not a measurement of refusal quality.
    {
        "question": "Elucidate zeppelin airship quantum observatory",
        "category": "direct-lookup",
        "gold_nodes": [],
        "expected_claims": [],
        "answerable": False,
        "notes": "Unanswerable: 'elucidate', 'zeppelin', 'airship', 'quantum' and "
        "'observatory' are all absent from BOTH corpora. Measured: this question "
        "refuses (insufficient=true, citations=[]) and the same question with "
        "ordinary scaffolding ('How does ... work?') is answered with 8 citations. "
        "The verb was 'explain' until the generator's check was made to reach "
        "the secondary corpus: 'explain' is absent from the Civil Code and "
        "present in the U.S. Code Title 11.",
    },
    {
        "question": "Outline zeppelin airship quantum observatory",
        "category": "definition",
        "gold_nodes": [],
        "expected_claims": [],
        "answerable": False,
        "notes": "Unanswerable: 'outline', 'zeppelin', 'airship', 'quantum' and "
        "'observatory' are absent from the corpus.",
    },
    {
        "question": "Elaborate centrifuge spectrometer ultraviolet embargo",
        "category": "multi-section",
        "gold_nodes": [],
        "expected_claims": [],
        "answerable": False,
        "notes": "Unanswerable: 'elaborate', 'centrifuge', 'spectrometer', "
        "'ultraviolet' and 'embargo' are absent from the corpus.",
    },
    {
        "question": "Recite quantum spectrometer embargo",
        "category": "exception",
        "gold_nodes": [],
        "expected_claims": [],
        "answerable": False,
        "notes": "Unanswerable: 'recite', 'quantum', 'spectrometer' and 'embargo' "
        "are absent from the corpus. The verb is part of the constraint: "
        "'define' occurs in the U.S. Code Title 11 corpus, and a question "
        "whose verb is a word the corpus contains can be answered by "
        "vocabulary alone.",
    },
    {
        "question": "Recite blockchain ledger cryptocurrency",
        "category": "cross-reference",
        "gold_nodes": [],
        "expected_claims": [],
        "answerable": False,
        "notes": "Unanswerable: 'recite', 'blockchain', 'ledger' and "
        "'cryptocurrency' are absent from the corpus.",
    },
    {
        "question": "Explicate spectrometer centrifuge embargo",
        "category": "multi-hop",
        "gold_nodes": [],
        "expected_claims": [],
        "answerable": False,
        "notes": "Unanswerable: 'explicate', 'spectrometer', 'centrifuge' and "
        "'embargo' are absent from BOTH corpora. The verb was 'enumerate' "
        "until the generator's check was made to reach the secondary corpus: "
        "'enumerate' is absent from the Civil Code and present in the U.S. Code "
        "Title 11.",
    },
    {
        "question": "Narrate concerto auditorium barbecue",
        "category": "comparison",
        "gold_nodes": [],
        "expected_claims": [],
        "answerable": False,
        "notes": "Unanswerable: 'narrate', 'concerto', 'auditorium' and 'barbecue' "
        "are absent from BOTH corpora. The verb was 'tell' until the "
        "generator's check was made to reach the secondary corpus: 'tell' is "
        "absent from the Civil Code and present in the U.S. Code Title 11.",
    },
    {
        "question": "Summarize zeppelin concerto ultraviolet",
        "category": "direct-lookup",
        "gold_nodes": [],
        "expected_claims": [],
        "answerable": False,
        "notes": "Unanswerable: 'summarize', 'zeppelin', 'concerto' and "
        "'ultraviolet' are absent from the corpus.",
    },
]

#: The notes the split files carry about what the unanswerable rows
#: measure. The dataset is honest about this, because a dataset that
#: measures the wrong thing should say so in its own artefact.
UNANSWERABLE_NOTE = (
    "The answerable: false rows are built so that EVERY token of the question "
    "is absent from the corpus - not only the content tokens - and the "
    "generator MEASURES that against a real ingest before it writes the "
    "split. The reason is a measured one: the abstention gate is lexical, and "
    "the lex component is min-max normalized per query, so a single matching "
    "token gives lex = 1.0. Measured on the Civil Code, "
    "'Elucidate zeppelin airship quantum observatory' refuses with no "
    "citations, while 'How does a zeppelin airship quantum observatory work?' "
    "is ANSWERED with 8 citations - the difference is entirely the "
    "scaffolding words, which occur in the corpus. A planted unanswerable "
    "question that shares ordinary vocabulary with the corpus is therefore "
    "ANSWERED, with citations, and the abstention metric then measures the "
    "dataset rather than the system. The honest limitation: refusing a "
    "question whose vocabulary is absent is a WEAKER test than refusing a "
    "question that is out-of-domain in substance. The abstention score this "
    "split produces is a lower bound on the system's refusal quality, not a "
    "measurement of it."
)


# ---------------------------------------------------------------------------
# The corpus
# ---------------------------------------------------------------------------


def ingest_corpus(db_path: str) -> str:
    """Ingest BOTH corpora into a fresh store; return the primary's id.

    The generator ingests the PDFs itself rather than reading the live
    store, so the dataset is reproducible from the PDFs and not from a
    mutable local artefact.

    The SECONDARY corpus is ingested too, and that is load-bearing rather
    than thoroughness: the unanswerable rows are checked against the
    vocabulary of every OTHER ingested document, so a check against a
    store that holds only the Civil Code can never fire. With only the
    primary ingested, "tell", "explain" and "enumerate" passed the
    generator's check - they are absent from the Civil Code and present in
    the U.S. Code Title 11 - and the runner, which checks the whole live
    store, refused the dataset the generator had just written.
    """
    from apps.api.ingestion.service import ingest_pdf

    if not CORPUS_PDF.exists():
        raise FileNotFoundError(
            f"the corpus PDF is missing: {CORPUS_PDF.as_posix()}. The dataset "
            "cannot be built without it."
        )
    document = ingest_pdf(str(CORPUS_PDF), slug=CORPUS_SLUG, db_path=db_path)
    if SECONDARY_PDF.exists():
        ingest_pdf(str(SECONDARY_PDF), slug=SECONDARY_SLUG, db_path=db_path)
    else:
        raise FileNotFoundError(
            f"the secondary corpus PDF is missing: {SECONDARY_PDF.as_posix()}. "
            "The unanswerable rows are checked against both corpora, and a "
            "dataset built against one corpus and checked against another is "
            "a dataset that breaks silently."
        )
    return document.id


def corpus_vocabulary(db_path: str, document_id: str) -> set[str]:
    """The folded token vocabulary of ONE document.

    The vocabulary is the DOCUMENT's, not the whole store's: retrieval
    scores a document's nodes against the query, so a word that occurs
    only in a different document cannot be matched by a run over this one.
    The dataset's questions all name the Civil Code, and that is the
    document this check is about.
    """
    vocabulary: set[str] = set()
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT title, text FROM nodes WHERE document_id = ?", (document_id,)
        ).fetchall()
    for row in rows:
        vocabulary |= tokens_of(f"{row['title']} {row['text']}")
    return vocabulary


def _secondary_vocabulary(db_path: str) -> set[str]:
    """The vocabulary of every OTHER ingested document.

    The dataset's questions name the Civil Code. A run over it cannot
    match a word that occurs only in another document — but the check is
    deliberately stricter than the run needs, so a row cannot become
    answerable if the dataset is ever pointed elsewhere.

    The exclusion is by SLUG rather than by the primary's document id,
    because the id is derived from the file's hash and the primary's id
    in a freshly built temporary store is not something to hard-code.
    """
    vocabulary: set[str] = set()
    conn = sqlite3.connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT n.title, n.text FROM nodes n JOIN documents d"
            " ON d.id = n.document_id WHERE d.slug != ?",
            (CORPUS_SLUG,),
        ).fetchall()
    finally:
        conn.close()
    for row in rows:
        vocabulary |= tokens_of(f"{row['title']} {row['text']}")
    return vocabulary


def _node_ids(db_path: str, document_id: str) -> set[str]:
    """The ids of every node of one document.

    The connection is CLOSED explicitly. A ``with sqlite3.connect(...)``
    block commits the transaction but does not close the connection, and
    on Windows a file that is still open cannot be deleted — which made
    the temporary directory's cleanup fail.
    """
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT id FROM nodes WHERE document_id = ?", (document_id,)
        ).fetchall()
    finally:
        conn.close()
    return {str(row[0]) for row in rows}


def content_tokens(question: str) -> list[str]:
    """The tokens of a question that must be absent from the corpus.

    **EVERY** token, not just the content ones. The reason is measured,
    not stylistic: the ``lex`` component is min-max normalized per query,
    so a SINGLE matching token gives ``lex = 1.0`` and the sufficiency
    gate passes. Measured on the Civil Code:

        "Elucidate zeppelin airship quantum observatory"
            -> refused (insufficient=true, citations=[])
        "How does a zeppelin airship quantum observatory work?"
            -> ANSWERED, 8 citations

    The difference is entirely the scaffolding: "How", "does" and "work"
    occur in the corpus. A row whose ordinary words are present is
    therefore answerable, and the abstention metric would measure the
    DATASET rather than the system.

    A bare number is excluded from the check: an unanswerable question
    that names a provision is not out-of-domain, and the identifier
    channel would find it. The unanswerable rows carry no numbers at
    all, so nothing is lost.
    """
    tokens: list[str] = []
    for token in _TOKEN_RE.findall(question.lower()):
        if token.isdigit():
            continue
        tokens.append(fold(token))
    return tokens


# ---------------------------------------------------------------------------
# The split
# ---------------------------------------------------------------------------


def _stratified_split(
    pool: list[dict[str, Any]], *, seed: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split the pool into dev and test, stratified by category.

    The shuffle is seeded (seed 42) and the STRATIFICATION is applied
    per category: each category's rows are shuffled, and the first rows
    of each category go to dev until the dev size is filled, the rest go
    to test. The result is a dev split with a balanced category profile
    and a test split with the remainder — the "stratified by category
    (>=3 dev + >=6 test per category)" of evaluation.md §1.

    The per-category quotas are computed from the pool's own category
    counts, so the split does not depend on the pool having any particular
    shape: it is a function of the pool and the seed alone.
    """
    by_category: dict[str, list[dict[str, Any]]] = {category: [] for category in CATEGORIES}
    for row in pool:
        by_category[row["category"]].append(row)

    rng = random.Random(seed)
    for category in CATEGORIES:
        rng.shuffle(by_category[category])

    total = len(pool)
    dev_target = DEV_SIZE
    # The dev quota per category is proportional to the pool, with the
    # floor of MIN_PER_CATEGORY_DEV and the ceiling that leaves at least
    # MIN_PER_CATEGORY_TEST in test.
    quotas: dict[str, int] = {}
    for category in CATEGORIES:
        available = len(by_category[category])
        proportional = available * dev_target // total
        quota = max(MIN_PER_CATEGORY_DEV, proportional)
        quota = min(quota, available - MIN_PER_CATEGORY_TEST)
        quotas[category] = max(0, quota)

    # The quotas may not add up to DEV_SIZE exactly. The remainder goes to
    # the categories with the largest pool, deterministically, and any
    # surplus is taken back from the largest quota — so the dev split is
    # EXACTLY DEV_SIZE rows and the test split exactly TEST_SIZE, and both
    # floors still hold.
    def _adjustable() -> list[str]:
        return [
            category
            for category in CATEGORIES
            if quotas[category] < len(by_category[category]) - MIN_PER_CATEGORY_TEST
        ]

    shortfall = dev_target - sum(quotas.values())
    while shortfall > 0:
        candidates = _adjustable()
        if not candidates:
            break
        candidates.sort(key=lambda category: (-len(by_category[category]), category))
        quotas[candidates[0]] += 1
        shortfall -= 1

    surplus = sum(quotas.values()) - dev_target
    while surplus > 0:
        # Take a row back from the category with the largest quota that
        # can afford to lose one and still keeps the dev floor.
        candidates = [
            category
            for category in CATEGORIES
            if quotas[category] > MIN_PER_CATEGORY_DEV
        ]
        if not candidates:
            break
        candidates.sort(key=lambda category: (-quotas[category], category))
        quotas[candidates[0]] -= 1
        surplus -= 1

    dev: list[dict[str, Any]] = []
    test: list[dict[str, Any]] = []
    for category in CATEGORIES:
        rows = by_category[category]
        quota = quotas[category]
        dev.extend(rows[:quota])
        test.extend(rows[quota:])

    # The ids are assigned AFTER the split, dev first, so the two files
    # together carry q001..qNNN and a question keeps its id when the pool
    # changes but its split does not.
    dev.sort(key=lambda row: (row["category"], row["question"]))
    test.sort(key=lambda row: (row["category"], row["question"]))
    return dev, test


def _assign_ids(rows: list[dict[str, Any]], start: int) -> list[dict[str, Any]]:
    """Number the rows ``q001``.., from ``start``."""
    numbered: list[dict[str, Any]] = []
    for offset, row in enumerate(rows):
        identifier = f"q{start + offset:03d}"
        numbered.append({"id": identifier, **row})
    return numbered


def build_split(
    pool: list[dict[str, Any]], *, seed: int = SPLIT_SEED
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The dev and test row lists, with their ids assigned."""
    dev_rows, test_rows = _stratified_split(pool, seed=seed)
    dev = _assign_ids(dev_rows, 1)
    test = _assign_ids(test_rows, len(dev) + 1)
    return dev, test


# ---------------------------------------------------------------------------
# The checks
# ---------------------------------------------------------------------------


def check_golds(
    rows: list[dict[str, Any]], node_ids: set[str]
) -> list[str]:
    """The gold ids that do not resolve against the ingested corpus.

    A gold that does not resolve yields ``Recall@5 = 0`` for its
    question while the run completes and nothing fails, so this check is
    a hard failure, not a warning.
    """
    problems: list[str] = []
    for row in rows:
        for gold in row["gold_nodes"]:
            if gold not in node_ids:
                problems.append(
                    f"{row['id']}: gold {gold!r} does not resolve in the ingested corpus"
                )
            elif not gold.startswith(f"node_{CORPUS_SLUG}_"):
                problems.append(
                    f"{row['id']}: gold {gold!r} does not start with 'node_{CORPUS_SLUG}_'"
                )
    return problems


def check_unanswerable(
    rows: list[dict[str, Any]], vocabulary: set[str], extra_vocabulary: set[str] | None = None
) -> list[str]:
    """The unanswerable rows whose content tokens ARE in the corpus.

    Each problem names the token, so the row can be rewritten against a
    word the corpus genuinely does not contain.

    ``extra_vocabulary`` is the vocabulary of the OTHER ingested
    documents. The dataset's questions name the Civil Code, and a run
    over that document cannot match a word that occurs only in another
    one — but the check is deliberately stricter than the run needs, so
    that a row cannot become answerable if the dataset is ever pointed at
    a different document. A stricter check is free; a weaker one is a
    dataset that breaks silently.
    """
    problems: list[str] = []
    for row in rows:
        if row.get("answerable", True):
            continue
        for token in content_tokens(row["question"]):
            if token in vocabulary:
                problems.append(
                    f"{row['id']}: content token {token!r} occurs in the corpus, so the "
                    "row is answerable by vocabulary"
                )
            elif extra_vocabulary is not None and token in extra_vocabulary:
                problems.append(
                    f"{row['id']}: content token {token!r} occurs in another ingested "
                    "document, so the row would become answerable if the dataset were "
                    "pointed at it"
                )
    return problems


def check_categories(rows: list[dict[str, Any]], label: str, minimum: int) -> list[str]:
    """The categories that do not have enough rows."""
    counts = Counter(row["category"] for row in rows)
    problems: list[str] = []
    for category in CATEGORIES:
        if counts.get(category, 0) < minimum:
            problems.append(
                f"{label}: category {category!r} has {counts.get(category, 0)} rows, "
                f"needs >= {minimum}"
            )
    return problems


def check_overlap(dev: list[dict[str, Any]], test: list[dict[str, Any]]) -> list[str]:
    """The questions that appear in both splits."""
    dev_ids = {row["id"] for row in dev}
    test_ids = {row["id"] for row in test}
    problems = []
    shared = sorted(dev_ids & test_ids)
    if shared:
        problems.append(f"the splits share question ids {shared}")
    dev_questions = {row["question"].strip() for row in dev}
    test_questions = {row["question"].strip() for row in test}
    shared_text = sorted(dev_questions & test_questions)
    if shared_text:
        problems.append(f"the splits share question text {shared_text}")
    return problems


# ---------------------------------------------------------------------------
# The write
# ---------------------------------------------------------------------------


def _row_json(row: dict[str, Any]) -> dict[str, Any]:
    """One row in the committed file's shape (evaluation.md §2)."""
    return {
        "id": row["id"],
        "question": row["question"],
        "document": CORPUS_SLUG,
        "category": row["category"],
        "gold_nodes": list(row["gold_nodes"]),
        "expected_claims": list(row["expected_claims"]),
        "answerable": bool(row.get("answerable", True)),
        "notes": row["notes"],
    }


def write_split(
    path: Path,
    name: str,
    rows: list[dict[str, Any]],
    *,
    seed: int,
    snapshot_id: str,
    notes: str,
) -> None:
    """Write one split file, deterministically."""
    payload = {
        "name": name,
        "snapshot_id": snapshot_id,
        "seed": seed,
        "notes": notes,
        "questions": [_row_json(row) for row in rows],
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    """Build the split, check it, and write it. Exit 0 on success."""
    args = list(sys.argv[1:] if argv is None else argv)
    output_dir = Path(args[0]) if args else OUTPUT_DIR

    with tempfile.TemporaryDirectory(prefix="tuklas-dataset-", ignore_cleanup_errors=True) as workdir:
        db_path = str(Path(workdir) / "corpus.db")
        document_id = ingest_corpus(db_path)
        vocabulary = corpus_vocabulary(db_path, document_id)
        node_ids = _node_ids(db_path, document_id)

        dev, test = build_split(QUESTIONS, seed=SPLIT_SEED)

        problems: list[str] = []
        problems += check_golds(dev + test, node_ids)
        problems += check_unanswerable(dev + test, vocabulary, _secondary_vocabulary(db_path))
        problems += check_categories(dev, "dev", MIN_PER_CATEGORY_DEV)
        problems += check_categories(test, "test", MIN_PER_CATEGORY_TEST)
        problems += check_overlap(dev, test)

        unanswerable = sum(
            1 for row in dev + test if not row.get("answerable", True)
        )
        if unanswerable < MIN_UNANSWERABLE:
            problems.append(
                f"the dataset has {unanswerable} unanswerable rows, needs "
                f">= {MIN_UNANSWERABLE}"
            )
        if len(dev) != DEV_SIZE:
            problems.append(f"the dev split has {len(dev)} rows, needs {DEV_SIZE}")
        if len(test) != TEST_SIZE:
            problems.append(f"the test split has {len(test)} rows, needs {TEST_SIZE}")

        if problems:
            print("the dataset is invalid; nothing was written:", file=sys.stderr)
            for problem in problems:
                print(f"  - {problem}", file=sys.stderr)
            return 1

        output_dir.mkdir(parents=True, exist_ok=True)
        write_split(
            output_dir / "dev.json",
            "dev",
            dev,
            seed=SPLIT_SEED,
            snapshot_id="v1-dev",
            notes=(
                "The dev split of the frozen 72-question benchmark (evaluation.md §1). "
                "Split seed 42, stratified by category. The dev split is the tuning "
                "split: a weight tuned on it may not be reported on it. " + UNANSWERABLE_NOTE
            ),
        )
        write_split(
            output_dir / "test.json",
            "test",
            test,
            seed=SPLIT_SEED,
            snapshot_id="v1-test",
            notes=(
                "The test split of the frozen 72-question benchmark (evaluation.md §1). "
                "Split seed 42, stratified by category. The test split is the "
                "REPORTED split and is touched once per frozen config. " + UNANSWERABLE_NOTE
            ),
        )

    counts = Counter(row["category"] for row in dev)
    test_counts = Counter(row["category"] for row in test)
    print(f"wrote {output_dir.as_posix()}/dev.json ({len(dev)} rows)")
    print(f"wrote {output_dir.as_posix()}/test.json ({len(test)} rows)")
    print(f"unanswerable rows: {unanswerable}")
    for category in CATEGORIES:
        print(f"  {category}: dev {counts.get(category, 0)}, test {test_counts.get(category, 0)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
