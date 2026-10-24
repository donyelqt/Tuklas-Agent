"""Screenshot the explorer in a real browser, deterministically.

This is the capture half of the visual parity harness. It drives a real
Chrome over the DevTools Protocol and writes PNG files, one per named
STATE, so two builds can be compared pixel for pixel.

Why a real browser and not a DOM snapshot: the thing under test is the
RENDERED interface. A DOM snapshot cannot see a clipped input, a
collided flex row, or a colour token that resolves to the wrong value,
and those are exactly the defects a visual parity check exists to catch.

Why Chrome over the protocol and not Playwright: the harness must run
from the pinned toolchain without adding a browser download to it. The
protocol is what Playwright drives anyway, and Chrome is already
installed.

Determinism is the whole point, so the driver pins everything that
would otherwise vary between two runs:

  * the viewport, the device scale factor and the color scheme;
  * the animation clock, by disabling CSS animations and transitions;
  * the fonts, by waiting for `document.fonts.ready` before capturing;
  * the network, by driving a LOCAL stub API that serves fixed data;
  * the scroll position, by scrolling to the top before capturing.

It also records, per state, the on-screen rectangle of any element that
the CANDIDATE added and the BASELINE did not have. An additive feature
cannot have a zero-pixel diff against the build it replaced; what it CAN
have is a diff of exactly the pixels the new control occupies. The
rectangle is MEASURED from the live DOM, not hand-written, so it cannot
be tuned to hide a defect: if the new control moved, or if something
else changed near it, the measurement moves with it and the comparison
outside the rectangle stays at zero.

Usage:

    python scripts/parity/shoot.py --url http://127.0.0.1:3002/app \\
        --out artifacts/parity/current --states explorer-idle explorer-answer
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from typing import Any

import websockets

CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]


def find_chrome() -> str:
    """The browser to drive, or a hard failure naming what was looked for."""
    for candidate in CHROME_CANDIDATES:
        if os.path.exists(candidate):
            return candidate
    raise SystemExit(
        "shoot.py: no Chrome or Edge found. Looked for:\n  " + "\n  ".join(CHROME_CANDIDATES)
    )


class Cdp:
    """A minimal DevTools Protocol client over a WebSocket.

    The protocol is request/response over one socket. The browser also
    sends unsolicited events, which are skipped, so one outstanding
    command at a time is enough.
    """

    def __init__(self) -> None:
        self._ws: websockets.ClientConnection | None = None
        self._id = 0

    async def connect(self, url: str) -> None:
        self._ws = await websockets.connect(url, max_size=None)

    async def send(self, method: str, params: dict | None = None) -> dict:
        self._id += 1
        message_id = self._id
        assert self._ws is not None
        await self._ws.send(
            json.dumps({"id": message_id, "method": method, "params": params or {}})
        )
        while True:
            raw = await self._ws.recv()
            message = json.loads(raw)
            if message.get("id") == message_id:
                if "error" in message:
                    raise RuntimeError(f"{method} failed: {message['error']}")
                return message.get("result", {})

    async def close(self) -> None:
        if self._ws is not None:
            await self._ws.close()


async def evaluate(cdp: Cdp, expression: str, await_promise: bool = False) -> object:
    """Run a JS expression in the page and return its JSON value."""
    result = await cdp.send(
        "Runtime.evaluate",
        {
            "expression": expression,
            "returnByValue": True,
            "awaitPromise": await_promise,
        },
    )
    if result.get("exceptionDetails"):
        raise RuntimeError(f"page threw: {result['exceptionDetails']}")
    return result.get("result", {}).get("value")


async def wait_for(
    cdp: Cdp, expression: str, timeout: float = 20.0, label: str = "condition"
) -> None:
    """Poll a JS expression until it is truthy, or fail with what it was."""
    deadline = time.monotonic() + timeout
    last: object = None
    while time.monotonic() < deadline:
        last = await evaluate(cdp, expression)
        if last:
            return
        await asyncio.sleep(0.1)
    raise SystemExit(f"shoot.py: timed out waiting for {label}; last value was {last!r}")


# The states the harness captures. Each is (name, setup JS). The setup runs
# after the page has loaded and the fonts are ready, so it can rely on the
# DOM being present.
#
# The selectors address the panes by their `aria-labelledby` target, which
# every build has had since the explorer shipped. A selector that only
# exists in the NEW build would make the baseline uncapturable, and an
# uncapturable baseline means there is no parity claim to make.
STATES: dict[str, str] = {
    "explorer-idle": """
        (() => {
          window.scrollTo(0, 0);
          return true;
        })()
    """,
    "explorer-node-selected": """
        (() => {
          const pane = document.querySelector('section[aria-labelledby=tree-pane-heading]');
          if (!pane) return false;
          const button = [...pane.querySelectorAll('button')]
            .find((b) => b.innerText.includes('Article 3'));
          if (!button) return false;
          button.click();
          window.scrollTo(0, 0);
          return true;
        })()
    """,
    "explorer-answer": """
        (() => {
          const input = document.querySelector('#ask-question');
          if (!input) return false;
          const setter = Object.getOwnPropertyDescriptor(
            window.HTMLInputElement.prototype, 'value').set;
          setter.call(input, 'What does Article 3 say?');
          input.dispatchEvent(new Event('input', { bubbles: true }));
          input.closest('form').requestSubmit();
          return true;
        })()
    """,
    "chat-idle": """
        (() => {
          const tab = document.querySelector('#view-tab-chat');
          if (!tab) return false;
          tab.click();
          window.scrollTo(0, 0);
          return true;
        })()
    """,
    "chat-answer": """
        (() => {
          const tab = document.querySelector('#view-tab-chat');
          if (!tab) return false;
          tab.click();
          const input = document.querySelector('#chat-question');
          if (!input) return false;
          const setter = Object.getOwnPropertyDescriptor(
            window.HTMLInputElement.prototype, 'value').set;
          setter.call(input, 'What does Article 3 say?');
          input.dispatchEvent(new Event('input', { bubbles: true }));
          input.closest('form').requestSubmit();
          return true;
        })()
    """,
}


async def wait_for_target(port: int, timeout: float = 30.0) -> str:
    """Wait for Chrome's debugger and return the page's WebSocket URL."""
    deadline = time.monotonic() + timeout
    last_error = ""
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/json/list", timeout=2
            ) as response:
                targets = json.loads(response.read().decode("utf-8"))
            for target in targets:
                if target.get("type") == "page" and target.get("webSocketDebuggerUrl"):
                    return str(target["webSocketDebuggerUrl"])
            last_error = "no page target yet"
        except Exception as error:  # the retry loop needs the reason
            last_error = str(error)
        await asyncio.sleep(0.2)
    raise SystemExit(f"shoot.py: Chrome's debugger never answered: {last_error}")


async def measure_rects(cdp: Cdp, args: argparse.Namespace, state: str) -> dict[str, list[int]]:
    """Measure the on-screen rectangle of each added element.

    The rectangle is read from the live layout, so it is the truth about
    where the new control actually landed, not an assumption about
    where it should have landed.
    """
    measured: dict[str, list[int]] = {}
    for selector in args.added_selector:
        rect = await evaluate(
            cdp,
            f"""
            (() => {{
              const el = document.querySelector({json.dumps(selector)});
              if (!el) return null;
              const r = el.getBoundingClientRect();
              return [
                Math.round(r.left),
                Math.round(r.top),
                Math.round(r.right),
                Math.round(r.bottom),
              ];
            }})()
            """,
        )
        if rect is None:
            raise SystemExit(
                f"shoot.py: the added selector {selector!r} matched nothing in "
                f"state {state!r}. An added element that is not on screen "
                "cannot be excluded from the comparison."
            )
        measured[selector] = list(rect)
    return measured


async def capture_state(cdp: Cdp, args: argparse.Namespace, state: str) -> None:
    """Load the page, put it into the state, and write the PNG."""
    if state not in STATES:
        raise SystemExit(f"shoot.py: unknown state {state!r}. Known: {sorted(STATES)}")
    await cdp.send("Page.enable")
    await cdp.send("Runtime.enable")
    await cdp.send("Page.navigate", {"url": args.url})
    await wait_for(
        cdp,
        "document.readyState === 'complete'",
        timeout=30.0,
        label="the page to load",
    )
    await wait_for(
        cdp,
        "document.querySelector('section[aria-labelledby=tree-pane-heading]') !== null",
        timeout=30.0,
        label="the explorer to mount",
    )
    # The fonts change the metrics, so a capture before they are ready
    # would compare one render against a different one.
    await evaluate(cdp, "document.fonts.ready.then(() => true)", await_promise=True)
    await wait_for(
        cdp,
        "document.fonts.status === 'loaded'",
        timeout=30.0,
        label="the fonts to load",
    )
    # The tree arrives over the network. A state setup that clicks a tree
    # node cannot run before the tree is populated, and the failure would
    # look like a missing node rather than a missing wait.
    if state.startswith("explorer"):
        await wait_for(
            cdp,
            "document.querySelector('section[aria-labelledby=tree-pane-heading]')"
            ".innerText.includes('Article 3')",
            timeout=30.0,
            label="the tree to load",
        )
    if not await evaluate(cdp, STATES[state]):
        raise SystemExit(f"shoot.py: could not put the page into state {state!r}")
    # The state change starts fetches and a stream. Each state names the
    # thing that proves it has settled.
    if state == "explorer-node-selected":
        await wait_for(
            cdp,
            "document.body.innerText.includes('Article 3. The ignorance')",
            timeout=30.0,
            label="the source text",
        )
    elif state in {"explorer-answer", "chat-answer"}:
        await wait_for(
            cdp,
            "document.body.innerText.includes('CITATIONS')",
            timeout=30.0,
            label="the answer",
        )
    await asyncio.sleep(args.settle)
    await evaluate(cdp, "window.scrollTo(0, 0)")
    await asyncio.sleep(0.1)
    # The rectangle is measured AFTER the scroll settles, because the
    # screenshot is taken in the same frame: a rectangle measured at a
    # different scroll position would not be the rectangle the diff
    # pixels are in.
    rects = await measure_rects(cdp, args, state)
    shot = await cdp.send(
        "Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False}
    )
    path = os.path.join(args.out, f"{state}.png")
    with open(path, "wb") as handle:
        handle.write(base64.b64decode(shot["data"]))
    print(f"shoot.py: wrote {path}", flush=True)
    if rects:
        rects_path = os.path.join(args.out, "rects.json")
        existing: dict[str, Any] = {}
        if os.path.exists(rects_path):
            with open(rects_path, encoding="utf-8") as handle:
                existing = json.load(handle)
        existing[state] = rects
        with open(rects_path, "w", encoding="utf-8") as handle:
            json.dump(existing, handle, indent=2, sort_keys=True)
        print(f"shoot.py: wrote {rects_path}", flush=True)


async def run(args: argparse.Namespace) -> int:
    """Capture every requested state and write the PNGs."""
    profile = tempfile.mkdtemp(prefix="parity-chrome-")
    chrome = find_chrome()
    process = subprocess.Popen(
        [
            chrome,
            f"--remote-debugging-port={args.debug_port}",
            f"--user-data-dir={profile}",
            "--headless=new",
            "--disable-gpu",
            "--hide-scrollbars",
            "--force-device-scale-factor=1",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-extensions",
            "--disable-background-networking",
            "--disable-lcd-text",
            "--font-render-hinting=none",
            f"--window-size={args.width},{args.height}",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        target = await wait_for_target(args.debug_port)
        cdp = Cdp()
        try:
            await cdp.connect(target)
            await cdp.send(
                "Emulation.setDeviceMetricsOverride",
                {
                    "width": args.width,
                    "height": args.height,
                    "deviceScaleFactor": 1,
                    "mobile": False,
                },
            )
            await cdp.send(
                "Emulation.setEmulatedMedia",
                {"features": [{"name": "prefers-reduced-motion", "value": "reduce"}]},
            )
            # The animation clock is the largest source of run-to-run
            # pixel noise. Everything that can animate is pinned off.
            await cdp.send(
                "Page.addScriptToEvaluateOnNewDocument",
                {
                    "source": """
                        const style = document.createElement('style');
                        style.textContent = `
                          *, *::before, *::after {
                            animation-duration: 0s !important;
                            animation-delay: 0s !important;
                            transition-duration: 0s !important;
                            transition-delay: 0s !important;
                            caret-color: transparent !important;
                          }
                        `;
                        document.addEventListener('DOMContentLoaded', () => {
                          document.head.appendChild(style);
                        });
                    """,
                },
            )
            os.makedirs(args.out, exist_ok=True)
            for state in args.states:
                await capture_state(cdp, args, state)
        finally:
            await cdp.close()
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
        shutil.rmtree(profile, ignore_errors=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:3002/app")
    parser.add_argument("--out", default="artifacts/parity/current")
    parser.add_argument("--states", nargs="+", default=sorted(STATES))
    parser.add_argument("--width", type=int, default=1440)
    parser.add_argument("--height", type=int, default=900)
    parser.add_argument("--settle", type=float, default=0.6)
    parser.add_argument("--debug-port", type=int, default=9333)
    parser.add_argument(
        "--added-selector",
        action="append",
        default=[],
        help=(
            "a CSS selector for an element the candidate ADDED and the "
            "baseline did not have. Its on-screen rectangle is measured "
            "from the live DOM and written to rects.json, so the compare "
            "half can require every differing pixel to fall inside it. "
            "May be given more than once."
        ),
    )
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
