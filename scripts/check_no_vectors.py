#!/usr/bin/env python3
"""Vectorless quarantine + agent-thinness gate (CONSTRAINTS.md floor).

Run from the repo root:  python scripts/check_no_vectors.py

Exit 0 + "check_no_vectors: OK (n files scanned)" when clean;
exit 1 + one line per violation (`path:line: rule`) otherwise.

Pure stdlib; exits before scanning if the repo layout is absent.

What it enforces (CONSTRAINTS.md "Floor", architecture.md sec 2):

1. The primary path -- apps/api/{ingestion,retrieval,agent,generation,api}/ --
   imports no package on the dense-vector deny list, and no vector machinery
   either. The deny list is the five named packages; the machinery list is the
   runtime the dense arm is actually built on, so a vector implementation
   cannot enter the primary path wearing a different hat.
2. `cosine_similarity` (and a bare `cosine(` call) does not appear in
   apps/api/retrieval/.
3. apps/api/agent/ imports no sqlite3, no sqlalchemy, and no apps.api.models:
   the agent reaches nodes only through the seven tools.
4. experiments/vector/ is imported by exactly one place, apps/api/evaluation/.
   Nothing else in the repository may import it -- not apps/api/retrieval/,
   not apps/api/api/, not scripts/.

The import rules are checked on the PARSED import statements, not on raw text,
so a docstring that names a package is not a violation, and a dynamic import
that names one is. The cosine rule is a text rule, because CONSTRAINTS.md
states it as a grep rule.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

# CONSTRAINTS.md floor — do not weaken. Exact dotted paths per architecture.md §2.
DENY_PRIMARY = {"sentence_transformers", "faiss", "chromadb", "qdrant_client", "pgvector"}

#: The vector machinery the dense arm is built on (ADR-002: fastembed 0.9.0 +
#: onnxruntime, 384-dim embeddings; numpy for the vector arithmetic).
#:
#: These three are NOT in CONSTRAINTS.md's named deny list, so this is a
#: TIGHTENING of the floor, and a tightening is silent under the file's own
#: rule ("Tightening is silent; loosening requires human approval").
#:
#: Why the tightening is the same rule and not a new one: the named deny list
#: names the LIBRARIES, and it is the libraries that carry vector
#: implementations, not the ban itself that is the intent. The intent is that
#: the vectorless arm contains no vector machinery. A `fastembed` import in
#: apps/api/retrieval/ is that machinery, whatever it is called. `numpy` is
#: the same case one step removed: a dot product over two normalised vectors
#: is a cosine similarity, and the existing grep rule for `cosine_similarity`
#: cannot see it, because numpy spells it `np.dot(a, b) / (norm(a) * norm(b))`.
#: So banning numpy in the quarantined directories closes the hole the grep
#: rule leaves open, rather than introducing a new policy.
VECTOR_MACHINERY = {"fastembed", "onnxruntime", "numpy"}

#: The primary path. CONSTRAINTS.md names five directories; AGENTS.md names
#: four. CONSTRAINTS.md is the binding file, so apps/api/api/ is included.
QUARANTINE_DIRS = (
    "apps/api/ingestion/",
    "apps/api/retrieval/",
    "apps/api/agent/",
    "apps/api/generation/",
    "apps/api/api/",
)

#: The dense baseline: the only place in the repository that may import the
#: embedding runtime itself.
DENSE_ARM_DIR = "experiments/vector/"

#: The sole legal importer of experiments/vector/ (architecture.md sec 2).
EVALUATION_DIR = "apps/api/evaluation/"

#: The dotted module path of the quarantined dense baseline.
EXPERIMENTS_VECTOR_MODULE = "experiments.vector"

AGENT_THINNESS = {"sqlite3", "sqlalchemy", "apps.api.models"}

#: `cosine_similarity` or a bare `cosine(` call. architecture.md sec 2 states
#: the grep rule verbatim.
COSINE_RE = re.compile(r"cosine_similarity|cosine\s*\(")

#: A dynamic import that names a module in a string literal.
DYNAMIC_IMPORT_RE = re.compile(
    r"""(?:import_module|__import__)\s*\(\s*["']([A-Za-z_][\w\.]*)["']"""
)

SKIP_DIRS = {
    "__pycache__",
    ".venv",
    "venv",
    "node_modules",
    ".next",
    "out",
    ".git",
    ".kilo",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".poetry-venv",
    ".tmp_refs",
    "htmlcov",
}


class Unreadable(Exception):
    """A file the scanner could not parse or read."""

    def __init__(self, path: Path, exc: Exception) -> None:
        super().__init__(str(exc))
        self.path = path
        self.exc = exc


def iter_py_files(root: Path) -> list[Path]:
    """Every .py file under `root`, skipping build and cache directories.

    A directory whose name starts with a dot is skipped: those are the
    local environments, tool caches and editor directories, and scanning
    a venv's site-packages would report the third-party packages this
    rule is about rather than the repository's own source.
    """
    files: list[Path] = []
    if not root.is_dir():
        return files
    stack = [root]
    while stack:
        current = stack.pop()
        for entry in sorted(current.iterdir()):
            if entry.is_dir():
                if entry.name in SKIP_DIRS or entry.name.startswith("."):
                    continue
                stack.append(entry)
            elif entry.is_file() and entry.suffix == ".py":
                files.append(entry)
    return files


def _module_root(name: str) -> str:
    """The top-level package of a dotted module name."""
    return name.split(".", 1)[0]


def imported_modules(path: Path) -> list[tuple[int, str]]:
    """The modules a file imports, as (lineno, dotted name) pairs.

    Both the static statements and the dynamic ones are listed, because
    `importlib.import_module("faiss")` is an import of faiss. A relative
    import is reported with a leading dot, because it can only reach a
    sibling package and the caller decides which one that is.
    """
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise Unreadable(path, exc) from exc
    try:
        tree = ast.parse(source, filename=path.as_posix())
    except SyntaxError as exc:
        raise Unreadable(path, exc) from exc

    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.append((node.lineno, alias.name))
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                found.append((node.lineno, "." * node.level + (node.module or "")))
            elif node.module:
                found.append((node.lineno, node.module))
    for lineno, match in enumerate(DYNAMIC_IMPORT_RE.finditer(source), start=1):
        found.append((lineno, match.group(1)))
    return found


def _is_under(rel: str, prefix: str) -> bool:
    return rel.startswith(prefix)


def _imports_experiments_vector(dotted: str) -> bool:
    """True when the dotted module name reaches experiments/vector.

    `import experiments.vector.embeddings` and `from experiments.vector import x`
    both name the package directly. A relative import inside the package
    itself is a sibling import and is not a leak of the package; the caller
    decides that by path.
    """
    return dotted == EXPERIMENTS_VECTOR_MODULE or dotted.startswith(
        EXPERIMENTS_VECTOR_MODULE + "."
    )


def check_file(path: Path, rel: str) -> list[str]:
    """The import rules that apply to one file."""
    violations: list[str] = []
    try:
        imports = imported_modules(path)
    except Unreadable as exc:
        return [f"{rel}:0: unreadable-file ({exc.exc})"]

    quarantined = any(_is_under(rel, prefix) for prefix in QUARANTINE_DIRS)

    for lineno, dotted in imports:
        if dotted.startswith("."):
            # A relative import. It cannot name an absolute third-party
            # package, so none of the absolute rules apply to it.
            continue
        root = _module_root(dotted)

        if quarantined:
            if root in DENY_PRIMARY:
                violations.append(
                    f"{rel}:{lineno}: deny-list import '{root}' (vectorless quarantine)"
                )
            elif root in VECTOR_MACHINERY:
                violations.append(
                    f"{rel}:{lineno}: vector-machinery import '{root}' (vectorless quarantine)"
                )
            if _is_under(rel, "apps/api/agent/") and dotted in AGENT_THINNESS:
                violations.append(f"{rel}:{lineno}: agent thinness import '{dotted}'")

        if _imports_experiments_vector(dotted):
            if _is_under(rel, EVALUATION_DIR) or _is_under(rel, DENSE_ARM_DIR):
                continue
            violations.append(
                f"{rel}:{lineno}: experiments/vector imported outside {EVALUATION_DIR}"
            )

    return violations


def check_cosine_text(path: Path, rel: str) -> list[str]:
    """The cosine grep rule over apps/api/retrieval/ (CONSTRAINTS.md)."""
    if not _is_under(rel, "apps/api/retrieval/"):
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return [f"{rel}:0: unreadable-file ({exc})"]
    violations: list[str] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if COSINE_RE.search(line):
            violations.append(f"{rel}:{lineno}: cosine-similarity grep rule (apps/api/retrieval)")
    return violations


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    if not (repo_root / "apps" / "api").is_dir():
        print("check_no_vectors: repo layout not found (run from the repo root)", file=sys.stderr)
        return 2

    violations: list[str] = []
    scanned: set[Path] = set()

    # One pass over the whole repository. The per-file rules are selected by
    # path, so the same pass covers the primary path, the dense arm, the
    # evaluation harness, and scripts/ alike.
    for path in iter_py_files(repo_root):
        scanned.add(path)
        rel = path.relative_to(repo_root).as_posix()
        violations.extend(check_file(path, rel))
        violations.extend(check_cosine_text(path, rel))

    if violations:
        for line in violations:
            print(line, file=sys.stderr)
        print(
            f"check_no_vectors: FAIL ({len(violations)} violation(s), {len(scanned)} files scanned)"
        )
        return 1
    print(f"check_no_vectors: OK ({len(scanned)} files scanned)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
