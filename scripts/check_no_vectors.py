#!/usr/bin/env python3
"""Vectorless quarantine + agent-thinness gate (CONSTRAINTS.md floor).

Run from the repo root:  python scripts/check_no_vectors.py

Exit 0 + "check_no_vectors: OK (n files scanned)" when clean;
exit 1 + one line per violation (`path:line: rule`) otherwise.

Pure stdlib; exits before scanning if the repo layout is absent.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# CONSTRAINTS.md floor — do not weaken. Exact dotted paths per architecture.md §2.
DENY_PRIMARY = {"sentence_transformers", "faiss", "chromadb", "qdrant_client", "pgvector"}

DENY_PRIMARY_RE = re.compile(
    r"^\s*(?:from|import)\s+(" + "|".join(sorted(DENY_PRIMARY)) + r")\b"
)
COSINE_RE = re.compile(r"cosine_similarity|cosine\s*\(")
AGENT_THINNESS_RE = re.compile(r"^\s*(?:from|import)\s+(sqlite3|sqlalchemy|apps\.api\.models)\b")
EXPERIMENTS_VECTOR_RE = re.compile(r"experiments\.vector|experiments/vector")

#: (directory relative to repo root, rules that apply to every .py under it, recursive)
SCAN_ROOTS: dict[str, tuple[re.Pattern[str], ...]] = {
    "apps/api/ingestion": (DENY_PRIMARY_RE,),
    "apps/api/retrieval": (DENY_PRIMARY_RE, COSINE_RE),
    "apps/api/agent": (DENY_PRIMARY_RE, AGENT_THINNESS_RE),
    "apps/api/generation": (DENY_PRIMARY_RE,),
    "apps/api/api": (DENY_PRIMARY_RE,),
}
#: directories allowed to import experiments/vector (evaluation is the sole legal importer)
EXPERIMENTS_ALLOWED_PREFIXES = ("apps/api/evaluation/",)

SKIP_DIRS = {"__pycache__", ".venv"}


def iter_py_files(root: Path) -> list[Path]:
    files: list[Path] = []
    stack = [root]
    while stack:
        current = stack.pop()
        if not current.is_dir():
            continue
        for entry in sorted(current.iterdir()):
            if entry.is_dir():
                if entry.name not in SKIP_DIRS:
                    stack.append(entry)
            elif entry.is_file() and entry.suffix == ".py":
                files.append(entry)
    return files


def check_file(path: Path, rules: tuple[re.Pattern[str], ...]) -> list[str]:
    violations: list[str] = []
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return [f"{path.as_posix()}:0: unreadable-file ({exc})"]
    for lineno, line in enumerate(text.splitlines(), start=1):
        for rule in rules:
            if rule.search(line):
                violations.append(f"{path.as_posix()}:{lineno}: {rule.pattern}")
    return violations


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    violations: list[str] = []
    scanned: set[Path] = set()

    for rel_dir, rules in sorted(SCAN_ROOTS.items()):
        for path in iter_py_files(repo_root / rel_dir):
            scanned.add(path)
            violations.extend(check_file(path, rules))

    # experiments/vector/ is importable ONLY from apps/api/evaluation/. Check every
    # apps/api/*.py (including roots above) for stray experiments/vector imports.
    for path in iter_py_files(repo_root / "apps" / "api"):
        scanned.add(path)
        if path.as_posix().startswith(EXPERIMENTS_ALLOWED_PREFIXES):
            continue
        violations.extend(check_file(path, (EXPERIMENTS_VECTOR_RE,)))

    if violations:
        for line in violations:
            print(line, file=sys.stderr)
        print(f"check_no_vectors: FAIL ({len(violations)} violation(s), {len(scanned)} files scanned)")
        return 1
    print(f"check_no_vectors: OK ({len(scanned)} files scanned)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
