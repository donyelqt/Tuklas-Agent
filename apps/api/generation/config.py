"""The generation configuration (ADR-004: "Backend selected by config").

ADR-004: the backend is selected by config, and "Model id + backend +
temperature + seed recorded in every ask trace and eval run".

This module is the ONE place the run's parameters come from. The environment
variables are read here and nowhere else, so the recorded run and the
configured run cannot drift.

| variable | default | meaning |
|---|---|---|
| ``TUKLAS_LLM_BACKEND`` | ``ollama`` | which backend to talk to |
| ``TUKLAS_LLM_MODEL`` | ``qwen2.5:3b`` | the model id |
| ``TUKLAS_LLM_BASE_URL`` | ``http://127.0.0.1:11434`` | where the backend lives |
| ``TUKLAS_LLM_TEMPERATURE`` | ``0.0`` | the sampling temperature (0 is fixed) |
| ``TUKLAS_LLM_SEED`` | ``42`` | the pinned seed |
| ``TUKLAS_LLM_TIMEOUT`` | ``120.0`` | the read timeout, in seconds |

The temperature is pinned at 0 by default because api.md §4 fixes it for the
eval; a non-zero value is allowed for exploration but is recorded, so a run
with one is distinguishable from a run without.
"""

from __future__ import annotations

import os

from apps.api.generation.interface import (
    DEFAULT_BACKEND,
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    DEFAULT_SEED,
    DEFAULT_TEMPERATURE,
    DEFAULT_TIMEOUT,
)
from apps.api.generation.service import GenerationService, build_generator

#: The environment variables, and their defaults.
ENV_BACKEND = "TUKLAS_LLM_BACKEND"
ENV_MODEL = "TUKLAS_LLM_MODEL"
ENV_BASE_URL = "TUKLAS_LLM_BASE_URL"
ENV_TEMPERATURE = "TUKLAS_LLM_TEMPERATURE"
ENV_SEED = "TUKLAS_LLM_SEED"
ENV_TIMEOUT = "TUKLAS_LLM_TIMEOUT"


def _float(name: str, default: float) -> float:
    """Read a float from the environment, falling back on a bad value."""
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _int(name: str, default: int) -> int:
    """Read an int from the environment, falling back on a bad value."""
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def generator_from_env() -> GenerationService:
    """The configured generator: the real LLM behind the agent's seam.

    This is what the ask surface uses by default. The model-free stub is NOT
    the default, because the stub applies no advice oracle and no refusal
    path: a request answered through it would bypass the safety machinery
    the generation layer exists to provide.
    """
    return build_generator(
        backend=os.environ.get(ENV_BACKEND, DEFAULT_BACKEND).strip() or DEFAULT_BACKEND,
        model=os.environ.get(ENV_MODEL, DEFAULT_MODEL).strip() or DEFAULT_MODEL,
        base_url=os.environ.get(ENV_BASE_URL, DEFAULT_BASE_URL).strip() or DEFAULT_BASE_URL,
        temperature=_float(ENV_TEMPERATURE, DEFAULT_TEMPERATURE),
        seed=_int(ENV_SEED, DEFAULT_SEED),
        timeout=_float(ENV_TIMEOUT, DEFAULT_TIMEOUT),
    )


__all__ = [
    "ENV_BACKEND",
    "ENV_BASE_URL",
    "ENV_MODEL",
    "ENV_SEED",
    "ENV_TEMPERATURE",
    "ENV_TIMEOUT",
    "generator_from_env",
]
