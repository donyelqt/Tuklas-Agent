import { HeroBackdrop } from "@/components/marketing/hero-backdrop";
import { BrandMark } from "@/components/marketing/brand-mark";
import { HeroRail } from "@/components/marketing/hero-rail";
import { CopyCommand } from "@/components/marketing/copy-command";
import { TreePreview } from "@/components/marketing/tree-preview";
import { hero, nav } from "@/lib/copy";

const FOCUS =
  "focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2";

const EASE =
  "transition-[background-color,border-color,color,opacity] duration-150 ease-[cubic-bezier(.4,0,.2,1)]";

/**
 * The hero is a two-column grid from `lg` up: the command-and-headline column
 * on the left, the tree panel on the right.
 *
 * The panel was briefly an absolutely positioned layer floating in the freed
 * right half. Measured, that overlapped the headline's box by 48px at 1440 and
 * 1280, because `max-w-3xl` is wider than the headline text actually needs and
 * the panel was anchored to the viewport edge rather than to a column. A grid
 * makes the collision impossible instead of tuning it away.
 */
export function Hero() {
  return (
    <section
      id="top"
      className="relative flex min-h-[100dvh] scroll-mt-24 flex-col overflow-hidden bg-ink-0"
    >
      {/* The animated dot field fills the section. It is the first child and
          paints behind every later positioned layer. */}
      <HeroBackdrop />

      {/* The rail belongs to the hero and scrolls away with it. It is not
          viewport-fixed: that left a column of numbers floating over every
          section below. The section is `relative`, so `absolute` anchors it
          here without touching the sticky header's z layer. */}
      <div className="absolute top-1/2 left-0 z-40 hidden -translate-y-1/2 lg:block">
        <HeroRail />
      </div>

      <div className="mx-auto grid w-full max-w-7xl flex-1 grid-cols-1 items-center gap-10 px-4 pt-24 pb-16 sm:px-6 lg:grid-cols-[minmax(0,1fr)_21rem] lg:gap-10 lg:px-8 lg:pb-24">
        <div className="relative z-10 flex flex-col gap-8 lg:pl-16">
          {/* The lockup is a label, not a control. It used to be a row of
              buttons labelled "Clone", which did nothing when pressed, so it
              is now a plain span holding the mark and the wordmark. */}
          <span className="flex min-h-11 items-center gap-3 font-mono text-[11px] uppercase tracking-[0.18em] text-ink-9">
            <BrandMark size={20} className="shrink-0" />
            Tuklas
          </span>

          <h1 className="max-w-[15ch] font-sans text-5xl font-semibold leading-[0.95] tracking-tighter text-ink-9 md:text-6xl lg:max-w-none">
            <span>{hero.headlineLead}</span>{" "}
            <span className="text-accent">{hero.headlineAccent}</span>
          </h1>

          <p className="max-w-[52ch] text-lg leading-relaxed text-ink-7">
            {hero.subhead}
          </p>

          <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
            <a
              href={nav.primaryCta.href}
              className={`inline-flex min-h-11 items-center justify-center whitespace-nowrap bg-accent px-6 text-sm font-medium text-ink-0 ${FOCUS} ${EASE} hover:opacity-90 active:translate-y-px`}
            >
              {hero.ctaPrimary}
            </a>
            <a
              href={nav.secondaryCta.href}
              className={`inline-flex min-h-11 items-center justify-center whitespace-nowrap border border-ink-4/70 px-6 text-sm font-medium text-ink-9 ${FOCUS} ${EASE} hover:border-ink-4 hover:bg-ink-1 active:translate-y-px`}
            >
              {hero.ctaSecondary}
            </a>
          </div>

          <div className="w-full max-w-2xl">
            <CopyCommand
              command={hero.command.command}
              label={hero.command.label}
            />
          </div>
        </div>

        {/* The tree panel is a grid column, not a floating layer, so it can
            never collide with the headline and never clips its own caption.
            Hidden below lg, where the column stack already carries the
            headline, both CTAs and the command block. */}
        <div className="relative z-10 hidden lg:block">
          <TreePreview />
        </div>
      </div>
    </section>
  );
}