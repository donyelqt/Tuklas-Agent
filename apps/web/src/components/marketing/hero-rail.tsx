"use client";
import {
  FlowArrow,
  Scales,
  ShieldCheck,
  TerminalWindow,
  TreeStructure,
  Wrench,
} from "@phosphor-icons/react/ssr";
import { useEffect, useRef, useState } from "react";
import { rail } from "@/lib/copy";

/**
 * The left rail mirrors the divided index strip in the reference design
 * language. It is navigation, not decoration: every cell is a real anchor to
 * a section that exists on this page.
 *
 * The two-digit number is a navigational index, not prose a user has to read,
 * so `ink-5` is legal on it. The label stays in the accessibility tree only:
 * each anchor carries an explicit `aria-label` and an `sr-only` span, and the
 * visible number is `aria-hidden` so a screen reader hears "Hierarchy" once
 * instead of "zero one Hierarchy".
 *
 * The active section is tracked by an IntersectionObserver over the page's
 * six section ids. The active cell carries `aria-current="page"` and a
 * gradient border that runs the Tuklas palette from accent (magenta) to
 * accent-2 (cyan), matching the brand mark gradient.
 */

const FOCUS =
  "focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2";
const EASE = "transition-[background-color,border-color,color,opacity] duration-150 ease-[cubic-bezier(.4,0,.2,1)]";

/**
 * One icon per section, single family, single weight. Order matches
 * `rail.items` exactly.
 */
const ICONS = [TreeStructure, FlowArrow, Wrench, Scales, ShieldCheck, TerminalWindow];


export function HeroRail() {
  const [activeId, setActiveId] = useState("");
  const observerRef = useRef<IntersectionObserver | null>(null);

  useEffect(() => {
    const ids = rail.items
      .map((item) => item.href.slice(1))
      .filter(Boolean);
    if (typeof IntersectionObserver === "undefined" || ids.length === 0) {
      return;
    }
    const observer = new IntersectionObserver(
      (entries) => {
        let best: IntersectionObserverEntry | null = null;
        for (const entry of entries) {
          if (!entry.isIntersecting) continue;
          if (
            best === null ||
            Math.abs(entry.boundingClientRect.top) <
              Math.abs(best.boundingClientRect.top)
          ) {
            best = entry;
          }
        }
        setActiveId(best ? (best.target.id as string) : "");
      },
      { rootMargin: "0px 0px -50% 0px", threshold: 0 },
    );
    observerRef.current = observer;
    for (const id of ids) {
      const el = document.getElementById(id);
      if (el) observer.observe(el);
    }
    return () => {
      observer.disconnect();
      observerRef.current = null;
    };
  }, []);

  const activeHref = activeId ? `#${activeId}` : "";

  return (
    <nav aria-label="Section" className="flex w-16 flex-col">
      {rail.items.map((item, index) => {
        const Icon = ICONS[index] ?? TreeStructure;
        const isActive = item.href === activeHref;
        return (
          <a
            key={item.href}
            href={item.href}
            aria-label={item.label}
            aria-current={isActive ? "page" : undefined}
            className={`group relative flex h-16 w-16 flex-col items-center justify-center gap-1 text-ink-5 ${EASE} ${FOCUS} hover:bg-ink-1 hover:text-ink-9 ${
              index > 0 ? "border-t border-ink-4/60" : ""
            } ${isActive ? "border-l-transparent" : "border-l border-ink-4/60"}`}
          >
            <span className="sr-only">{item.label}</span>
            <Icon
              size={14}
              weight="regular"
              aria-hidden="true"
              className="text-ink-7 transition-colors duration-150 group-hover:text-ink-9"
            />
            <span
              aria-hidden="true"
              className="font-mono text-[10px] tracking-[0.16em] text-ink-5 transition-colors duration-150 group-hover:text-ink-9"
            >
              {item.n}
            </span>
            {isActive ? (
              <span
                aria-hidden="true"
                className="absolute inset-y-0 left-0 w-[3px] bg-[linear-gradient(to_bottom,theme(colors.accent),theme(colors.accent-2))]"
              />
            ) : null}
          </a>
        );
      })}
    </nav>
  );
}
