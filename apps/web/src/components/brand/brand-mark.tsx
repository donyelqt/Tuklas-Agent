"use client";

import { useId } from "react";

/**
 * Tuklas Agent brand mark.
 *
 * A root bar, a stem, a spread, and two leaves. It is a document tree reduced
 * to its minimum: a root level, a descent, a branch, and a leaf. The right
 * leaf is the cited provision, which is what the product returns.
 *
 * The name helps. `tuklas` is Tagalog for discovery, and the tool discovers
 * provisions inside hierarchical legal documents, so a mark that reads as
 * "root to cited leaf" is the argument rather than decoration.
 *
 * Construction follows the reference language this page is built on. That mark
 * is a 64-unit grid rendered at 22px, built from straight bars, and filled with
 * a three-stop gradient running from magenta through violet to cyan. Its two
 * gradient endpoints are byte-identical to this page's `accent` and `accent-2`
 * tokens, so the two marks are already the same palette.
 *
 * Deliberately not a llama. Two llama marks already appear on this page in the
 * backends strip, and a third would read as a third vendor rather than as the
 * product.
 *
 * Four primitives, not five.
 *
 * A first pass used five bars including a horizontal spread. Rendered at
 * 20px that read as a coloured smudge rather than as a shape, because a
 * 64-unit grid leaves each bar about two pixels wide. Dropping the spread and
 * thickening what remains leaves three distinguishable masses at nav size: a
 * bar, a stem, and two blocks.
 */
const ROOT = { x: 6, y: 13, width: 52, height: 11 };
const STEM = { x: 28, y: 24, width: 8, height: 10 };
const LEAF_LEFT = { x: 11, y: 34, width: 17, height: 17 };
const LEAF_RIGHT = { x: 36, y: 34, width: 17, height: 17 };

export function BrandMark({
  className,
  size = 22,
}: {
  className?: string;
  size?: number;
}): React.JSX.Element {
  // Unique per instance: the mark is rendered more than once per page (the
  // nav, the hero, and the document explorer's header), and a shared id would
  // make one gradient definition win over the other.
  const gradientId = useId();

  return (
    <svg
      viewBox="0 0 64 64"
      width={size}
      height={size}
      aria-hidden="true"
      focusable="false"
      className={className}
    >
      <defs>
        <linearGradient
          id={gradientId}
          x1={8}
          y1={12}
          x2={56}
          y2={52}
          gradientUnits="userSpaceOnUse"
        >
          <stop offset="0" stopColor="oklch(0.7 0.24 340)" />
          <stop offset="0.5" stopColor="oklch(0.62 0.21 295)" />
          <stop offset="1" stopColor="oklch(0.81 0.14 200)" />
        </linearGradient>
      </defs>
      <g fill={`url(#${gradientId})`}>
        <rect {...ROOT} />
        <rect {...STEM} />
        <rect {...LEAF_LEFT} />
        <rect {...LEAF_RIGHT} />
      </g>
    </svg>
  );
}