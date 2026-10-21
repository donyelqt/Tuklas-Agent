#!/usr/bin/env python3
"""Fetch the benchmark corpus into data/documents/ and verify its checksums.

The corpus PDFs are gitignored (ADR-002): they are third-party artifacts, not
source, and they never leave the machine. A CI runner has no corpus, so this
script is how it gets one.

Usage (from the repo root):
    python -m scripts.fetch_corpus

The URL and the expected sha256 of each artifact are the ones
`docs/CORPUS.md` records. The checksum is verified after the download and a
mismatch is a hard failure: a corpus that does not match the recorded
checksum is not the corpus the frozen dataset was built against, and every
gold node id in it would be suspect.

Only the primary corpus is required. The secondary (U.S. Code Title 11) is
fetched too when `--secondary` is passed; the reproducibility check and the
golden 10Q need only the primary.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCUMENTS = REPO_ROOT / "data" / "documents"

#: (filename, url, sha256) -- the artifacts and checksums from docs/CORPUS.md.
PRIMARY = (
    "civil-code-ra386-icnl.pdf",
    "https://www.icnl.org/wp-content/uploads/civilcode1949.pdf",
    "cdb52f8d2b1c8fbb04daf7949025a6dda6faa1fe91ae5248eceb75cafb451a3a",
)

SECONDARY = (
    "uscode-2023-title11.pdf",
    "https://www.govinfo.gov/content/pkg/USCODE-2023-title11/pdf/USCODE-2023-title11.pdf",
    "5a66d81044b5909739f1f232bcaa610ea02dbdc2eff6a1b5e12aa903a445cfa4",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def fetch(name: str, url: str, expected: str) -> bool:
    """Fetch one artifact and verify its checksum. True on success."""
    target = DOCUMENTS / name
    if target.exists():
        actual = _sha256(target)
        if actual == expected:
            print(f"present: {name} (sha256 matches)")
            return True
        print(f"stale: {name} has sha256 {actual}, expected {expected}; refetching")
        target.unlink()

    DOCUMENTS.mkdir(parents=True, exist_ok=True)
    print(f"fetching: {url}")
    try:
        with urllib.request.urlopen(url, timeout=300) as response:
            payload = response.read()
    except OSError as exc:
        print(f"fetch_corpus: cannot fetch {name}: {exc}", file=sys.stderr)
        return False

    target.write_bytes(payload)
    actual = _sha256(target)
    if actual != expected:
        target.unlink(missing_ok=True)
        print(
            f"fetch_corpus: {name} has sha256 {actual}, expected {expected}; "
            "the artifact was deleted rather than left in place",
            file=sys.stderr,
        )
        return False
    print(f"fetched: {name} (sha256 matches)")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--secondary",
        action="store_true",
        help="also fetch the secondary corpus (U.S. Code Title 11)",
    )
    args = parser.parse_args()

    if not fetch(*PRIMARY):
        return 1
    if args.secondary and not fetch(*SECONDARY):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
