import { backends } from "@/lib/copy";

const LABEL =
  "font-mono text-[11px] uppercase tracking-[0.18em] text-ink-7";

/**
 * The one marquee on the page. Wordmark logos only, no caption under any logo.
 * Keyframes are scoped to this file rather than global CSS.
 */
export function BackendMarquee(): React.JSX.Element {
  const half = (
    <ul className="flex shrink-0 items-center gap-14 pr-14">
      {backends.items.map((backend) => (
        <li
          key={backend.slug}
          className="flex items-center whitespace-nowrap font-sans text-base font-semibold text-ink-9"
        >
          {backend.name}
        </li>
      ))}
    </ul>
  );

  return (
    <section
      id="backends"
      aria-labelledby="backends-label"
      className="scroll-mt-24 border-y border-ink-4/60 bg-ink-1 py-14"
    >
      <p id="backends-label" className={`${LABEL} text-center`}>
        {backends.label}
      </p>

      <style>{`
        @keyframes marquee-scroll {
          from {
            transform: translateX(0);
          }
          to {
            transform: translateX(-50%);
          }
        }
        @media (prefers-reduced-motion: reduce) {
          .marquee-track {
            animation: none;
          }
        }
      `}</style>

      <div className="mt-8 h-10 overflow-hidden">
        <div
          aria-hidden="true"
          className="marquee-track flex w-max animate-[marquee-scroll_36s_linear_infinite] hover:[animation-play-state:paused] focus-within:[animation-play-state:paused]"
        >
          {half}
          {half}
        </div>
      </div>
    </section>
  );
}