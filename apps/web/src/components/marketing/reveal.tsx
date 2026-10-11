'use client';

import { useEffect, useRef } from "react";
import type * as React from "react";

/** Stable hook for browser verification. Exactly one element carries it per instance. */
export const REVEAL_TEST_ID = "reveal";

/** Upper bound for a stagger delay. Anything larger is ignored rather than honored. */
const MAX_DELAY_MS = 400;

const HIDDEN_CLASSES = [
  "motion-safe:opacity-0",
  "motion-safe:translate-y-[12px]",
] as const;
const VISIBLE_CLASSES = ["opacity-100", "translate-y-0"] as const;

function clampDelay(delayMs: number | undefined): number {
  if (delayMs === undefined || !Number.isFinite(delayMs)) return 0;
  return Math.min(Math.max(Math.trunc(delayMs), 0), MAX_DELAY_MS);
}

export function Reveal({
  children,
  className,
  delayMs,
}: {
  children: React.ReactNode;
  className?: string;
  delayMs?: number;
}): React.JSX.Element {
  const elementRef = useRef<HTMLDivElement>(null);
  const shownRef = useRef(false);
  const observerRef = useRef<IntersectionObserver | null>(null);

  useEffect(() => {
    const node = elementRef.current;
    if (!node) return;

    const reveal = (target: HTMLElement): void => {
      if (shownRef.current) return;
      shownRef.current = true;
      observerRef.current?.disconnect();
      observerRef.current = null;
      target.classList.remove(...HIDDEN_CLASSES);
      target.classList.add(...VISIBLE_CLASSES);
    };

    if (typeof IntersectionObserver === "undefined") {
      reveal(node);
      return;
    }

    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) reveal(entry.target as HTMLElement);
        }
      },
      { rootMargin: "0px 0px -10% 0px", threshold: 0.1 },
    );

    observerRef.current = observer;
    observer.observe(node);

    return () => {
      observer.disconnect();
      if (observerRef.current === observer) observerRef.current = null;
    };
  }, []);

  const classes = [
    "reveal",
    "motion-safe:transition-[opacity,transform]",
    "motion-safe:duration-[420ms]",
    "motion-safe:ease-[cubic-bezier(.16,1,.3,1)]",
    "motion-safe:[transition-delay:var(--reveal-delay)]",
    ...HIDDEN_CLASSES,
    className,
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <div
      ref={elementRef}
      data-testid={REVEAL_TEST_ID}
      className={classes}
      style={
        { "--reveal-delay": `${clampDelay(delayMs)}ms` } as React.CSSProperties
      }
    >
      {children}
    </div>
  );
}

export default Reveal;
