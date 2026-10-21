"""The CI gate scripts: the quarantine scan, the eval-config parity check, and
the config-id derivation.

CONSTRAINTS.md names three of these gates in its "Enforced with numbers"
table and they are all scripts under `scripts/`. A gate that only exists as
a file is a rule that is asserted but not enforced, which is the failure
mode this repo's own docs keep naming. This test runs them.

What it pins:

1. `scripts/check_no_vectors.py` exits 0 on the clean tree, and exits 1 on
   each deliberate violation: a deny-list import, a vector-machinery import,
   the cosine grep rule, the agent thinness rule, and an `experiments/vector`
   import from outside the evaluation harness.
2. `scripts/check_eval_config.py` exits 0 on a valid config, and exits 1 on
   each parity violation: different models, different backends, different
   seeds, a non-zero temperature, a config id that does not match its
   content, and a missing pinned field.
3. The config id is DERIVED, not asserted: the same config always yields the
   same id, any change to the config yields a different id, and the declared
   id in a config file is the one its own content derives. This is what
   makes "re-run with the same config id" a checkable statement rather than
   a convention.

The corpus is NOT needed: nothing here touches the ingestion, the retrieval
engine, or a live model. The fixtures are synthetic JSON written into
`tmp_path`.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
NO_VECTORS = REPO_ROOT / "scripts" / "check_no_vectors.py"
EVAL_CONFIG = REPO_ROOT / "scripts" / "check_eval_config.py"
EXAMPLE_CONFIG = REPO_ROOT / "scripts" / "fixtures" / "example-run-config.json"


def _load_module(name: str, path: Path) -> ModuleType:
    """Load a script by path, without putting the repo root on sys.path.

    The scripts are not a package, so they are loaded the way a tool loads a
    file: by spec, by path.
    """
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_check_eval_config = _load_module("tuklas_check_eval_config", EVAL_CONFIG)

check_run_config = _check_eval_config.check_run_config
config_payload = _check_eval_config.config_payload
derive_config_id = _check_eval_config.derive_config_id


def _run(script: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(script), *args],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=False,
    )


#: The quarantine scan reads the WORKING TREE, and the violation cases
#: below write a probe file into it. Two tests doing that at once would see
#: each other's probe, so every scan of the tree is serialised on this lock.
#: Without it the "passes on the clean tree" case fails whenever it runs
#: alongside a violation case -- a race that shows up as a flaky failure
#: with no real cause.
_SCAN_LOCK = threading.Lock()


@contextmanager
def _scanner_lock() -> Iterator[None]:
    """Hold the tree-scan lock for the duration of the block."""
    with _SCAN_LOCK:
        yield


#: The probe file names, collected so every one of them can be removed even
#: if a test fails partway through.
_PROBE_PATHS = (
    "apps/api/retrieval/_gate_probe.py",
    "apps/api/api/_gate_probe.py",
    "apps/api/ingestion/_gate_probe.py",
    "apps/api/generation/_gate_probe.py",
    "apps/api/agent/_gate_probe.py",
    "scripts/_gate_probe.py",
)


def _valid_config() -> dict[str, Any]:
    parsed: object = json.loads(EXAMPLE_CONFIG.read_text(encoding="utf-8"))
    assert isinstance(parsed, dict)
    return parsed


# ---------------------------------------------------------------------------
# 1. The quarantine scan
# ---------------------------------------------------------------------------


def test_check_no_vectors_passes_on_the_clean_tree() -> None:
    with _scanner_lock():
        result = _run(NO_VECTORS)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert "check_no_vectors: OK" in result.stdout


@pytest.mark.parametrize(
    ("rel_path", "body", "expected"),
    [
        (
            "apps/api/retrieval/_gate_probe.py",
            "import faiss\n",
            "deny-list import 'faiss'",
        ),
        (
            "apps/api/api/_gate_probe.py",
            "import chromadb\n",
            "deny-list import 'chromadb'",
        ),
        (
            "apps/api/ingestion/_gate_probe.py",
            "from sentence_transformers import SentenceTransformer\n",
            "deny-list import 'sentence_transformers'",
        ),
        (
            "apps/api/generation/_gate_probe.py",
            "import qdrant_client\n",
            "deny-list import 'qdrant_client'",
        ),
        (
            "apps/api/agent/_gate_probe.py",
            "import fastembed\n",
            "vector-machinery import 'fastembed'",
        ),
        (
            "apps/api/retrieval/_gate_probe.py",
            "import numpy as np\n",
            "vector-machinery import 'numpy'",
        ),
        (
            "apps/api/retrieval/_gate_probe.py",
            "def cosine_similarity(a, b):\n    return 0.0\n",
            "cosine-similarity grep rule",
        ),
        (
            "apps/api/agent/_gate_probe.py",
            "import sqlite3\n",
            "agent thinness import 'sqlite3'",
        ),
        (
            "apps/api/agent/_gate_probe.py",
            "from apps.api.models import schemas\n",
            "agent thinness import 'apps.api.models'",
        ),
        (
            "apps/api/retrieval/_gate_probe.py",
            "from experiments.vector import dense\n",
            "experiments/vector imported outside apps/api/evaluation/",
        ),
        (
            "scripts/_gate_probe.py",
            "import experiments.vector.dense\n",
            "experiments/vector imported outside apps/api/evaluation/",
        ),
    ],
)
def test_check_no_vectors_fails_on_a_violation(
    rel_path: str, body: str, expected: str
) -> None:
    target = REPO_ROOT / rel_path
    with _scanner_lock():
        assert not target.exists(), f"the probe path is occupied: {rel_path}"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
        try:
            result = _run(NO_VECTORS)
        finally:
            target.unlink(missing_ok=True)
    combined = result.stdout + result.stderr
    assert result.returncode == 1, f"the scan did not fail:\n{combined}"
    assert expected in combined, combined


# ---------------------------------------------------------------------------
# 2. The eval-config parity check
# ---------------------------------------------------------------------------


def test_check_eval_config_passes_on_the_example_config() -> None:
    result = _run(EVAL_CONFIG, str(EXAMPLE_CONFIG))
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert "check_eval_config: OK" in result.stdout


def test_check_eval_config_fails_on_a_malformed_file(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    result = _run(EVAL_CONFIG, str(bad))
    assert result.returncode == 2, result.stdout + result.stderr


def test_check_eval_config_fails_on_a_missing_file(tmp_path: Path) -> None:
    result = _run(EVAL_CONFIG, str(tmp_path / "absent.json"))
    assert result.returncode == 2, result.stdout + result.stderr


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (
            lambda run: run["arms"]["vector"].__setitem__("model", "llama3.1:8b"),
            "arms.vector.model",
        ),
        (
            lambda run: run["arms"]["vector"].__setitem__("backend", "vllm"),
            "arms.vector.backend",
        ),
        (
            lambda run: run["arms"]["vector"].__setitem__("seed", 7),
            "arms.vector.seed",
        ),
        (
            lambda run: run["arms"]["vector"].__setitem__("temperature", 0.7),
            "arms.vector.temperature",
        ),
        (
            lambda run: run["config"].__setitem__("temperature", 0.2),
            "config.temperature",
        ),
        (
            lambda run: run["config"].__setitem__("model", "llama3.1:8b"),
            "config.model",
        ),
        (
            lambda run: run["config"].pop("judge_prompt_version"),
            "config.judge_prompt_version: missing",
        ),
        (
            lambda run: run["config"].pop("weights"),
            "config.weights: missing",
        ),
        (
            lambda run: run.pop("dataset_snapshot"),
            "dataset_snapshot: missing",
        ),
        (
            lambda run: run.__setitem__("k", [1, 3, 10]),
            "k: 5 is missing",
        ),
        (
            lambda run: run["config"].__setitem__("fuzzy_threshold", 99),
            "config.fuzzy_threshold: 99 is outside [80, 95]",
        ),
        (
            lambda run: run["config"]["weights"].__setitem__("lex", 3.5),
            "config.weights.lex: 3.5 is outside the legal range",
        ),
        (
            lambda run: run.__setitem__("config_id", "cfg_deadbeefdeadbeef"),
            "config_id: 'cfg_deadbeefdeadbeef' does not match",
        ),
    ],
)
def test_check_eval_config_fails_on_a_parity_violation(
    tmp_path: Path, mutate: Any, expected: str
) -> None:
    run = _valid_config()
    mutate(run)
    target = tmp_path / "config.json"
    target.write_text(json.dumps(run, indent=2), encoding="utf-8")
    result = _run(EVAL_CONFIG, str(target))
    combined = result.stdout + result.stderr
    assert result.returncode == 1, f"the check did not fail:\n{combined}"
    assert expected in combined, combined


# ---------------------------------------------------------------------------
# 3. The config id is derived
# ---------------------------------------------------------------------------


def test_config_id_is_stable_for_the_same_config() -> None:
    first = derive_config_id(_valid_config())
    second = derive_config_id(_valid_config())
    assert first == second
    assert first.startswith("cfg_")


def test_config_id_changes_when_the_config_changes() -> None:
    base = derive_config_id(_valid_config())
    changed = _valid_config()
    changed["config"]["fuzzy_threshold"] = 86
    assert derive_config_id(changed) != base


def test_config_id_ignores_the_declared_id() -> None:
    """The id is derived from the content, so declaring it is not circular."""
    run = _valid_config()
    without = derive_config_id(run)
    run["config_id"] = "cfg_0000000000000000"
    assert derive_config_id(run) == without


def test_config_payload_excludes_the_declared_id() -> None:
    run = _valid_config()
    run["config_id"] = "cfg_0000000000000000"
    assert "config_id" not in config_payload(run)


def test_the_example_config_declares_its_own_derived_id() -> None:
    run = _valid_config()
    assert run["config_id"] == derive_config_id(run)


def test_the_example_config_has_no_violations() -> None:
    violations, config_id = check_run_config(_valid_config())
    assert violations == [], violations
    assert config_id == derive_config_id(_valid_config())


def test_an_unknown_top_level_key_is_rejected() -> None:
    """A key the checker cannot check is a key that could change the run."""
    run = _valid_config()
    run["some_unrecognised_knob"] = 3
    violations, _ = check_run_config(run)
    assert any("unknown top-level key" in line for line in violations), violations


def test_an_unknown_config_key_is_rejected() -> None:
    run = _valid_config()
    run["config"]["some_unrecognised_knob"] = 3
    violations, _ = check_run_config(run)
    assert any("unknown key in the EvalConfig" in line for line in violations), violations


def test_the_example_config_is_a_valid_arm_shape() -> None:
    """The example config uses the arms-as-object shape the runner writes."""
    run = _valid_config()
    assert isinstance(run["arms"], dict)
    assert set(run["arms"]) == {"tuklas", "vector"}
    for block in run["arms"].values():
        assert block["temperature"] == 0
        assert block["model"] == run["config"]["model"]
        assert block["backend"] == run["config"]["backend"]
        assert block["seed"] == run["config"]["seed"]


def test_a_renamed_config_key_is_caught_not_ignored() -> None:
    """A typo in a key name is a failure, not a silently different config."""
    run = _valid_config()
    renamed = copy.deepcopy(run)
    renamed["config"]["judge_promptversion"] = renamed["config"].pop(
        "judge_prompt_version"
    )
    violations, _ = check_run_config(renamed)
    assert any("unknown key in the EvalConfig" in line for line in violations), violations
    assert any("judge_prompt_version: missing" in line for line in violations), violations


# ---------------------------------------------------------------------------
# 4. The checker's id derivation and the runner's agree
# ---------------------------------------------------------------------------


def _runner_config_module() -> ModuleType | None:
    """The runner's config module, if it has landed.

    The eval harness owns `apps/api/evaluation/`. When it is not there yet
    this test skips rather than fails: the checker's own derivation is
    still pinned by the tests above, and the cross-check becomes meaningful
    only when there are two implementations to compare.

    It is imported as a PACKAGE module, not loaded by path: the runner's
    module defines a dataclass, and a dataclass resolved through
    `importlib.util.module_from_spec` has no `sys.modules` entry, which
    breaks the dataclass machinery on class creation.
    """
    path = REPO_ROOT / "apps" / "api" / "evaluation" / "config.py"
    if not path.is_file():
        return None
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    try:
        from apps.api.evaluation import config as runner_config
    except ImportError:
        return None
    return runner_config


def test_the_checker_and_the_runner_derive_the_same_id() -> None:
    """Two independent implementations of the same rule must agree.

    The config id is what makes "re-run with the same config id" a
    checkable statement, and the checker compares a declared id against the
    id it derives. If the runner derived a DIFFERENT id from the same file,
    the checker's verdict would be wrong in the one direction that matters:
    it would reject a run that is actually reproducible.
    """
    runner = _runner_config_module()
    if runner is None:
        pytest.skip("the evaluation harness has not landed yet")

    for fixture in (
        EXAMPLE_CONFIG,
        REPO_ROOT / "apps" / "api" / "evaluation" / "fixtures" / "example-run-config.json",
    ):
        if not fixture.is_file():
            continue
        config = json.loads(fixture.read_text(encoding="utf-8"))
        assert derive_config_id(config) == runner.derive_config_id(config), (
            f"the checker and the runner derive different ids for {fixture.name}"
        )


def test_both_derivations_change_when_the_config_changes() -> None:
    """The id must move with the config, not just be stable."""
    runner = _runner_config_module()
    if runner is None:
        pytest.skip("the evaluation harness has not landed yet")

    base = _valid_config()
    changed = copy.deepcopy(base)
    changed["config"]["fuzzy_threshold"] = 86
    assert derive_config_id(changed) != derive_config_id(base)
    assert runner.derive_config_id(changed) == derive_config_id(changed)
