"""The dense embedding model of Arm B (ADR-002, "Arm B embedding model").

**This module is the only place in the repository that imports
``fastembed`` or ``numpy``.** The quarantine in CONSTRAINTS.md puts the
primary path (``apps/api/{ingestion,retrieval,agent,generation,api}/``) free
of any vector machinery, and the only legal home for a dense arm is
``experiments/vector/``, which only ``apps/api/evaluation/`` may import.

What ADR-002 pins, and what this module implements:

* the model is ``BAAI/bge-small-en-v1.5`` — 384 dimensions, MIT licence;
* it runs through ``fastembed==0.9.0``, whose dependency chain is ONNX
  Runtime on CPU. No torch, no ``onnxruntime-gpu``;
* the HF revision is pinned to ``5c38ec7c405ec4b44b94cc5a9bb96e735b38267a``;
* the query instruction policy is "none" — BGE v1.5 runs without an
  instruction prefix, with slight degradation, and the alternative (e5)
  would force a prefix discipline the eval config would then have to
  enforce;
* the vectors are NOT stored in a vector database. ADR-002 forbids one in
  v1, and a brute-force cosine over a NumPy array needs none.

The embedding is DETERMINISTIC: the same text embeds to the same vector
on every run, which is what makes "same config → same retrieval metrics
exactly" (evaluation.md §6) hold for the dense arm as well as the
vectorless one.
"""

from __future__ import annotations

import logging
from typing import Any

_LOG = logging.getLogger(__name__)

#: The embedding model id (ADR-002, "Arm B embedding model").
EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"

#: The pinned Hugging Face revision of that model (ADR-002). fastembed
#: resolves a revision through its ``revision`` argument; pinning it is
#: what makes the arm reproducible rather than merely repeatable.
EMBEDDING_REVISION = "5c38ec7c405ec4b44b94cc5a9bb96e735b38267a"

#: The dimension of the model's output. Recorded in the run config so a
#: run can name the geometry of its vectors.
EMBEDDING_DIM = 384

#: The batch size the embedder uses. It is NOT fastembed's default of 256.
#:
#: Measured on the Civil Code corpus (2,579 nodes) with ONNX Runtime on
#: CPU: at the default batch size the session tries to allocate a ~2.9 GB
#: attention buffer and dies with
#: ``BFCArena::AllocateRawInternal Failed to allocate memory for
#: requested buffer of size 2938318848``. The corpus's own texts are not
#: the problem - the mean node text is 292 characters and the longest is
#: 2,870 - so the allocation is the BATCH, not the input.
#:
#: At 32 the whole corpus embeds in about 6 seconds and the allocation
#: fits comfortably. The value is recorded in the run config, because a
#: different batch size does not change the vectors (BGE truncates its
#: input at 512 tokens either way) but it does change whether the arm
#: completes at all, and an arm that dies is not a measurement.
EMBED_BATCH_SIZE = 32

#: The dense arm is DENSE. This constant is the arm's self-description, it
#: is recorded in every run's config, and the report prints it. A baseline
#: that silently stopped being dense would otherwise be reported as a
#: dense result.
ARM_DENSE = True


class Embedder:
    """The dense embedder: text in, a unit-length vector out.

    The model is loaded LAZILY, on the first embedding call. A process
    that imports this module but never embeds anything (a test that only
    checks the arm's shape) pays no model load, and a process that cannot
    reach the model weights fails at the first call with a clear error
    rather than at import time.
    """

    def __init__(
        self,
        model_name: str = EMBEDDING_MODEL,
        *,
        revision: str = EMBEDDING_REVISION,
    ) -> None:
        self.model_name = model_name
        self.revision = revision
        self._model: Any | None = None

    def _load(self) -> Any:
        """Load the fastembed model once and keep it."""
        if self._model is None:
            from fastembed import TextEmbedding

            self._model = TextEmbedding(
                self.model_name,
                revision=self.revision,
                cache_dir=None,
            )
        return self._model

    def embed(self, texts: list[str]) -> Any:
        """Embed ``texts`` into a ``(len(texts), 384)`` float array.

        The rows are L2-NORMALIZED by fastembed, so a dot product between
        two of them is already the cosine similarity. The normalization is
        not re-applied here: doing it twice would be a no-op on a unit
        vector, and re-implementing it here would be a second place for
        the geometry to be defined.

        The batch size is set EXPLICITLY, and it is not fastembed's
        default of 256. Measured on the Civil Code corpus: the default
        batch tries to allocate a ~2.9 GB attention buffer inside ONNX
        Runtime and the session dies with
        ``BFCArena::AllocateRawInternal Failed to allocate memory for
        requested buffer of size 2938318848``. The batch is split into
        chunks that fit in memory instead, so the index build completes on
        a machine without a GPU and without an enormous allocation.
        """
        if not texts:
            import numpy as np

            return np.zeros((0, EMBEDDING_DIM), dtype="float32")
        model = self._load()
        vectors = list(model.embed(texts, batch_size=EMBED_BATCH_SIZE))
        import numpy as np

        return np.asarray(vectors, dtype="float32")

    def embed_one(self, text: str) -> Any:
        """Embed one text into a ``(384,)`` float array."""
        return self.embed([text])[0]

    def describe(self) -> dict[str, Any]:
        """The arm's own description, for the run config.

        The revision and the dimension are recorded so a run names the
        exact model that produced its vectors. A run whose vectors came
        from a different revision is not comparable with this one, and the
        config is where that difference has to be visible.
        """
        return {
            "provider": "fastembed",
            "model": self.model_name,
            "revision": self.revision,
            "dim": EMBEDDING_DIM,
            "dense": ARM_DENSE,
            "query_instruction": "none",
        }


__all__ = [
    "ARM_DENSE",
    "EMBEDDING_DIM",
    "EMBEDDING_MODEL",
    "EMBEDDING_REVISION",
    "Embedder",
]
