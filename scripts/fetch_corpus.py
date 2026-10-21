#!/usr/bin/env python3
"""Fetch the benchmark corpus into data/documents/ and verify its checksums.

The corpus PDFs are gitignored (ADR-002): they are third-party artifacts, not
source, and they never leave the machine. A CI runner has no corpus, so this
script is how it gets one.

Usage (from the repo root):
    python -m scripts.fetch_corpus                    # every artifact
    python -m scripts.fetch_corpus --corpus primary   # only the Civil Code

The URL and the expected sha256 of each artifact are the ones
`docs/CORPUS.md` records. The checksum is verified after the download and a
mismatch is a hard failure: a corpus that does not match the recorded
checksum is not the corpus the frozen dataset was built against, and every
gold node id in it would be suspect.

A download that never arrives and a download that arrives with the wrong
bytes are DIFFERENT outcomes, and they get different exit statuses so the
caller can treat them differently:

    0  every requested artifact is present and its sha256 matches
    1  hard: the host ANSWERED, and the bytes are not the recorded artifact
    3  soft: the host did not answer, after the retries below

3 is soft because the hosts are third parties. `www.icnl.org` answers through
Cloudflare, and a 522 is that CDN reporting that the ORIGIN timed out, which
is a statement about the host and not about the artifact. The corpus is not
needed to run any gate on this repository's code either: the corpus-dependent
tests skip themselves, with the reason in their own skip message, when the
file is absent. A third-party outage is therefore a degraded run, and what a
degraded run means to a build is the caller's decision, not this script's.

A RETRY is never applied to the checksum. Bytes that arrived are either the
recorded artifact or a hard failure; asking again would only fetch the same
wrong bytes more slowly.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCUMENTS = REPO_ROOT / "data" / "documents"

#: The three outcomes, as exit statuses. They are a contract with the CI
#: step, which tolerates 3 (a degraded run) and fails on 1 (a corpus that is
#: not the recorded one). 2 is left alone: argparse exits with it on a usage
#: error, which is neither of these.
OK = 0
INTEGRITY_FAILURE = 1
UNREACHABLE = 3

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

#: The artifacts by the name `--corpus` selects them with, in the order they
#: are fetched when all are asked for. The primary (Civil Code) is first
#: because it is the artifact the retrieval tests need; the secondary (U.S.
#: Code Title 11) is exercised by one dataset test that wants both.
ARTIFACTS: dict[str, tuple[str, str, str]] = {
    "primary": PRIMARY,
    "secondary": SECONDARY,
}

#: How long to wait before each retry, in seconds. The number of entries IS
#: the number of retries, so the first request is followed by at most four
#: more (5 attempts in total). The delays grow because a host that is
#: already timing out is not helped by being asked again immediately: the
#: first wait covers a blip, the last covers an origin having a bad
#: afternoon, and by then the run should degrade rather than stall.
RETRY_DELAYS = (5, 15, 45, 90)

#: Seconds one attempt may take before it is abandoned. This multiplies with
#: the retries, so a host that accepts the connection and then never answers
#: must not be able to spend the whole CI job's timeout: five attempts at 300
#: seconds would be 25 minutes on their own, which would turn the degradation
#: path itself into a failed build. A minute is generous for a single PDF of
#: a few megabytes, and an attempt that has produced no bytes in a minute is
#: the unreachable case this script exists to survive.
FETCH_TIMEOUT = 60

#: The HTTP statuses that mean "not now". 408/425/429 are the timeout and
#: rate-limit family, 5xx are the server's own trouble, and 522/523/524 are
#: Cloudflare's "the origin did not answer" family -- the one CI actually
#: hit. A 403 is deliberately NOT here: the ICNL CDN answers an unidentified
#: client with it on purpose (docs/CORPUS.md), and four more requests would
#: get four more identical answers. A status the host chose to send is not
#: made different by asking again, so those report UNREACHABLE immediately.
TRANSIENT_HTTP_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504, 522, 523, 524})

#: The User-Agent the fetches are sent with.
#:
#: urllib sends NO User-Agent of its own, and the ICNL host answers that
#: (and the "Python-urllib/3.x" default) with a 403, while every other
#: User-Agent is served the file. The corpus PDFs are behind a CDN that
#: refuses unidentified clients, so a fetch without a User-Agent is a hard
#: 403 on a CI runner and the corpus never arrives. Naming a client is what
#: every other HTTP client does, and the checksum check below is what
#: actually guarantees the artifact is the one docs/CORPUS.md records.
USER_AGENT = "tuklas-agent-corpus-fetcher/1.0 (+https://github.com/donyelqt/Tuklas-Agent)"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _is_transient(exc: OSError) -> bool:
    """Whether asking again has any chance of a different answer.

    An HTTPError is the host's own verdict, so only the statuses that mean
    "not now" are worth retrying. Anything else that lands here is a
    URLError (DNS, refused connection, reset, read timeout) or an OSError
    from the socket: a failure of the HOST or of the path to it rather than
    of the request, and exactly the case a retry is for.
    """
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in TRANSIENT_HTTP_STATUS
    return True


def fetch(name: str, url: str, expected: str) -> int:
    """Fetch one artifact and verify its checksum, returning one of the three statuses.

    The bytes are verified BEFORE they are written, so the file on disk is
    never a good artifact mixed with a bad download, and the retry loop only
    ever wraps the request: a response that arrives is checked once.
    """
    target = DOCUMENTS / name
    if target.exists():
        actual = _sha256(target)
        if actual == expected:
            print(f"present: {name} (sha256 matches)")
            return OK
        print(f"stale: {name} has sha256 {actual}, expected {expected}; refetching")
        target.unlink()

    DOCUMENTS.mkdir(parents=True, exist_ok=True)

    attempts = len(RETRY_DELAYS) + 1
    failure = ""
    for attempt in range(1, attempts + 1):
        print(f"fetching: {url} (attempt {attempt}/{attempts})")
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT) as response:
                payload = response.read()
        except OSError as exc:
            failure = str(exc) or exc.__class__.__name__
            if attempt == attempts or not _is_transient(exc):
                break
            delay = RETRY_DELAYS[attempt - 1]
            print(
                f"fetch_corpus: {name} attempt {attempt}/{attempts} failed: {failure}; "
                f"retrying in {delay}s",
                file=sys.stderr,
            )
            time.sleep(delay)
            continue

        # The host answered, so there is nothing left to retry: the bytes are
        # either the recorded artifact or they are not, and this is the case
        # that must stay hard. A corpus that is not the one docs/CORPUS.md
        # records invalidates every gold node id.
        actual = hashlib.sha256(payload).hexdigest()
        if actual != expected:
            print(
                f"fetch_corpus: {name} has sha256 {actual}, expected {expected}; "
                "the bytes were NOT written, because this is not the artifact "
                "docs/CORPUS.md records",
                file=sys.stderr,
            )
            return INTEGRITY_FAILURE

        target.write_bytes(payload)
        print(f"fetched: {name} (sha256 matches)")
        return OK

    # The one line the CI step greps for to build its warning. It names the
    # artifact, the URL and the error, because those are what a reader needs
    # to tell a host outage from a broken download.
    print(
        f"fetch_corpus: UNREACHABLE: {name}: {url}: {failure} (after {attempts} attempts)",
        file=sys.stderr,
    )
    return UNREACHABLE


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--corpus",
        choices=(*ARTIFACTS, "all"),
        default="all",
        help=(
            "which artifact to fetch: one of the named corpora, or all of them "
            "(the default). Each name can be asked for on its own, so a caller "
            "that wants the primary's outcome kept apart from the secondary's "
            "makes one call per name."
        ),
    )
    args = parser.parse_args()

    names = tuple(ARTIFACTS) if args.corpus == "all" else (args.corpus,)

    # Every named artifact is attempted, whatever the one before it did: the
    # primary matters more than the secondary, and neither one's outcome is a
    # reason to skip the other's attempt. The worst outcome decides the exit
    # status, and an integrity failure outranks an unreachable host -- a
    # corpus that is not the recorded one is a fact about the corpus, while
    # an unreachable host is a fact about the network.
    hard_failure = False
    unreachable = False
    for name in names:
        status = fetch(*ARTIFACTS[name])
        if status == INTEGRITY_FAILURE:
            hard_failure = True
        elif status == UNREACHABLE:
            unreachable = True

    if hard_failure:
        return INTEGRITY_FAILURE
    if unreachable:
        return UNREACHABLE
    return OK


if __name__ == "__main__":
    raise SystemExit(main())
