"""The evaluation framework (evaluation.md).

The package is the SOLE legal importer of ``experiments/vector/``
(CONSTRAINTS.md's vectorless quarantine, enforced by
``scripts/check_no_vectors.py``). Nothing in the primary path imports it,
and nothing in this package imports the primary path's internals beyond
the surfaces the agent and the retrieval layer already expose.

The modules:

* :mod:`apps.api.evaluation.dataset` — the schema, the loader and the
  validator (evaluation.md §2, §8).
* :mod:`apps.api.evaluation.scorer` — the shared correctness scorer
  (evaluation.md §3).
* :mod:`apps.api.evaluation.metrics` — the retrieval, generation and
  system metrics (evaluation.md §3-§5).
* :mod:`apps.api.evaluation.judge` — the arm-blinded, versioned judge
  (evaluation.md §4).
* :mod:`apps.api.evaluation.arms` — the two arms behind one interface
  (evaluation.md §1, ADR-003).
* :mod:`apps.api.evaluation.config` — the run config, its validation and
  the config id (api.md §4, evaluation.md §1, §6).
* :mod:`apps.api.evaluation.store` — the ``evaluation_runs`` and
  ``retrieval_runs`` rows (evaluation.md §6).
* :mod:`apps.api.evaluation.runner` — the run itself.
* :mod:`apps.api.evaluation.report` — the run report, in the honest
  reading of evaluation.md §7.
* :mod:`apps.api.evaluation.generate_dataset` — the deterministic
  generator that produced the frozen split.

**The one thing every report must say.** The vectorless arm applies
identifier precedence (ADR-005) and the dense arm has no ident component
at all, so the arms differ in more than the retrieval mechanism. The
``direct-lookup`` category is therefore not a like-for-like result. The
outcome of the comparison is UNKNOWN until it is measured, and nothing in
this package assumes it.
"""

from __future__ import annotations

__all__ = [
    "arms",
    "config",
    "dataset",
    "judge",
    "metrics",
    "report",
    "runner",
    "scorer",
    "store",
]
