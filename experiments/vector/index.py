"""The dense index of Arm B: a brute-force cosine over a NumPy array.

ADR-002 forbids a vector DATABASE in v1 — no Postgres, no pgvector, no
Chroma, no Qdrant, no FAISS. It does not forbid the arithmetic. A dense
baseline over a 2,579-node corpus needs no ANN index at all: a matrix of
2,579 unit vectors against one query vector is 2,579 dot products, which is
microseconds, and it is EXACT — no approximation error to explain away in
the reported numbers.

So this module holds a NumPy array of unit vectors and a search that
returns the top-k by cosine similarity. There is no server, no service and
no client library, which is exactly the property ADR-002's "Alternatives
Considered" section asks for: "running infra whose only legal caller is
evaluation/ invites leakage and weakens the vectorless claim."

**The corpus comes IN, not out.** This module does not read the database.
architecture.md §2 gives ``experiments/vector/`` no imports from the
primary path, and the harness (``apps/api/evaluation/``) is the module that
knows about the store. The harness reads the nodes and hands them over as
:class:`CorpusEntry` values. The dense arm therefore depends on nothing
but NumPy and fastembed.

**The shape of the result is the SAME as the vectorless arm's.** The
vectorless arm returns a ``ScoredNode`` with a five-component
``components`` dict; this arm returns a dict with the same keys, of which
only ``lex`` carries a value — and the name is the point. The dense
similarity is a similarity over the node's own text, which is the
vectorless arm's ``lex`` slot and NOT its ``ident`` slot: a dense arm has
no identifier channel at all (ADR-005: "the dense arm has no ident
component"), so naming a provision in a query reaches it only
semantically.

**The score is min-max normalized per query**, exactly as the vectorless
arm's ``lex`` component is (retrieval.md §3: "BM25 over body, min-max
normalized per query → [0,1]"). The min-max is MONOTONIC, so the ranking
is the raw ranking; what it buys is a common SCALE, so the sufficiency
gate's 0.15 threshold means the same thing in both arms. Without it the
dense arm's gate would sit on a cosine scale and the vectorless arm's on a
five-component sum scale, and the abstention metric would compare two
different questions.

The comparability note belongs HERE, in the arm itself, and not only in a
report: the two arms do not differ only in the retrieval MECHANISM. The
vectorless arm applies identifier precedence (ADR-005) and the dense arm
has no ident component at all, so the arms differ in the SIGNALS they can
use as well as in how they combine them. A reader of the numbers must be
told that, and the arm's own ``describe()`` says it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from experiments.vector.embedder import EMBEDDING_DIM, Embedder

_LOG = logging.getLogger(__name__)

#: The five component names of retrieval.md §3. The dense arm fills one of
#: them and reports 0.0 for the other four, so a component breakdown
#: compares like with like across the arms.
COMPONENT_NAMES: tuple[str, ...] = ("lex", "ident", "title", "ref", "hier")

#: The component the dense similarity occupies. It is the ``lex`` slot
#: because a dense similarity over the node's own text is a lexical-surface
#: analogue: it scores the node's text against the query's text. The
#: ``ident`` slot is a SYMBOLIC channel and the dense arm has none.
DENSE_COMPONENT = "lex"


@dataclass(frozen=True)
class CorpusEntry:
    """One node of the corpus the dense arm indexes."""

    node_id: str
    title: str
    text: str
    page_start: int | None = None
    depth: int = 0
    position: int = 0


def embeddable_text(entry: CorpusEntry) -> str:
    """The text the dense arm embeds for one node.

    **The node's own text, ONCE.** ``entry.text`` already carries the
    heading: the parser slices a node's body from the heading's own
    line onward, so on the Civil Code 2,578 of 2,579 nodes' text begins
    with their own title (measured). A surface that concatenated the
    title a SECOND time embedded the heading twice, and the duplication
    systematically re-weighted provisions against containers — the
    title's share of the embedded text was 32.4% for articles with the
    duplicate against 52.9% without, and 19.6% against 25.9% for
    chapters. Removing it changed the dense arm's top-5 for every
    question measured (44 of 48 in one sweep), which is what a surface
    change does: a ranking that moves under a change that should be a
    no-op is not a stable measurement.

    The vectorless arm's retrieval.md §3 disjointness rule is a
    QUERY-side rule about IDENTIFIER tokens (ADR-007), not a rule that
    keeps a heading's ordinary words out of the lexical surface — the
    parser stores the heading line at the start of a node's body text,
    so on the vectorless side a heading's words are lexical surface too.
    A dense arm has ONE channel and no components to keep disjoint, and
    it embeds the node's complete text exactly once.

    The function is PUBLIC and module-level, so the harness — the sole
    legal importer of this package — can name the rule and test it
    without the arm having to be built.
    """
    return entry.text


def _min_max(values: list[float]) -> list[float]:
    """Min-max normalize to [0,1]; all-equal input maps to all zeros.

    This is the same rule the vectorless arm's ``_min_max`` applies
    (retrieval.md §3), spelled out here because the dense arm cannot
    import the vectorless arm's internals — architecture.md §2 gives
    ``experiments/vector/`` no imports from the primary path, and the two
    arms must stay independent of each other.
    """
    if not values:
        return []
    low = min(values)
    high = max(values)
    if high <= low:
        return [0.0 for _ in values]
    span = high - low
    return [(value - low) / span for value in values]


class VectorIndex:
    """A document's nodes, embedded, searched by cosine similarity.

    The index is built once per document and then queried many times. The
    embedding of a node is a pure function of its text and the pinned
    model, so the built index is REPRODUCIBLE: rebuilding it produces the
    same vectors, and the same vectors produce the same rankings.
    """

    def __init__(
        self,
        corpus: list[CorpusEntry],
        *,
        embedder: Embedder | None = None,
    ) -> None:
        self.corpus = list(corpus)
        self.embedder = embedder or Embedder()
        self._node_ids: list[str] = []
        self._titles: list[str] = []
        self._vectors: Any | None = None
        self._built = False

    # -- construction ------------------------------------------------------

    def _embeddable_text(self, entry: CorpusEntry) -> str:
        """The text the arm embeds for one node."""
        return embeddable_text(entry)

    def build(self) -> None:
        """Embed the corpus and hold the vectors.

        Idempotent: a second call on a built index is a no-op, so the
        runner can ask for the index without knowing whether it was built.
        """
        if self._built:
            return
        import numpy as np

        self._node_ids = [entry.node_id for entry in self.corpus]
        self._titles = [entry.title for entry in self.corpus]
        if self.corpus:
            self._vectors = self.embedder.embed(
                [self._embeddable_text(entry) for entry in self.corpus]
            )
        else:
            self._vectors = np.zeros((0, EMBEDDING_DIM), dtype="float32")
        self._built = True

    # -- search ------------------------------------------------------------

    def search(self, query: str, top_k: int = 10) -> list[dict[str, Any]]:
        """The top-``top_k`` nodes for ``query``, by cosine similarity.

        The result shape matches the vectorless arm's hit shape: a
        ``node_id``, a ``title``, a ``snippet``, a ``score``, a
        ``components`` dict with the five retrieval.md §3 names, and a
        ``page_start``. That is what "the arms differ only in retrieval"
        means operationally: the runner consumes one shape from both.

        The ORDER is the dense arm's own: score descending, then the node
        id ascending as the tiebreak, so the order is total and
        deterministic. The vectorless arm's identifier precedence
        deliberately has NO counterpart here — the dense arm cannot
        identify a provision it does not match semantically, and
        pretending otherwise would be a claim about the arm that the arm
        does not support.
        """
        self.build()
        if self._vectors is None or not self._node_ids:
            return []
        query_vector = self.embedder.embed_one(query)
        # The rows are unit vectors and the query vector is a unit vector,
        # so the dot product IS the cosine similarity.
        raw = [float(value) for value in (self._vectors @ query_vector)]
        normalized = _min_max(raw)
        order = sorted(
            range(len(raw)),
            key=lambda index: (-normalized[index], self._node_ids[index]),
        )
        limit = min(max(1, top_k), len(order))
        hits: list[dict[str, Any]] = []
        for index in order[:limit]:
            entry = self.corpus[index]
            hits.append(
                {
                    "node_id": entry.node_id,
                    "title": entry.title,
                    "snippet": entry.text[:400],
                    "score": normalized[index],
                    "similarity": raw[index],
                    "components": {
                        "lex": normalized[index],
                        "ident": 0.0,
                        "title": 0.0,
                        "ref": 0.0,
                        "hier": 0.0,
                    },
                    "page_start": entry.page_start,
                    "depth": entry.depth,
                    "position": entry.position,
                }
            )
        return hits

    def describe(self) -> dict[str, Any]:
        """The arm's own description, for the run config.

        The ``ident_component`` and ``identifier_precedence`` fields are the
        honest part: this arm has no identifier channel, so a query naming
        a provision reaches it only through the embedding.
        """
        payload = self.embedder.describe()
        payload.update(
            {
                "algorithm": "dense-cosine-bruteforce",
                "store": "numpy-array-in-process",
                "ident_component": "none",
                "identifier_precedence": False,
                "score_normalization": "min-max-per-query",
                "n_nodes": len(self.corpus),
            }
        )
        return payload


def build_index(
    corpus: list[CorpusEntry],
    *,
    embedder: Embedder | None = None,
) -> VectorIndex:
    """Build the dense index of one corpus."""
    import time

    index = VectorIndex(corpus, embedder=embedder)
    started = time.perf_counter()
    index.build()
    _LOG.info(
        "dense index built: %d nodes in %d ms",
        len(index.corpus),
        int((time.perf_counter() - started) * 1000),
    )
    return index


__all__ = [
    "COMPONENT_NAMES",
    "DENSE_COMPONENT",
    "CorpusEntry",
    "VectorIndex",
    "build_index",
    "embeddable_text",
]
