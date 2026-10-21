"""The quarantined dense baseline (ADR-003, ADR-002).

This package is the ONLY home for dense retrieval in the repository, and
``apps/api/evaluation/`` is the ONLY module allowed to import it
(CONSTRAINTS.md's vectorless quarantine, enforced by
``scripts/check_no_vectors.py``).

What lives here:

* :mod:`experiments.vector.embedder` — the pinned embedding model
  (``BAAI/bge-small-en-v1.5`` via ``fastembed==0.9.0``, 384 dims, CPU ONNX
  Runtime, HF revision pinned).
* :mod:`experiments.vector.index` — the dense index: a brute-force cosine
  over a NumPy array, with no vector database (ADR-002).

What does NOT live here, and why it matters: the arm has no identifier
component. The vectorless arm applies identifier precedence (ADR-005), so
a query naming a provision puts that provision first; the dense arm has no
symbolic label channel at all, so a query naming a provision reaches it
only through the embedding. **The two arms therefore differ in more than
the retrieval mechanism, and every report that compares them must say so.**
The comparison's OUTCOME is unknown until it is measured, and nothing in
this package assumes it.
"""

from __future__ import annotations

from experiments.vector.embedder import (
    ARM_DENSE,
    EMBEDDING_DIM,
    EMBEDDING_MODEL,
    EMBEDDING_REVISION,
    Embedder,
)
from experiments.vector.index import VectorIndex, build_index

__all__ = [
    "ARM_DENSE",
    "EMBEDDING_DIM",
    "EMBEDDING_MODEL",
    "EMBEDDING_REVISION",
    "Embedder",
    "VectorIndex",
    "build_index",
]
