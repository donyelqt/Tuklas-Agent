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
import urllib.error
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from email.message import Message
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
NO_VECTORS = REPO_ROOT / "scripts" / "check_no_vectors.py"
EVAL_CONFIG = REPO_ROOT / "scripts" / "check_eval_config.py"
FETCH_CORPUS = REPO_ROOT / "scripts" / "fetch_corpus.py"
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
#: The corpus fetcher, loaded so its retry loop and its exit statuses can be
#: driven directly (section 6). Loading it has no side effects: it parses
#: arguments and touches the network only under `main()`.
_fetch_corpus = _load_module("tuklas_fetch_corpus", FETCH_CORPUS)

#: The fetcher's three outcomes, in typed names: the module is loaded
#: dynamically, so its attributes are `Any` and a test that RETURNS one
#: cannot be annotated. The values are its own, not copies: a test that
#: asserts on FETCH_UNREACHABLE asserts on what the script exits with.
FETCH_OK: int = _fetch_corpus.OK
FETCH_INTEGRITY_FAILURE: int = _fetch_corpus.INTEGRITY_FAILURE
FETCH_UNREACHABLE: int = _fetch_corpus.UNREACHABLE

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
# The arguments are recorded too, so a test can assert what the step ASKED
# curl to do (for example that a transient download failure is retried)
# without reading the script as text.
printf '%s\\n' "$@" >> "${RECORD_DIR}/curl_args.txt"
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
    # The stubs are EXECUTED, so they have to be executable. A file written
    # on Windows carries no execute bit, and on a Linux runner the shell
    # would then find the REAL `curl` and `gitleaks` on the PATH instead --
    # which fetches the real release over the network and quietly makes
    # the test assert nothing. The bit is set explicitly for that reason.
    for stub in (bin_dir / "curl", bin_dir / "sudo", bin_dir / "gitleaks"):
        stub.chmod(0o755)

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
    # The downloads ask curl to retry a TRANSIENT failure. That is not the
    # corpus step's degrade-and-warn treatment and must not become it: the
    # scanner IS a gate, so a binary that never arrives has to fail, and a
    # release that does not exist has to fail on the first attempt too.
    curl_args = (Path(result.sandbox) / "record" / "curl_args.txt").read_text(
        encoding="utf-8"
    )
    assert "--retry" in curl_args, (
        "a transient download failure must be retried:\n" + curl_args
    )
    assert "--retry-delay" in curl_args, curl_args


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


# ---------------------------------------------------------------------------
# 6. the corpus fetch, EXECUTED against a stubbed network
# ---------------------------------------------------------------------------

#: The bytes the stubbed network serves for a successful download. They are
#: NOT a real PDF: what is under test is the retry, the classification and
#: the checksum check, and the sha256 the script is handed is computed from
#: these bytes, so the check is exercised for real.
_STUB_PDF = b"%PDF-1.4 corpus bytes served by the stub\n"


class _StubResponse:
    """The part of an http.client response the fetch script uses."""

    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    def read(self) -> bytes:
        return self.payload

    def __enter__(self) -> _StubResponse:
        return self

    def __exit__(self, *exc: object) -> None:
        # Nothing is swallowed: an exception inside the `with` belongs to the
        # code under test, not to the stub.
        return None


@dataclass
class _FetchRun:
    """What driving the fetch script's `fetch` produced."""

    status: int
    attempts: int
    sleeps: list[float]
    target: Path


def _run_fetch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    fail_times: int,
    code: int | None = 522,
    body: bytes = _STUB_PDF,
    expected: str | None = None,
    already_present: bool = False,
) -> _FetchRun:
    """Drive the REAL `fetch` with a stubbed network layer.

    `fail_times` requests fail before the host starts answering, and `code` is
    the HTTP status they fail with (None is a URLError: a connection that
    never got a status at all). `time.sleep` is stubbed and its arguments
    recorded, so the backoff is asserted without the test spending 155
    seconds asleep, and the delay is the REAL one from the module.

    Stubbing the network here is the point: the retry loop, the decision of
    whether a failure is worth retrying, and the checksum check are all the
    production ones.
    """
    documents = tmp_path / "documents"
    monkeypatch.setattr(_fetch_corpus, "DOCUMENTS", documents)
    digest = expected if expected is not None else hashlib.sha256(body).hexdigest()
    name, url, _ = _fetch_corpus.PRIMARY
    if already_present:
        documents.mkdir(parents=True, exist_ok=True)
        (documents / name).write_bytes(body)

    attempts: list[int] = []
    sleeps: list[float] = []

    def fake_urlopen(request: Any, timeout: float = 0.0) -> _StubResponse:
        attempts.append(1)
        if len(attempts) <= fail_times:
            if code is None:
                raise urllib.error.URLError("timed out")
            raise urllib.error.HTTPError(
                url, code, "<none>", hdrs=Message(), fp=None
            )
        return _StubResponse(body)

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(_fetch_corpus.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(_fetch_corpus.time, "sleep", fake_sleep)

    status = _fetch_corpus.fetch(name, url, digest)
    return _FetchRun(
        status=status,
        attempts=len(attempts),
        sleeps=sleeps,
        target=documents / name,
    )


def test_the_fetch_retries_a_transient_failure_and_verifies_the_artifact(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A 522 is retried with growing backoff, and the artifact still lands.

    The defect this pins: the download was ONE request, so a 522 from
    Cloudflare -- the CDN reporting that the origin, in another country, on
    somebody else's infrastructure, timed out -- failed the whole build, even
    though the next request is answered.
    """
    run = _run_fetch(monkeypatch, tmp_path, fail_times=2)
    captured = capsys.readouterr()

    assert run.status == FETCH_OK, captured.out + captured.err
    assert run.attempts == 3, "the third request must have been made"
    assert run.sleeps == [5, 15], "the wait must grow, and must be the real schedule"
    assert run.target.read_bytes() == _STUB_PDF, "the artifact must land"
    assert hashlib.sha256(run.target.read_bytes()).hexdigest() == hashlib.sha256(
        _STUB_PDF
    ).hexdigest(), "and it must be the bytes the checksum was computed over"
    # Each attempt is surfaced, with its number and the URL it asked for.
    assert "attempt 1/5" in captured.out, captured.out
    assert "attempt 2/5" in captured.out, captured.out
    assert _fetch_corpus.PRIMARY[1] in captured.out, captured.out
    assert "retrying in 5s" in captured.err, captured.err
    assert "sha256 matches" in captured.out, captured.out


def test_the_fetch_gives_up_loudly_after_the_full_backoff(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Every attempt failing is the UNREACHABLE outcome, and nothing is written.

    The line this prints is the one the CI step quotes in its warning, so it
    has to name the artifact, the URL, the error and the number of attempts:
    that is what tells a reader a host was down rather than a download was
    broken.
    """
    run = _run_fetch(monkeypatch, tmp_path, fail_times=99)
    captured = capsys.readouterr()
    combined = captured.out + captured.err

    assert run.status == FETCH_UNREACHABLE, combined
    assert run.attempts == 5, "four retries after the first attempt"
    assert run.sleeps == [5, 15, 45, 90], "the real schedule, in order"
    assert not run.target.exists(), "nothing that arrived may be left behind"
    assert "fetch_corpus: UNREACHABLE:" in combined, combined
    assert _fetch_corpus.PRIMARY[0] in combined, combined
    assert _fetch_corpus.PRIMARY[1] in combined, combined
    assert "522" in combined, combined
    assert "after 5 attempts" in combined, combined


def test_the_fetch_does_not_retry_a_status_the_host_chose(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A 403 is the host's own answer, so asking again is just hammering it.

    The ICNL CDN refuses an unidentified client with 403 on purpose. Four more
    requests would get four more 403s, and the delay would be spent for
    nothing.
    """
    run = _run_fetch(monkeypatch, tmp_path, fail_times=99, code=403)
    captured = capsys.readouterr()

    assert run.status == FETCH_UNREACHABLE, captured.out
    assert run.attempts == 1, "a refusal must not be retried"
    assert run.sleeps == [], "and it must not wait"


def test_the_fetch_does_not_retry_a_checksum_mismatch_and_writes_nothing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The case that must stay HARD: the host answered with the wrong bytes.

    A corpus that is not the one docs/CORPUS.md records invalidates every gold
    node id, so this is not a third-party uptime problem and must not be
    tolerated, retried, or softened. The wrong bytes are never written.
    """
    recorded = hashlib.sha256(b"the artifact docs/CORPUS.md records").hexdigest()
    run = _run_fetch(
        monkeypatch,
        tmp_path,
        fail_times=0,
        body=b"%PDF-1.4 somebody else's file",
        expected=recorded,
    )
    captured = capsys.readouterr()

    assert run.status == FETCH_INTEGRITY_FAILURE, captured.err
    assert run.status != FETCH_UNREACHABLE, (
        "a mismatch is not the unreachable outcome, so the CI step cannot "
        "treat it as a degraded run"
    )
    assert run.attempts == 1, "a mismatch must not be retried"
    assert run.sleeps == []
    assert not run.target.exists(), "the wrong bytes must not be left on disk"
    assert recorded in captured.err, captured.err
    assert "NOT written" in captured.err, captured.err


def test_the_fetch_keeps_an_artifact_that_is_already_the_recorded_one(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An artifact that is already there is not downloaded again."""
    run = _run_fetch(
        monkeypatch,
        tmp_path,
        fail_times=99,
        already_present=True,
    )
    captured = capsys.readouterr()

    assert run.status == FETCH_OK, captured.out + captured.err
    assert run.attempts == 0, "a matching file must not be fetched again"
    assert "present:" in captured.out, captured.out
    assert run.target.read_bytes() == _STUB_PDF


def test_the_script_attempts_every_named_artifact_whatever_the_other_did(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Neither corpus's failure may prevent the other's attempt.

    The defect this pins: the step ran the primary and the secondary from one
    shell under `set -e`, so the primary's failure aborted the step and the
    secondary was never attempted -- or, in the other order, the secondary's
    failure would have hidden a working primary.
    """
    primary = _fetch_corpus.PRIMARY[0]
    secondary = _fetch_corpus.SECONDARY[0]
    calls: list[str] = []
    statuses: dict[str, int] = {primary: FETCH_UNREACHABLE, secondary: FETCH_OK}

    def fake_fetch(name: str, url: str, expected: str) -> int:
        calls.append(name)
        return statuses[name]

    monkeypatch.setattr(_fetch_corpus, "fetch", fake_fetch)
    monkeypatch.setattr(sys, "argv", ["fetch_corpus", "--corpus", "all"])

    # The primary is unreachable: the secondary is still attempted, and the
    # run reports the degraded status rather than a red one.
    assert _fetch_corpus.main() == FETCH_UNREACHABLE
    assert calls == [primary, secondary]

    # The reverse: the secondary is unreachable and the primary is fine.
    calls.clear()
    statuses = {primary: FETCH_OK, secondary: FETCH_UNREACHABLE}
    assert _fetch_corpus.main() == FETCH_UNREACHABLE
    assert calls == [primary, secondary]

    # And an integrity failure outranks an unreachable host, whatever order
    # they are in: a corpus that is not the recorded one is a fact about the
    # corpus, while an unreachable host is a fact about the network.
    calls.clear()
    statuses = {
        primary: FETCH_UNREACHABLE,
        secondary: FETCH_INTEGRITY_FAILURE,
    }
    assert _fetch_corpus.main() == FETCH_INTEGRITY_FAILURE
    assert calls == [primary, secondary]


def test_the_script_fetches_only_the_corpus_it_was_asked_for(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The selector the CI step uses to keep the two outcomes apart.

    Both artifacts in one invocation would put them back under one exit
    status, which is exactly the coupling the step exists to remove.
    """
    primary = _fetch_corpus.PRIMARY[0]
    secondary = _fetch_corpus.SECONDARY[0]
    calls: list[str] = []

    def fake_fetch(name: str, url: str, expected: str) -> int:
        calls.append(name)
        return FETCH_OK

    monkeypatch.setattr(_fetch_corpus, "fetch", fake_fetch)

    monkeypatch.setattr(sys, "argv", ["fetch_corpus", "--corpus", "primary"])
    assert _fetch_corpus.main() == FETCH_OK
    assert calls == [primary]

    calls.clear()
    monkeypatch.setattr(sys, "argv", ["fetch_corpus", "--corpus", "secondary"])
    assert _fetch_corpus.main() == FETCH_OK
    assert calls == [secondary]


# ---------------------------------------------------------------------------
# 7. the corpus fetch and the pytest step, EXECUTED as the runner runs them
# ---------------------------------------------------------------------------

#: A `poetry` that plays a plan instead of running the real fetch script. The
#: plan is one exit status per invocation, consumed in order, which is enough
#: to drive every branch of the step's policy in any order -- and the order is
#: what makes the independence of the two artifacts observable.
_POETRY_FETCH_STUB = """#!/usr/bin/env bash
record="${RECORD_DIR}"
printf '%s\\n' "$*" >> "${record}/calls.txt"
count="$(wc -l < "${record}/calls.txt" | tr -d ' ')"
code="$(awk -v n="${count}" 'NR == n { print }' "${FAKE_FETCH_PLAN}")"
if [ -z "${code}" ]; then
  echo "poetry stub: the plan has no status for invocation ${count}" >&2
  exit 99
fi
corpus=""
prev=""
for arg in "$@"; do
  if [ "${prev}" = "--corpus" ]; then corpus="${arg}"; fi
  prev="${arg}"
done
if [ "${corpus}" = "primary" ]; then
  name="${FAKE_PRIMARY_NAME}"
  url="${FAKE_PRIMARY_URL}"
else
  name="${FAKE_SECONDARY_NAME}"
  url="${FAKE_SECONDARY_URL}"
fi
case "${code}" in
  0)
    echo "fetching: ${url} (attempt 1/5)"
    echo "fetched: ${name} (sha256 matches)"
    exit 0
    ;;
  3)
    echo "fetching: ${url} (attempt 1/5)"
    echo "fetch_corpus: ${name} attempt 1/5 failed: HTTP Error 522: <none>; retrying in 5s" >&2
    echo "fetch_corpus: UNREACHABLE: ${name}: ${url}: HTTP Error 522: <none> (after 5 attempts)" >&2
    exit 3
    ;;
  *)
    echo "fetch_corpus: ${name} has sha256 deadbeef, expected cafe" >&2
    exit "${code}"
    ;;
esac
"""

#: A `poetry` that plays a canned pytest run: the text it prints and the
#: status it exits with are the test's, so a green run, a degraded run and a
#: red run can all be put through the step's own reporting.
_POETRY_PYTEST_STUB = """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "${RECORD_DIR}/calls.txt"
cat "${STAGING_DIR}/pytest-output.txt"
exit "$(cat "${STAGING_DIR}/pytest-status.txt")"
"""


@dataclass
class _ScriptedStepRun:
    """What running a workflow step against a scripted stub produced."""

    returncode: int
    stdout: str
    stderr: str
    #: One line per invocation of the stubbed command, as its own `"$*"`.
    calls: list[str]

    @property
    def output(self) -> str:
        return self.stdout + self.stderr


@dataclass
class _Sandbox:
    """A bash sandbox with a stubbed `poetry` first on the PATH."""

    bash: str
    sandbox: Path
    staging: Path

    def run(self, step_name: str, extra_env: dict[str, str]) -> _ScriptedStepRun:
        """Run the named step's REAL `run:` block, with `bash -e`.

        `bash -e` is how the runner runs a `run:` block (`bash -e {0}`), so
        the step is executed under the same flag it meets in CI: a step that
        inherits `-e` and does not turn it off dies at the first failure it
        was written to tolerate, and nothing but running it can show that.
        """
        script = _step_script(_workflow(), step_name)
        step_file = self.sandbox / "step.sh"
        step_file.write_text(script, encoding="utf-8", newline="\n")
        env = dict(os.environ)
        env.update(extra_env)
        wrapper = (
            'cd "$1" && export PATH="$PWD/bin:$PATH" '
            '&& export RECORD_DIR="$PWD/record" '
            '&& export STAGING_DIR="$PWD/staging" '
            '&& exec bash -e "$2"'
        )
        result = subprocess.run(
            [self.bash, "-c", wrapper, "_", str(self.sandbox), str(step_file)],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
        calls_file = self.sandbox / "record" / "calls.txt"
        calls = (
            calls_file.read_text(encoding="utf-8").splitlines()
            if calls_file.is_file()
            else []
        )
        return _ScriptedStepRun(
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
            calls=calls,
        )


def _start_sandbox(tmp_path: Path, stub: str) -> _Sandbox:
    """A sandbox holding the stub, with the directories the step writes into."""
    bash = _git_bash() or shutil.which("bash")
    if bash is None:
        pytest.skip("no bash on PATH to execute the workflow's steps with")
    sandbox = tmp_path / "sandbox"
    bin_dir = sandbox / "bin"
    staging = sandbox / "staging"
    for directory in (bin_dir, staging, sandbox / "record"):
        directory.mkdir(parents=True, exist_ok=True)
    stub_path = bin_dir / "poetry"
    stub_path.write_text(stub, encoding="utf-8", newline="\n")
    # The stub is EXECUTED, so it needs the execute bit: a file written on
    # Windows carries none, and the step would then find the real `poetry` (or
    # nothing) instead, which quietly makes the test assert the wrong thing.
    stub_path.chmod(0o755)
    return _Sandbox(bash=bash, sandbox=sandbox, staging=staging)


def _fetched_names() -> dict[str, str]:
    """The stub's canned artifact names and URLs, which are the real ones."""
    return {
        "FAKE_PRIMARY_NAME": _fetch_corpus.PRIMARY[0],
        "FAKE_PRIMARY_URL": _fetch_corpus.PRIMARY[1],
        "FAKE_SECONDARY_NAME": _fetch_corpus.SECONDARY[0],
        "FAKE_SECONDARY_URL": _fetch_corpus.SECONDARY[1],
    }


def _run_fetch_step(tmp_path: Path, plan: str) -> _ScriptedStepRun:
    """Run the workflow's `Fetch the benchmark corpus` step against the stub.

    `plan` is one exit status per invocation, space separated: "3 0" is "the
    primary is unreachable, the secondary verifies".
    """
    sandbox = _start_sandbox(tmp_path, _POETRY_FETCH_STUB)
    plan_file = sandbox.sandbox / "plan.txt"
    # One status per LINE: the stub's awk picks the Nth record, so a plan on
    # one line would hand the whole line to `exit` as the status.
    plan_file.write_text("\n".join(plan.split()) + "\n", encoding="utf-8", newline="\n")
    extra_env = _fetched_names()
    # A relative path: the step's working directory IS the sandbox.
    extra_env["FAKE_FETCH_PLAN"] = "plan.txt"
    return sandbox.run("Fetch the benchmark corpus", extra_env)


def _requested(calls: list[str], corpus: str) -> bool:
    """Whether one of the step's calls asked for that corpus, by selector."""
    return any(line.endswith(f"--corpus {corpus}") for line in calls)


def test_the_fetch_step_degrades_and_still_exits_zero_when_a_host_never_answers(
    tmp_path: Path,
) -> None:
    """The step must warn and pass, and must still attempt the other artifact.

    The defect this pins, exactly as CI hit it: the primary's 522 failed the
    step under `set -e`, so the secondary was never attempted and a CDN in
    another country decided the build was red.

    This FAILS against the old step for two independent reasons: the old run
    block has no warning at all, and it exits 1 on the same stub.
    """
    result = _run_fetch_step(tmp_path, "3 0")

    assert result.returncode == 0, (
        "an unanswered host must not fail the build:\n" + result.output
    )
    assert "::warning" in result.output, result.output
    assert "the primary corpus could not be fetched" in result.output, result.output
    assert _fetch_corpus.PRIMARY[1] in result.output, (
        "the warning must name the URL that did not answer:\n" + result.output
    )
    assert "HTTP Error 522" in result.output, (
        "the warning must name the HTTP error:\n" + result.output
    )
    assert "the primary corpus is ABSENT in this run" in result.output, result.output
    assert "SKIPS instead of running" in result.output, (
        "the warning must name the consequence:\n" + result.output
    )
    assert _requested(result.calls, "primary"), result.calls
    assert _requested(result.calls, "secondary"), (
        "the secondary must still be attempted when the primary fails:\n"
        + str(result.calls)
    )
    # And the summary must not claim everything arrived.
    assert "every requested artifact is present" not in result.output, result.output
    assert "fetch_corpus: DEGRADED:" in result.output, result.output


def test_the_fetch_step_degrades_when_only_the_secondary_is_unreachable(
    tmp_path: Path,
) -> None:
    """The other direction: a working primary is reported as verified.

    The primary matters far more than the secondary, and a degraded secondary
    must not be reported as if the primary had failed.
    """
    result = _run_fetch_step(tmp_path, "0 3")

    assert result.returncode == 0, result.output
    assert "::warning" in result.output, result.output
    assert "the secondary corpus could not be fetched" in result.output, result.output
    assert "the secondary corpus is ABSENT in this run" in result.output, result.output
    assert "the primary corpus is present and its sha256 matches" in result.output, (
        result.output
    )
    assert _requested(result.calls, "primary") and _requested(result.calls, "secondary")


def test_the_fetch_step_warns_once_per_artifact_when_neither_host_answers(
    tmp_path: Path,
) -> None:
    """Both artifacts degrade independently, and both are reported."""
    result = _run_fetch_step(tmp_path, "3 3")

    assert result.returncode == 0, result.output
    assert "the primary corpus is ABSENT in this run" in result.output, result.output
    assert "the secondary corpus is ABSENT in this run" in result.output, result.output
    assert result.output.count("::warning") == 2, result.output
    assert result.output.count("fetch_corpus: DEGRADED:") == 1, result.output
    assert "every requested artifact is present" not in result.output, result.output


def test_the_fetch_step_fails_hard_on_a_checksum_mismatch_and_still_attempts_both(
    tmp_path: Path,
) -> None:
    """The hard outcome stays hard, and does not stop the other attempt.

    A checksum mismatch is the case that must NOT be softened: the step must
    fail on it. It must also still attempt the secondary, because the primary
    being the wrong artifact says nothing about the secondary.
    """
    result = _run_fetch_step(tmp_path, "1 0")

    assert result.returncode != 0, (
        "a corpus that is not the recorded one must fail the step:\n" + result.output
    )
    assert "::error" in result.output, result.output
    assert "not the recorded artifact" in result.output, result.output
    assert _requested(result.calls, "primary"), result.calls
    assert _requested(result.calls, "secondary"), (
        "a hard failure of the primary must not prevent the secondary's attempt:\n"
        + str(result.calls)
    )


def test_the_fetch_step_still_attempts_the_primary_when_the_secondary_fails_hard(
    tmp_path: Path,
) -> None:
    """The reverse direction of the same rule."""
    result = _run_fetch_step(tmp_path, "0 1")

    assert result.returncode != 0, result.output
    assert "the primary corpus is present and its sha256 matches" in result.output, (
        result.output
    )
    assert "The secondary corpus is not the recorded artifact" in result.output, (
        result.output
    )
    assert _requested(result.calls, "primary") and _requested(result.calls, "secondary")


def test_the_fetch_step_passes_with_no_warning_when_both_artifacts_verify(
    tmp_path: Path,
) -> None:
    """A step that warns about everything is not a check either."""
    result = _run_fetch_step(tmp_path, "0 0")

    assert result.returncode == 0, result.output
    assert "::warning" not in result.output, result.output
    assert "::error" not in result.output, result.output
    assert "every requested artifact is present and its sha256 matches" in result.output


def test_the_pytest_step_reports_the_skips_and_names_the_corpus_ones(
    tmp_path: Path,
) -> None:
    """A skipped test must not be invisible in a green build.

    The canned run below is the real pytest short-summary format, and the
    counts are what make the report a report: 15 tests skipped, 14 of them
    because a corpus file is missing, and one -- the opt-in live-LLM test --
    that is NOT corpus-dependent and must not be counted as if it were.
    """
    sandbox = _start_sandbox(tmp_path, _POETRY_PYTEST_STUB)
    (sandbox.staging / "pytest-output.txt").write_text(
        "SKIPPED [10] apps\\api\\tests\\test_retrieval.py:761: corpus file missing: civil-code-ra386-icnl.pdf\n"
        "SKIPPED [4] apps\\api\\tests\\test_retrieval.py:797: corpus file missing: civil-code-ra386-icnl.pdf\n"
        "SKIPPED [1] apps\\api\\tests\\test_generation.py:1270: live local LLM test: set TUKLAS_LIVE_LLM=1 to run it\n"
        "232 passed, 15 skipped, 6 warnings in 55.00s\n",
        encoding="utf-8",
        newline="\n",
    )
    (sandbox.staging / "pytest-status.txt").write_text("0\n", encoding="utf-8")

    result = sandbox.run("pytest (apps/api)", {})

    assert result.returncode == 0, result.output
    assert "pytest: 15 test(s) skipped in this run, 14 of them because the corpus is absent." in result.output, result.output
    assert "::warning" in result.output, result.output
    assert "The corpus-dependent tests did not run" in result.output, result.output
    assert any("-rs" in line for line in result.calls), (
        "the step must ask pytest for the skip reasons:\n" + str(result.calls)
    )


def test_the_pytest_step_does_not_call_a_non_corpus_skip_a_corpus_skip(
    tmp_path: Path,
) -> None:
    """The discrimination, from the other side.

    The suite has skips that have nothing to do with the corpus (the opt-in
    live-LLM test). Reporting those as corpus-dependent would make the warning
    noise, and a warning that fires on a healthy run is a warning nobody
    reads.
    """
    sandbox = _start_sandbox(tmp_path, _POETRY_PYTEST_STUB)
    (sandbox.staging / "pytest-output.txt").write_text(
        "SKIPPED [1] apps\\api\\tests\\test_generation.py:1270: live local LLM test: set TUKLAS_LIVE_LLM=1 to run it\n"
        "246 passed, 1 skipped in 55.00s\n",
        encoding="utf-8",
        newline="\n",
    )
    (sandbox.staging / "pytest-status.txt").write_text("0\n", encoding="utf-8")

    result = sandbox.run("pytest (apps/api)", {})

    assert result.returncode == 0, result.output
    assert "pytest: 1 test(s) skipped in this run, 0 of them because the corpus is absent." in result.output, result.output
    assert "no skipped test needed the corpus" in result.output, result.output
    assert "::warning" not in result.output, result.output


def test_the_pytest_step_still_reports_the_skips_when_the_suite_is_red(
    tmp_path: Path,
) -> None:
    """The report must survive a failing suite, and the gate must not move.

    The step exits with pytest's own status, so a red suite stays red; the
    difference is that the log also says how much of the suite did not run.
    """
    sandbox = _start_sandbox(tmp_path, _POETRY_PYTEST_STUB)
    (sandbox.staging / "pytest-output.txt").write_text(
        "FAILED apps\\api\\tests\\test_retrieval.py:100: assert 0\n"
        "SKIPPED [4] apps\\api\\tests\\test_retrieval.py:797: corpus file missing: civil-code-ra386-icnl.pdf\n"
        "1 failed, 245 passed, 4 skipped in 55.00s\n",
        encoding="utf-8",
        newline="\n",
    )
    (sandbox.staging / "pytest-status.txt").write_text("1\n", encoding="utf-8")

    result = sandbox.run("pytest (apps/api)", {})

    assert result.returncode == 1, (
        "the step must keep pytest's status:\n" + result.output
    )
    assert "pytest: 4 test(s) skipped in this run, 4 of them because the corpus is absent." in result.output, result.output
    assert "::warning" in result.output, result.output
