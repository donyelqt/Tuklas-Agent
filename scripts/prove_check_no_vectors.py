#!/usr/bin/env python3
"""Prove scripts/check_no_vectors.py fails on each deliberate violation.

Every case writes a temporary file into a quarantined path, runs the
scanner, asserts the exit code is 1 and the expected rule fired, and then
removes the file. The tree is left exactly as it was found.

Run from the repo root:  python scripts/prove_check_no_vectors.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCANNER = REPO_ROOT / "scripts" / "check_no_vectors.py"

#: (label, relative path of the temp file, file body, substring the scanner
#: must print for the case to count as proven).
CASES: list[tuple[str, str, str, str]] = [
    (
        "deny-list import in apps/api/retrieval/",
        "apps/api/retrieval/_violation_probe.py",
        "import faiss\n",
        "deny-list import 'faiss'",
    ),
    (
        "deny-list import in apps/api/api/",
        "apps/api/api/_violation_probe.py",
        "import chromadb\n",
        "deny-list import 'chromadb'",
    ),
    (
        "deny-list import in apps/api/ingestion/",
        "apps/api/ingestion/_violation_probe.py",
        "from sentence_transformers import SentenceTransformer\n",
        "deny-list import 'sentence_transformers'",
    ),
    (
        "vector machinery (fastembed) in apps/api/agent/",
        "apps/api/agent/_violation_probe.py",
        "import fastembed\n",
        "vector-machinery import 'fastembed'",
    ),
    (
        "vector machinery (numpy) in apps/api/retrieval/",
        "apps/api/retrieval/_violation_probe.py",
        "import numpy as np\n",
        "vector-machinery import 'numpy'",
    ),
    (
        "cosine_similarity in apps/api/retrieval/",
        "apps/api/retrieval/_violation_probe.py",
        "def cosine_similarity(a, b):\n    return 0.0\n",
        "cosine-similarity grep rule",
    ),
    (
        "agent thinness (sqlite3) in apps/api/agent/",
        "apps/api/agent/_violation_probe.py",
        "import sqlite3\n",
        "agent thinness import 'sqlite3'",
    ),
    (
        "agent thinness (apps.api.models) in apps/api/agent/",
        "apps/api/agent/_violation_probe.py",
        "from apps.api.models import schemas\n",
        "agent thinness import 'apps.api.models'",
    ),
    (
        "experiments/vector imported from apps/api/retrieval/",
        "apps/api/retrieval/_violation_probe.py",
        "from experiments.vector import dense\n",
        "experiments/vector imported outside apps/api/evaluation/",
    ),
    (
        "experiments/vector imported from apps/api/api/",
        "apps/api/api/_violation_probe.py",
        "import experiments.vector.dense\n",
        "experiments/vector imported outside apps/api/evaluation/",
    ),
    (
        "experiments/vector imported from scripts/",
        "scripts/_violation_probe.py",
        "from experiments.vector import dense\n",
        "experiments/vector imported outside apps/api/evaluation/",
    ),
    (
        "dynamic import of a deny-listed package in apps/api/generation/",
        "apps/api/generation/_violation_probe.py",
        "import importlib\n\nmod = importlib.import_module('qdrant_client')\n",
        "deny-list import 'qdrant_client'",
    ),
]


def _run() -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCANNER)],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=False,
    )


def main() -> int:
    baseline = _run()
    if baseline.returncode != 0:
        print("prove_check_no_vectors: the clean tree does not pass the scanner", file=sys.stderr)
        print(baseline.stdout, file=sys.stderr)
        print(baseline.stderr, file=sys.stderr)
        return 1
    print(f"clean tree: exit 0 -> {baseline.stdout.strip()}")

    failures: list[str] = []
    for label, rel, body, expected in CASES:
        target = REPO_ROOT / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
        try:
            result = _run()
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

    # The tree must be clean again.
    after = _run()
    if after.returncode != 0:
        failures.append("tree not clean after the probes")
        print("FAIL  the tree is not clean after the probes")
        print(after.stdout, after.stderr)
    else:
        print(f"tree after the probes: exit 0 -> {after.stdout.strip()}")

    if failures:
        print(f"prove_check_no_vectors: FAIL ({len(failures)} case(s))", file=sys.stderr)
        return 1
    print(f"prove_check_no_vectors: OK ({len(CASES)} violation(s) caught, tree restored)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
