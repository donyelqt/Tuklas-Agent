import { TreePreview } from "@/components/marketing/tree-preview";
import { hero, nav } from "@/lib/copy";

const FOCUS =
  "focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2";
const EASE = "transition-[background-color,border-color,color,opacity] duration-150 ease-[cubic-bezier(.4,0,.2,1)]";

export function Hero() {
  return (
    <section
      id="top"
      className="flex min-h-[100dvh] flex-col scroll-mt-24 bg-ink-0 pt-16 lg:pt-24"
    >
      <div className="mx-auto flex max-w-7xl flex-1 flex-col gap-12 px-4 pb-16 sm:px-6 lg:flex-row lg:items-center lg:gap-16 lg:px-8 lg:pb-24">
        <div className="flex w-full flex-col items-start gap-6 lg:w-1/2">
          <h1 className="max-w-[24ch] font-sans text-4xl font-semibold leading-[1.05] tracking-tight text-ink-9 md:text-5xl">
            {hero.headline}
          </h1>
          <p className="max-w-[46ch] text-base leading-relaxed text-ink-7 md:max-w-[52ch]">
            {hero.subhead}
          </p>
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
            <a
              href={nav.primaryCta.href}
              className={`inline-flex min-h-11 items-center justify-center whitespace-nowrap bg-accent px-5 text-sm font-medium tracking-tight text-ink-0 ${FOCUS} ${EASE} hover:opacity-90 active:translate-y-px`}
            >
              {hero.ctaPrimary}
            </a>
            <a
              href={nav.secondaryCta.href}
              className={`inline-flex min-h-11 items-center justify-center whitespace-nowrap border border-ink-4/70 px-5 text-sm font-medium tracking-tight text-ink-9 ${FOCUS} ${EASE} hover:border-ink-4 hover:bg-ink-1 active:translate-y-px`}
            >
              {hero.ctaSecondary}
            </a>
          </div>
        </div>

        <div className="w-full lg:w-1/2">
          <TreePreview />
        </div>
      </div>
    </section>
  );
}