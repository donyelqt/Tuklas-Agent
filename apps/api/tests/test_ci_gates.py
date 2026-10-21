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
4. The CI WORKFLOW's own steps are checked for the class of defect that
   made the workflow red twice: a step that verifies a file under one name
   while downloading it under another, a step that needs a tool or a model
   the runner does not have, and a step whose gate can be satisfied by
   accident. These are read out of `.github/workflows/ci.yml` itself, so
   the workflow file and this test cannot drift.

The corpus is NOT needed: nothing here touches the ingestion, the retrieval
engine, or a live model. The fixtures are synthetic JSON written into
`tmp_path`.
"""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
NO_VECTORS = REPO_ROOT / "scripts" / "check_no_vectors.py"
EVAL_CONFIG = REPO_ROOT / "scripts" / "check_eval_config.py"
EXAMPLE_CONFIG = REPO_ROOT / "scripts" / "fixtures" / "example-run-config.json"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"


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


# ---------------------------------------------------------------------------
# 4. the CI WORKFLOW's own steps
# ---------------------------------------------------------------------------


def _workflow() -> dict[str, Any]:
    """The parsed workflow, or a skip if PyYAML is not installed."""
    yaml = pytest.importorskip("yaml", reason="PyYAML is needed to read the workflow")
    parsed = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    assert isinstance(parsed, dict)
    return parsed


def _step_script(workflow: dict[str, Any], name: str) -> str:
    """The `run:` block of the named step."""
    for step in workflow["jobs"]["ci"]["steps"]:
        if step.get("name") == name:
            script = step.get("run")
            assert isinstance(script, str), f"the step {name!r} has no run: block"
            return script
    raise AssertionError(f"the workflow has no step named {name!r}")


def test_the_gitleaks_archive_is_downloaded_under_the_name_the_checksum_lists() -> None:
    """The archive's name on disk must be the name in the checksums line.

    The defect this pins: the archive was downloaded as `gitleaks.tar.gz`
    while the checksums file lists it as
    `gitleaks_<version>_linux_x64.tar.gz`, and `sha256sum --check`
    resolves the file by the NAME IN THE CHECKSUM LINE. The step therefore
    died with "FAILED open or read" before the scan ever ran.

    The check is on the SHAPE, not on a literal: the archive is written
    under a variable, and that variable is what both the download and the
    checksum lookup name.
    """
    workflow = _workflow()
    script = _step_script(workflow, "Install gitleaks")

    assert 'archive="gitleaks_${GITLEAKS_VERSION}_linux_x64.tar.gz"' in script, (
        "the archive name must be the one the release's checksums file lists"
    )
    # The download writes THAT name, not a generic one.
    assert '-o "$archive"' in script, "the download must write the archive under $archive"
    # And the checksum check is driven by the same name.
    assert 'awk -v name="${archive}"' in script, (
        "the checksum lookup must select the line for $archive"
    )
    # The old failure mode: a hardcoded generic name on disk.
    assert '-o gitleaks.tar.gz' not in script, (
        "the archive must not be downloaded under a name the checksums file does not list"
    )


def test_the_gitleaks_step_fails_closed_when_the_checksum_is_wrong() -> None:
    """The gate must still be a gate.

    Two things have to hold at once: the bytes must match the checksum,
    and the version the binary REPORTS must be the pinned one. A checksum
    alone proves the bytes are the ones the release published; it does not
    prove the release is the one the workflow asked for.
    """
    workflow = _workflow()
    script = _step_script(workflow, "Install gitleaks")

    assert "sha256sum --check --strict" in script, "the checksum check must stay"
    # The checksum line is SELECTED first, so a missing entry fails loudly
    # instead of producing an empty checksum list.
    assert 'if [ -z "${expected_line}" ]' in script, (
        "a checksums file that does not list the archive must fail by name"
    )
    # The version the binary reports is checked against the pin.
    assert 'installed_version="$(gitleaks version)"' in script
    assert 'if [ "$installed_version" != "${GITLEAKS_VERSION}" ]' in script, (
        "the version the binary reports must be checked against the pinned version"
    )


def test_the_gitleaks_download_stays_out_of_the_checkout() -> None:
    """The scan step must scan the repository, not the download directory.

    The `gitleaks` step runs `gitleaks detect --source .` in the
    checkout. The install step used to drop the archive and the
    checksums file into that same tree, so the scan's input was not the
    repository. The download goes to its own directory instead.
    """
    workflow = _workflow()
    script = _step_script(workflow, "Install gitleaks")

    assert 'work_dir="$(mktemp -d)"' in script, (
        "the download must go to its own directory, not into the checkout"
    )
    assert 'cd "$work_dir"' in script


def test_the_reproducibility_gate_does_not_need_a_model_backend() -> None:
    """The reproducibility gate must not need a live LLM.

    The defect this pins: the script's ARMS pass built the arms WITHOUT a
    generator, so each arm fell back to `generator_from_env()` -- the real
    local model -- and the pass died with `LLM_UNAVAILABLE` on a CI
    runner that has no model backend, before it compared anything.

    The script is checked here on its own terms: it must build the arms
    with the deterministic stub, and the stub must be the model's
    generator, not the environment's.
    """
    script = (REPO_ROOT / "scripts" / "check_reproducibility.py").read_text(
        encoding="utf-8"
    )
    assert "StubGenerator" in script, (
        "the arms pass must use the model-free stub, not the configured LLM"
    )
    assert "generator=generator" in script, (
        "both arms must be given the stub explicitly"
    )
    # The arms must not be built bare: a bare arm falls back to the LLM.
    assert "TuklasArm(db_path, document.id)," not in script, (
        "an arm built without a generator falls back to the real local LLM"
    )
    assert "VectorArm(db_path, document.id)," not in script, (
        "an arm built without a generator falls back to the real local LLM"
    )


def test_the_workflow_file_is_valid_yaml_and_every_run_block_is_valid_bash(
    tmp_path: Path,
) -> None:
    """The workflow must parse, and every `run:` block must be valid bash.

    A workflow that does not parse never runs; a `run:` block with a bash
    syntax error fails at the point of the error rather than at review.

    The block is handed to bash as a FILE, not on stdin: the `bash` on a
    Windows host is the WSL launcher, and it does not read a script from
    its standard input the way a Linux bash does.
    """
    workflow = _workflow()
    steps = workflow["jobs"]["ci"]["steps"]
    assert steps, "the workflow has no steps"

    bash = _git_bash() or shutil.which("bash")
    if bash is None:
        pytest.skip("no bash on PATH to syntax-check the run: blocks with")

    for index, step in enumerate(steps):
        script = step.get("run")
        if not script:
            continue
        block = tmp_path / f"step{index:02d}.sh"
        block.write_text(script, encoding="utf-8")
        result = subprocess.run(
            [bash, "-n", str(block)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, (
            f"the run: block of {step.get('name')!r} is not valid bash:\n{result.stderr}"
        )


def _git_bash() -> str | None:
    """Git Bash, on a Windows host that has no bash on PATH."""
    for candidate in (
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files\Git\usr\bin\bash.exe",
    ):
        if Path(candidate).is_file():
            return candidate
    return None


# ---------------------------------------------------------------------------
# 5. the gitleaks step, EXECUTED
# ---------------------------------------------------------------------------

#: A `curl` that serves the staged files instead of the network, and records
#: the directory it was called in, so the test can see WHERE the download
#: went. The archive's bytes are the staged ones, and the checksums file
#: lists the real sha256 of those bytes, so the checksum check is exercised
#: for real rather than stubbed.
_CURL_STUB = """#!/usr/bin/env bash
out=""; prev=""
for a in "$@"; do
  if [ "$prev" = "-o" ]; then out="$a"; fi
  prev="$a"
done
[ -n "$out" ] || exit 2
src="${STAGING_DIR}/$(basename "$out")"
[ -f "$src" ] || exit 22
cat "$src" > "$out"
pwd > "${RECORD_DIR}/download_dir.txt"
"""

#: `sudo install` is the privileged part of the step; the test does not need
#: it and cannot have it, so it is a no-op.
_SUDO_STUB = """#!/usr/bin/env bash
exit 0
"""

#: A `gitleaks` that reports the version the test wants it to report, so
#: the version assertion can be driven from both sides.
_GITLEAKS_STUB = """#!/usr/bin/env bash
echo "${FAKE_GITLEAKS_VERSION}"
"""


def _gzip_tarball_with_gitleaks() -> bytes:
    """A real gzip tarball holding a `gitleaks` file, built in memory.

    The step untars the archive it downloaded, so the staged archive has to
    be a real one. It is built in memory rather than on disk so the test
    does not depend on `tar` being on the PATH of the machine running it.
    """
    import io
    import tarfile

    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        payload = b"#!/bin/sh\necho 8.30.1\n"
        info = tarfile.TarInfo(name="gitleaks")
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))
    return buffer.getvalue()


@dataclass
class _StepRun:
    """What running a workflow step against stubs produced."""

    returncode: int
    stdout: str
    stderr: str
    #: The directory the step downloaded into, as the step's own bash
    #: reported it. This is a POSIX path (Git Bash's `/tmp/...`), so it is
    #: NOT a Windows path and must be checked with bash, not with
    #: `pathlib`.
    download_dir: str
    #: The sandbox the step ran in.
    sandbox: str
    bash: str

    @property
    def output(self) -> str:
        return self.stdout + self.stderr

    def dir_exists(self, path: str) -> bool:
        """Whether `path` is a directory, as this step's own bash sees it."""
        probe = subprocess.run(
            [self.bash, "-c", '[ -d "$1" ] && echo yes || echo no', "_", path],
            capture_output=True,
            text=True,
            check=False,
        )
        return probe.stdout.strip() == "yes"


def _run_gitleaks_step(
    tmp_path: Path,
    checksums: str,
    *,
    reported_version: str = "8.30.1",
) -> _StepRun:
    """Run the workflow's `Install gitleaks` step against stubs.

    The step is the REAL `run:` block out of the workflow file, executed by
    a real bash, with `curl`, `sudo` and `gitleaks` replaced by stubs. That
    is what makes the assertions below about BEHAVIOUR rather than about
    the shape of the script: a step that reads correctly but dies on the
    wrong exit status fails here, and no amount of reading the source would
    have said so.
    """
    bash = _git_bash() or shutil.which("bash")
    if bash is None:
        pytest.skip("no bash on PATH to execute the gitleaks step with")

    sandbox = tmp_path / "sandbox"
    bin_dir = sandbox / "bin"
    staging = sandbox / "staging"
    record = sandbox / "record"
    for directory in (bin_dir, staging, record):
        directory.mkdir(parents=True, exist_ok=True)

    (bin_dir / "curl").write_text(_CURL_STUB, encoding="utf-8", newline="\n")
    (bin_dir / "sudo").write_text(_SUDO_STUB, encoding="utf-8", newline="\n")
    (bin_dir / "gitleaks").write_text(_GITLEAKS_STUB, encoding="utf-8", newline="\n")

    # The archive's real bytes, and the checksums file that lists them
    # under the archive's own name. The archive is a REAL gzip tarball,
    # because the step untars it, and a stub that could not be untarred
    # would fail the step for a reason that has nothing to do with the
    # gate under test.
    archive_name = "gitleaks_8.30.1_linux_x64.tar.gz"
    archive_bytes = _gzip_tarball_with_gitleaks()
    (staging / archive_name).write_bytes(archive_bytes)
    digest = hashlib.sha256(archive_bytes).hexdigest()
    (staging / "checksums.txt").write_text(checksums.format(digest=digest), encoding="utf-8")

    script = _step_script(_workflow(), "Install gitleaks")
    step_file = sandbox / "step.sh"
    step_file.write_text(script, encoding="utf-8", newline="\n")

    env = dict(os.environ)
    env["GITLEAKS_VERSION"] = "8.30.1"
    env["FAKE_GITLEAKS_VERSION"] = reported_version
    # The stubs are found by PATH; the staged files and the record of the
    # download directory are found by the variables the stubs read.
    wrapper = (
        'cd "$1" && export PATH="$PWD/bin:$PATH" '
        '&& export STAGING_DIR="$PWD/staging" '
        '&& export RECORD_DIR="$PWD/record" '
        '&& exec bash "$2"'
    )
    result = subprocess.run(
        [bash, "-c", wrapper, "_", str(sandbox), str(step_file)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    recorded = record / "download_dir.txt"
    download_dir = recorded.read_text(encoding="utf-8").strip() if recorded.is_file() else ""
    return _StepRun(
        returncode=result.returncode,
        stdout=result.stdout,
        stderr=result.stderr,
        download_dir=download_dir,
        sandbox=str(sandbox),
        bash=bash,
    )


def test_the_gitleaks_step_names_the_missing_archive_when_it_is_not_listed(
    tmp_path: Path,
) -> None:
    """A checksums file that omits the archive must fail BY NAME.

    The defect this pins: the step selected the checksum line with
    `grep`, which exits 1 when it matches nothing. Under `set -e` a
    failing command substitution in an assignment aborts the shell, so the
    "the checksums file does not list ..." branch was DEAD CODE: the step
    died on the bare grep exit status, with no message naming the archive
    that was missing. The step still failed, but it failed the way an
    unexplained failure does.

    This asserts the message is actually PRINTED, by running the step.
    """
    result = _run_gitleaks_step(
        tmp_path,
        # a checksums file that lists a DIFFERENT asset, and not the archive
        "{digest}  gitleaks_8.30.1_darwin_x64.tar.gz\n",
    )
    assert result.returncode != 0, "the step must fail when the archive is not listed"
    assert "gitleaks_8.30.1_linux_x64.tar.gz" in result.output, (
        "the failure must name the archive that was expected:\n" + result.output
    )
    assert "does not list" in result.output, (
        "the failure must say the checksums file does not list the archive:\n"
        + result.output
    )


def test_the_gitleaks_step_passes_when_the_checksums_file_lists_the_archive(
    tmp_path: Path,
) -> None:
    """The happy path must still work, and must install the pinned version.

    The counterpart of the fail-closed case: a step that fails everything
    is not a gate either. The archive is downloaded, its bytes are checked
    against the checksums file, and the version the binary reports is
    compared against the pin.
    """
    result = _run_gitleaks_step(
        tmp_path,
        "{digest}  gitleaks_8.30.1_linux_x64.tar.gz\n",
    )
    assert result.returncode == 0, (
        "the step must succeed when the checksums file lists the archive:\n"
        + result.output
    )
    assert "gitleaks_8.30.1_linux_x64.tar.gz: OK" in result.stdout, (
        "the checksum must have been verified:\n" + result.stdout
    )
    assert "installed: gitleaks 8.30.1" in result.stdout


def test_the_gitleaks_step_rejects_a_binary_that_reports_another_version(
    tmp_path: Path,
) -> None:
    """The reported version must be the pinned one.

    The checksum proves the bytes are the ones the release published; it
    does not prove the release is the one the workflow asked for. A
    binary that reports a different version must be rejected.
    """
    result = _run_gitleaks_step(
        tmp_path,
        "{digest}  gitleaks_8.30.1_linux_x64.tar.gz\n",
        reported_version="8.29.0",
    )
    assert result.returncode != 0, "a wrong version must fail the step"
    assert "8.29.0" in result.output and "8.30.1" in result.output, (
        "the failure must name both the expected and the reported version:\n"
        + result.output
    )


def test_the_gitleaks_step_downloads_outside_the_checkout_and_cleans_up(
    tmp_path: Path,
) -> None:
    """The download must not land in the checkout, and must not be left behind.

    The `gitleaks` step runs `gitleaks detect --source .` in the checkout.
    The install step used to drop the archive and the checksums file into
    that same tree, so the scan's input was not the repository. The
    download goes to its own directory -- and that directory is removed
    again, on the way out, however the step ends.
    """
    result = _run_gitleaks_step(
        tmp_path,
        "{digest}  gitleaks_8.30.1_linux_x64.tar.gz\n",
    )
    assert result.returncode == 0
    download_dir = result.download_dir
    assert download_dir, "the step must record where it downloaded to"
    assert not download_dir.startswith(result.sandbox), (
        f"the download must go outside the checkout, not into {result.sandbox}"
    )
    # The trap removes the work directory on the way out, so the runner is
    # not left with the archive and the checksums file under /tmp. The path
    # is a POSIX one, so it is checked with the step's own bash.
    assert not result.dir_exists(download_dir), (
        f"the download directory {download_dir} must be removed again"
    )
