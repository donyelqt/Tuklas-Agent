# The visual parity harness

Three scripts, and one rule they exist to enforce: **the explorer renders
the same pixels as the build it replaced, except for the pixels the new
control itself occupies.**

## Why a harness and not a look

A screenshot is only comparable against another screenshot if the data
behind it is the same. A real API serves whatever the corpus holds and
whatever the model produced, so two runs of the same code would differ
for reasons that have nothing to do with the interface. The stub serves
fixed responses, so a pixel diff is a statement about the interface and
nothing else.

The capture drives a real Chrome over the DevTools Protocol. A DOM
snapshot cannot see a clipped input, a collided flex row, or a colour
token that resolves to the wrong value, and those are exactly the
defects a visual parity check exists to catch.

## The three scripts

| Script | What it does |
|---|---|
| `stub_api.py` | Serves the five routes `docs/api.md` §3 freezes, with fixed data, plus the frozen SSE event sequence. Two runs against this stub produce the same pixels. |
| `shoot.py` | Drives Chrome over the DevTools Protocol and writes one PNG per named state. Pins the viewport, the device scale factor, the colour scheme, the animation clock, the fonts and the scroll position, so the only thing that can move a pixel is the code. |
| `compare.py` | Compares two screenshot sets pixel for pixel and writes `report.json` plus a diff image. |

## Running it

The API and the two builds have to be served first. The static export
writes `app.html`, so the route is `/app.html`, not `/app`:

```bash
# 1. the stub API
python scripts/parity/stub_api.py --port 8000

# 2. the baseline build, on its own port
#    BASELINE_DIR is a worktree checked out at the commit the baseline was
#    captured from - c9852a2 for the committed baseline.
cd "$BASELINE_DIR/apps/web" && npm run build
cd out && python -m http.server 3102 --bind 127.0.0.1

# 3. the candidate build, on its own port
cd apps/web && npm run build
npm run start -- --port 3103

# 4. capture the candidate
python scripts/parity/shoot.py --url http://127.0.0.1:3103/app.html \
    --out artifacts/parity/current --added-selector '[role="tablist"]'

# 5. compare
python scripts/parity/compare.py --baseline artifacts/parity/baseline \
    --candidate artifacts/parity/current --out artifacts/parity/diff
```

`npm run start` serves the export; `python -m http.server` works too, but
it does not map the extensionless `/app` route onto `app.html`, and the
capture asks for `/app.html` either way.

The baseline was captured from the `tuklas-baseline` worktree at `c9852a2`.
Re-capturing it there and comparing it against the committed PNGs gives 0
differing pixels, which is what makes the committed PNGs a reference
rather than a snapshot of one machine.

## What the verdict means

`compare.py` exits 0 only when **every** state with a baseline has **zero
differing pixels outside the added element's rectangle**.

An additive feature cannot have a zero-pixel diff against the build it
replaced: the control's pixels are new pixels. So the claim is made
precisely rather than not at all:

* `shoot.py --added-selector` measures the control's on-screen rectangle
  from the **live DOM**, in the same frame the screenshot is taken, and
  writes it to `rects.json`;
* `compare.py` splits the differing pixels by whether they fall inside
  that rectangle, and requires the count outside it to be zero;
* the report states the rectangle it used and the two counts, so a
  reader can check the claim rather than take it.

The rectangle is measured, not hand-written. It cannot be tuned to hide
a defect, because it moves with the control. Both failure modes are
verified:

* a defect **outside** the rectangle (20 changed pixels at x=300) fails
  with `20 differ outside it`;
* the control **moved** 10px from the rectangle the DOM measured fails
  with `240 differ outside it`.

## The rules it holds

* A missing baseline is a FAILURE, not a skip. "No baseline" means there
  is no statement to make, and a harness that quietly passes when it has
  nothing to compare is a harness that always passes.
* A missing candidate is a FAILURE. A state that stopped rendering is
  not a state with zero differences.
* The threshold is a COUNT of differing pixels, not a percentage,
  because a percentage hides a small-but-total failure.
* The diff IMAGE is written next to the report, so a nonzero diff can be
  looked at rather than guessed about.
* A state the candidate has and the baseline does not is reported as
  `no-baseline`, not failed: the harness compares the states it was
  given a baseline for, and `chat-idle` and `chat-answer` are new
  states with nothing to compare against yet.

## What is tracked

`artifacts/parity/baseline/` is tracked. It is the committed reference:
the pixels the explorer is supposed to match. `artifacts/parity/current/`
and `artifacts/parity/diff/` are generated and gitignored; they are
evidence of a run, not input to one.
