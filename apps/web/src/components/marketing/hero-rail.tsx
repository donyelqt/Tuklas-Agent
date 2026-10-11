import {
  FlowArrow,
  Scales,
  ShieldCheck,
  TerminalWindow,
  TreeStructure,
  Wrench,
} from "@phosphor-icons/react/ssr";
import { rail } from "@/lib/copy";

/**
 * The left rail mirrors the divided index strip in the reference design
 * language. It is navigation, not decoration: every cell is a real anchor to a
 * section that exists on this page.
 *
 * The two-digit number is a navigational index, not prose a user has to read,
 * so `ink-5` is legal on it. The label stays in the accessibility tree only:
 * each anchor carries an explicit `aria-label` and an `sr-only` span, and the
 * visible number is `aria-hidden` so a screen reader hears "Hierarchy" once
 * instead of "zero one Hierarchy".
 */

const FOCUS =
  "focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2";

/**
 * One icon per section, single family, single weight. Order matches
 * `rail.items` exactly.
 */
const ICONS = [TreeStructure, FlowArrow, Wrench, Scales, ShieldCheck, TerminalWindow];

export function HeroRail() {
  return (
    <nav aria-label="Section" className="flex w-16 flex-col">
      {rail.items.map((item, index) => {
        const Icon = ICONS[index] ?? TreeStructure;
        return (
          <a
            key={item.href}
            href={item.href}
            aria-label={item.label}
            className={`group flex h-16 w-16 flex-col items-center justify-center gap-1 text-ink-5 transition-[background-color,border-color,color,opacity] duration-150 ease-[cubic-bezier(.4,0,.2,1)] hover:bg-ink-1 hover:text-ink-9 ${FOCUS} ${
              index > 0 ? "border-t border-ink-4/60" : ""
            }`}
          >
            <span className="sr-only">{item.label}</span>
            <Icon size={14} weight="regular" aria-hidden="true" className="text-ink-7 transition-colors duration-150 group-hover:text-ink-9" />
            <span
              aria-hidden="true"
              className="font-mono text-[10px] tracking-[0.16em] text-ink-5 transition-colors duration-150 group-hover:text-ink-9"
            >
              {item.n}
            </span>
          </a>
        );
      })}
    </nav>
  );
}