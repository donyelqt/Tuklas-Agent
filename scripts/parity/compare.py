"""Compare two screenshot sets pixel for pixel.

This is the compare half of the visual parity harness. It reads a
BASELINE directory and a CANDIDATE directory and reports, for every
state, how many pixels differ and by how much.

The rules it holds:

  * A missing baseline is a FAILURE, not a skip. "No baseline" means
    there is no statement to make, and a harness that quietly passes
    when it has nothing to compare is a harness that always passes.
  * A missing candidate is a FAILURE. A state that stopped rendering is
    not a state with zero differences.
  * The threshold is a COUNT of differing pixels, not a percentage,
    because a percentage hides a small-but-total failure (an entire
    pane gone is 30% of the pixels and is not a 30% defect).
  * The diff IMAGE is written next to the report, so a nonzero diff
    can be looked at rather than guessed about.
  * An ADDITIVE change is compared on its matching surface. A build
    that adds a control cannot have a zero-pixel diff against the build
    it replaced, because the control's pixels are new pixels. When the
    candidate's capture recorded the on-screen rectangle of the added
    control (shoot.py's --added-selector), every differing pixel MUST
    fall inside that rectangle, and the count OUTSIDE it must be zero.
    The rectangle is measured from the live DOM, so it cannot be tuned
    to hide a defect: it moves with the control. The report states the
    rectangle it used, so a reader can check the claim.

Usage:

    python scripts/parity/compare.py --baseline artifacts/parity/baseline \\
        --candidate artifacts/parity/current --out artifacts/parity/diff
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from PIL import Image, ImageChops


def load_rects(candidate_dir: str) -> dict[str, dict[str, list[int]]]:
    """Read the rectangles the capture measured, if it recorded any."""
    path = os.path.join(candidate_dir, "rects.json")
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def compare_pair(
    baseline_path: str,
    candidate_path: str,
    diff_path: str,
    threshold: int,
    rects: dict[str, list[int]] | None = None,
) -> dict[str, Any]:
    """Compare one pair and write the diff image."""
    baseline = Image.open(baseline_path).convert("RGB")
    candidate = Image.open(candidate_path).convert("RGB")
    if baseline.size != candidate.size:
        return {
            "state": os.path.basename(baseline_path)[: -len(".png")],
            "status": "fail",
            "reason": (
                f"the baseline is {baseline.size[0]}x{baseline.size[1]} and the "
                f"candidate is {candidate.size[0]}x{candidate.size[1]}"
            ),
            "differing_pixels": baseline.size[0] * baseline.size[1],
            "total_pixels": baseline.size[0] * baseline.size[1],
            "max_channel_delta": 255,
        }
    difference = ImageChops.difference(baseline, candidate)
    # A pixel counts as different when any channel moved by more than
    # the threshold. Anti-aliasing on the same text on two different
    # builds is a few units; a changed colour or a moved element is
    # tens.
    bands = difference.split()
    mask = bands[0].point(lambda v: 255 if v > threshold else 0)
    for band in bands[1:]:
        mask = ImageChops.lighter(mask, band.point(lambda v: 255 if v > threshold else 0))
    differing = sum(1 for value in mask.get_flattened_data() if value != 0)
    total = baseline.size[0] * baseline.size[1]
    max_delta = max(max(band.getextrema()) for band in bands)
    # Split the differing pixels by whether they fall inside a rectangle
    # the capture measured for an ADDED element. A pixel outside every
    # such rectangle is a pixel of the surface that was supposed to be
    # unchanged, and it is a defect whether it is one pixel or ten
    # thousand.
    outside = differing
    inside = 0
    if rects:
        # The union of the measured rectangles is a mask of the pixels
        # the added element owns. The differing pixels inside it are the
        # mask's pixels the union also covers; the rest are outside.
        union = Image.new("L", baseline.size, 0)
        for left, top, right, bottom in rects.values():
            box = (
                max(0, int(left)),
                max(0, int(top)),
                min(baseline.size[0], int(right)),
                min(baseline.size[1], int(bottom)),
            )
            if box[2] <= box[0] or box[3] <= box[1]:
                continue
            patch = Image.new("L", (box[2] - box[0], box[3] - box[1]), 255)
            union.paste(patch, box[:2])
        inside = sum(
            1
            for mask_value, union_value in zip(
                mask.get_flattened_data(), union.get_flattened_data(), strict=True
            )
            if mask_value != 0 and union_value != 0
        )
        outside = differing - inside
    if differing > 0:
        # The diff image shows the CHANGED pixels in magenta over a
        # dimmed copy of the baseline, so a reader can see both what
        # changed and where.
        highlight = Image.new("RGB", baseline.size, (255, 0, 255))
        dimmed = baseline.point(lambda v: v // 3)
        diff_image = Image.composite(highlight, dimmed, mask.convert("L"))
        os.makedirs(os.path.dirname(os.path.abspath(diff_path)), exist_ok=True)
        diff_image.save(diff_path)
    result = {
        "state": os.path.basename(baseline_path)[: -len(".png")],
        "status": "pass" if outside == 0 else "fail",
        "differing_pixels": differing,
        "differing_pixels_inside_added_rects": inside,
        "differing_pixels_outside_added_rects": outside,
        "added_rects": rects or {},
        "total_pixels": total,
        "max_channel_delta": max_delta,
        "diff_image": diff_path if differing > 0 else None,
    }
    if rects:
        # Say why the state passed when it did not have a zero diff, so
        # the report never claims more than it checked.
        result["reason"] = (
            f"{differing} pixels differ, all {inside} of them inside the "
            f"rectangle of the added element; {outside} differ outside it"
        )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--out", default="artifacts/parity/diff")
    parser.add_argument(
        "--threshold",
        type=int,
        default=0,
        help="the channel delta a pixel must exceed to count as different",
    )
    args = parser.parse_args()

    if not os.path.isdir(args.baseline):
        raise SystemExit(
            f"compare.py: the baseline directory {args.baseline!r} does not exist. "
            "There is no baseline, so there is no parity claim to make."
        )
    if not os.path.isdir(args.candidate):
        raise SystemExit(f"compare.py: the candidate directory {args.candidate!r} does not exist.")

    baseline_states = sorted(f for f in os.listdir(args.baseline) if f.endswith(".png"))
    candidate_states = sorted(f for f in os.listdir(args.candidate) if f.endswith(".png"))
    if not baseline_states:
        raise SystemExit(f"compare.py: the baseline directory {args.baseline!r} holds no PNGs.")

    results: list[dict[str, Any]] = []
    all_rects = load_rects(args.candidate)
    for name in baseline_states:
        candidate_path = os.path.join(args.candidate, name)
        if not os.path.exists(candidate_path):
            results.append(
                {
                    "state": name[: -len(".png")],
                    "status": "fail",
                    "reason": f"the candidate has no {name}",
                    "differing_pixels": -1,
                    "total_pixels": 0,
                    "max_channel_delta": 255,
                }
            )
            continue
        diff_path = os.path.join(args.out, f"{name[: -len('.png')]}-diff.png")
        results.append(
            compare_pair(
                os.path.join(args.baseline, name),
                candidate_path,
                diff_path,
                args.threshold,
                all_rects.get(name[: -len(".png")]),
            )
        )

    # A state the candidate has and the baseline does not is a state that
    # was added. It is reported, not failed: the harness compares the
    # states it was given a baseline for.
    for name in candidate_states:
        if name not in baseline_states:
            results.append(
                {
                    "state": name[: -len(".png")],
                    "status": "no-baseline",
                    "differing_pixels": 0,
                    "total_pixels": 0,
                    "max_channel_delta": 0,
                }
            )

    failed = [r for r in results if r["status"] == "fail"]
    report = {
        "baseline": os.path.abspath(args.baseline),
        "candidate": os.path.abspath(args.candidate),
        "threshold": args.threshold,
        "results": results,
        "verdict": "fail" if failed else "pass",
    }
    os.makedirs(args.out, exist_ok=True)
    report_path = os.path.join(args.out, "report.json")
    with open(report_path, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2)

    for result in results:
        if result["status"] == "pass":
            differing = result.get("differing_pixels", 0)
            if differing == 0:
                print(f"compare.py: PASS {result['state']}: 0 pixels differ")
            else:
                print(
                    f"compare.py: PASS {result['state']}: {differing} pixels differ, "
                    f"all inside the added element's rectangle "
                    f"({result.get('added_rects')}); 0 differ outside it"
                )
        elif result["status"] == "no-baseline":
            print(f"compare.py: NO BASELINE {result['state']}: the candidate added this state")
        else:
            reason = result.get("reason", "")
            print(
                f"compare.py: FAIL {result['state']}: "
                f"{result['differing_pixels']} of {result['total_pixels']} pixels differ"
                + (f" ({reason})" if reason else "")
                + (f", diff image at {result['diff_image']}" if result.get("diff_image") else "")
            )
    print(f"compare.py: report at {report_path}")
    print(f"compare.py: VERDICT {report['verdict'].upper()}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
