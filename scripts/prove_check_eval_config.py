#!/usr/bin/env python3
"""Prove scripts/check_eval_config.py fails on each deliberate violation.

Every case writes a temporary config, runs the checker, asserts the exit
code is 1 and the expected rule fired, and then removes the file. The tree
is left exactly as it was found.

Run from the repo root:  python scripts/prove_check_eval_config.py
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
CHECKER = REPO_ROOT / "scripts" / "check_eval_config.py"
EXAMPLE = REPO_ROOT / "scripts" / "fixtures" / "example-run-config.json"
TMP_DIR = REPO_ROOT / "scripts" / "fixtures" / ".proof_tmp"


def _run(path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CHECKER), str(path)],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=False,
    )


def _base() -> dict[str, Any]:
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


def _per_arm(model_b: str = "qwen2.5:3b") -> dict[str, Any]:
    """A config that names a generation block per arm, in the `arms` object shape."""
    run = _base()
    run.pop("config_id", None)
    run["arms"] = {
        "tuklas": {
            "model": "qwen2.5:3b",
            "backend": "ollama",
            "temperature": 0,
            "seed": 42,
        },
        "vector": {
            "model": model_b,
            "backend": "ollama",
            "temperature": 0,
            "seed": 42,
        },
    }
    return run


#: (label, config builder, substring the checker must print).
CASES: list[tuple[str, Any, str]] = [
    (
        "arms answer with different models",
        lambda: _per_arm(model_b="llama3.1:8b"),
        "arms.vector.model: 'llama3.1:8b' differs from tuklas",
    ),
    (
        "arms use different backends",
        lambda: _mutate_per_arm(backend_b="vllm"),
        "arms.vector.backend: 'vllm' differs from tuklas",
    ),
    (
        "arms use different seeds",
        lambda: _mutate_per_arm(seed_b=7),
        "arms.vector.seed: 7 differs from tuklas",
    ),
    (
        "an arm runs at a non-zero temperature",
        lambda: _mutate_per_arm(temperature_b=0.7),
        "arms.vector.temperature: 0.7 is not 0",
    ),
    (
        "the recorded run-level model is not the model an arm used",
        lambda: _mutate_shared(model="llama3.1:8b"),
        "config.model: 'llama3.1:8b' differs from arms.tuklas",
    ),
    (
        "the shared temperature is not 0",
        lambda: _mutate_shared(temperature=0.2),
        "config.temperature: 0.2 is not 0",
    ),
    (
        "the declared config_id does not match the config",
        lambda: _mutate_shared(config_id="cfg_deadbeefdeadbeef"),
        "config_id: 'cfg_deadbeefdeadbeef' does not match",
    ),
    (
        "the weights are missing",
        lambda: _mutate_shared(remove="weights"),
        "config.weights: missing",
    ),
    (
        "a weight is outside the legal range",
        lambda: _mutate_weight("lex", 3.5),
        "config.weights.lex: 3.5 is outside the legal range",
    ),
    (
        "the judge prompt version is missing",
        lambda: _mutate_shared(remove="judge_prompt_version"),
        "config.judge_prompt_version: missing",
    ),
    (
        "the fuzzy threshold is out of range",
        lambda: _mutate_shared(fuzzy_threshold=99),
        "config.fuzzy_threshold: 99 is outside [80, 95]",
    ),
    (
        "the stoplist version is missing",
        lambda: _mutate_shared(remove="stoplist_version"),
        "config.stoplist_version: missing",
    ),
    (
        "the margins are missing",
        lambda: _mutate_shared(remove="margins"),
        "config.margins: missing",
    ),
    (
        "the dataset snapshot is missing",
        lambda: _mutate_top(remove="dataset_snapshot"),
        "dataset_snapshot: missing",
    ),
    (
        "K does not include the primary metric 5",
        lambda: _mutate_top(k=[1, 3, 10]),
        "k: 5 is missing",
    ),
    (
        "the backend is not one of the three allowed",
        lambda: _mutate_shared(backend="openai"),
        "config.backend: 'openai' is not one of",
    ),
]


def _mutate_shared(
    *,
    temperature: float | None = None,
    config_id: str | None = None,
    remove: str | None = None,
    fuzzy_threshold: int | None = None,
    backend: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    run = _base()
    if remove is not None:
        run["config"].pop(remove, None)
    if temperature is not None:
        run["config"]["temperature"] = temperature
    if fuzzy_threshold is not None:
        run["config"]["fuzzy_threshold"] = fuzzy_threshold
    if backend is not None:
        run["config"]["backend"] = backend
    if model is not None:
        run["config"]["model"] = model
    if config_id is not None:
        run["config_id"] = config_id
    return run


def _mutate_weight(name: str, value: float) -> dict[str, Any]:
    run = _base()
    run["config"]["weights"][name] = value
    return run


def _mutate_top(*, remove: str | None = None, k: list[int] | None = None) -> dict[str, Any]:
    run = _base()
    if remove is not None:
        run.pop(remove, None)
    if k is not None:
        run["k"] = k
    return run


def _mutate_per_arm(
    *,
    backend_b: str | None = None,
    seed_b: int | None = None,
    temperature_b: float | None = None,
) -> dict[str, Any]:
    run = _per_arm()
    if backend_b is not None:
        run["arms"]["vector"]["backend"] = backend_b
    if seed_b is not None:
        run["arms"]["vector"]["seed"] = seed_b
    if temperature_b is not None:
        run["arms"]["vector"]["temperature"] = temperature_b
    return run


def main() -> int:
    baseline = _run(EXAMPLE)
    if baseline.returncode != 0:
        print("prove_check_eval_config: the example config does not pass the checker")
        print(baseline.stdout, baseline.stderr)
        return 1
    print(f"example config: exit 0 -> {baseline.stdout.strip()}")

    TMP_DIR.mkdir(parents=True, exist_ok=True)
    failures: list[str] = []
    try:
        for index, (label, build, expected) in enumerate(CASES):
            config = build()
            target = TMP_DIR / f"case_{index:02d}.json"
            target.write_text(json.dumps(config, indent=2), encoding="utf-8")
            try:
                result = _run(target)
            finally:
                target.unlink(missing_ok=True)
            combined = result.stdout + result.stderr
            ok = result.returncode == 1 and expected in combined
            if ok:
                fired = next(line for line in combined.splitlines() if expected in line)
                print(f"PASS  {label}: exit 1, printed {fired.strip()}")
            else:
                failures.append(label)
                print(f"FAIL  {label}: exit {result.returncode}, expected {expected!r}")
                print(combined)
    finally:
        if TMP_DIR.is_dir():
            for leftover in TMP_DIR.iterdir():
                leftover.unlink(missing_ok=True)
            TMP_DIR.rmdir()

    if failures:
        print(f"prove_check_eval_config: FAIL ({len(failures)} case(s))", file=sys.stderr)
        return 1
    print(f"prove_check_eval_config: OK ({len(CASES)} violation(s) caught, tree restored)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
