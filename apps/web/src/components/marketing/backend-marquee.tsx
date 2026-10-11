'use client';

import { useState } from "react";
import { Pause, Play } from "@phosphor-icons/react/ssr";
import { BackendMark } from "@/components/marketing/backend-marks";
import { backends } from "@/lib/copy";

const LABEL =
  "font-mono text-[11px] uppercase tracking-[0.18em] text-ink-7";

const FOCUS =
  "focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2";

/**
 * Copies of the logo group in the scrolling track.
 *
 * The runtimes list is three 24px marks at 32px spacing with a 32px trailing
 * pad: 3 x 24 + 2 x 32 + 32 = 168px. The models list adds a 1px seam rule and
 * 32px of padding, then four marks: 1 + 32 + 4 x 24 + 3 x 32 + 32 = 225px.
 * One group is therefore 393px.
 *
 * Seven copies give a 2751px track, wider than the 2560px display this page
 * is built to survive, so the animation steps exactly one group width
 * (`-100% / 7`) and the seam is never inside the viewport at that width.
 */
const GROUPS = 7;

/**
 * Runtimes first, then the models they serve, with a rule between them.
 *
 * The two kinds are not interchangeable. A mark in the first run is a runtime
 * the product targets directly; a mark in the second is an example of
 * something a runtime can serve. Read as one undifferentiated run of seven
 * logos they blur together, so they are separate sibling lists and the seam
 * between them is a drawn line, not spacing that happens to fall there.
 */
const MARK_GROUPS = [
  { key: "runtimes", items: backends.backends },
  { key: "models", items: backends.models },
];

const ALL_MARKS = [...backends.backends, ...backends.models];

/**
 * The one marquee on the page.
 *
 * Accessibility decisions that are load-bearing here:
 *
 * - The scrolling track is `aria-hidden` and a visually hidden list carries the
 *   backend names once. The names are the content; hiding them from assistive
 *   technology and exposing only the strip label left a screen reader with a
 *   sentence and no list.
 * - There is a real pause control. `focus-within` could never fire on this
 *   track, because it contained no focusable element, so the previous pause
 *   behaviour was reachable by pointer only and failed WCAG 2.2.2.
 * - Under reduced motion the track stops and collapses to one centred group,
 *   because a static strip showing twenty-four repeated logos is noise.
 */
export function BackendMarquee(): React.JSX.Element {
  const [paused, setPaused] = useState(false);

  return (
    <section
      id="backends"
      aria-labelledby="backends-label"
      className="scroll-mt-24 border-y border-ink-4/60 bg-ink-1 py-14"
    >
      <style>{`
        @keyframes marquee-scroll {
          from {
            transform: translateX(0);
          }
          to {
            transform: translateX(calc(-100% / var(--groups)));
          }
        }
        .marquee-track {
          animation: marquee-scroll 48s linear infinite;
        }
        .marquee-track[data-paused="true"],
        .marquee-viewport:hover .marquee-track,
        .marquee-viewport:focus-within .marquee-track {
          animation-play-state: paused;
        }
        @media (prefers-reduced-motion: reduce) {
          .marquee-track {
            animation: none;
            width: 100%;
            justify-content: center;
          }
          .marquee-group:not(:first-child) {
            display: none;
          }
          .marquee-toggle {
            display: none;
          }
        }
      `}</style>

      <div className="mx-auto flex max-w-7xl flex-col items-center gap-4 px-4 sm:px-6 lg:flex-row lg:justify-center lg:gap-5 lg:px-8">
        <p id="backends-label" className={LABEL}>
          {backends.label}
        </p>

        <button
          type="button"
          onClick={() => setPaused((value) => !value)}
          aria-pressed={paused}
          className={`marquee-toggle inline-flex size-11 shrink-0 items-center justify-center border border-ink-4/60 text-ink-7 transition-[background-color,border-color,color] duration-150 ease-[cubic-bezier(.4,0,.2,1)] hover:border-ink-4 hover:bg-ink-2 hover:text-ink-9 active:translate-y-px ${FOCUS}`}
        >
          <span className="sr-only">
            {paused ? "Resume the backend strip" : "Pause the backend strip"}
          </span>
          <span aria-hidden="true" className="contents">
            {paused ? <Play size={16} weight="fill" /> : <Pause size={16} weight="fill" />}
          </span>
        </button>
      </div>

      {/* The real list, once, for assistive technology. */}
      <ul className="sr-only">
        {ALL_MARKS.map((item) => (
          <li key={item.slug}>{item.name}</li>
        ))}
      </ul>

      <div className="marquee-viewport mt-8 h-10 overflow-hidden">
        <div
          className="marquee-track flex w-max"
          data-paused={paused}
          style={{ "--groups": GROUPS } as React.CSSProperties}
          aria-hidden="true"
        >
          {Array.from({ length: GROUPS }, (_, group) => (
            <div key={group} className="marquee-group flex shrink-0 items-center">
              {MARK_GROUPS.map((markGroup) => (
                <ul
                  key={markGroup.key}
                  className={`flex shrink-0 items-center gap-8 pr-8 ${
                    markGroup.key === "models"
                      ? "border-l border-ink-4/40 pl-8"
                      : ""
                  }`}
                >
                  {markGroup.items.map((item) => (
                    <li
                      key={item.slug}
                      className="flex items-center justify-center text-ink-7"
                    >
                      <BackendMark slug={item.slug} />
                    </li>
                  ))}
                </ul>
              ))}
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}