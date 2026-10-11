import { nav } from "@/lib/copy";
import { site } from "@/lib/site";
/**
 * The `/ssr` entry point, not the package root. The root barrel re-exports
 * the CSR components, which build an `IconContext` with `createContext` at
 * module scope. That throws in the React Server Components graph, where
 * `react` resolves to the server build and `createContext` does not exist,
 * and it fails the build during page-data collection. The `/ssr` barrel
 * imports only `SSRBase` and the context-free icon set.
 */
import { List, X } from "@phosphor-icons/react/ssr";

const FOCUS =
  "focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2";
const EASE = "transition-[background-color,border-color,color,opacity] duration-150 ease-[cubic-bezier(.4,0,.2,1)]";

function linkClasses(): string {
  return `inline-flex min-h-11 items-center whitespace-nowrap px-2 text-sm ${FOCUS} ${EASE} active:translate-y-px`;
}

export function SiteNav() {
  return (
    <header className="sticky top-0 z-40 border-b border-ink-4/60 bg-ink-0">
      <nav
        aria-label="Site"
        className="mx-auto flex h-15 max-w-7xl items-center gap-6 px-4 sm:h-18 sm:px-6 lg:px-8"
      >
        <a
          href="#top"
          className={`inline-flex min-h-11 shrink-0 items-center font-sans text-base font-semibold tracking-tight text-ink-9 ${FOCUS} ${EASE} active:translate-y-px`}
        >
          {site.shortName}
        </a>

        <ul className="hidden items-center gap-1 lg:flex">
          {nav.items.map((item) => (
            <li key={item.href}>
              <a href={item.href} className={linkClasses()}>
                {item.label}
              </a>
            </li>
          ))}
        </ul>

        <div className="ml-auto hidden items-center gap-3 lg:flex">
          <a
            href={nav.secondaryCta.href}
            className={`inline-flex min-h-11 items-center whitespace-nowrap border border-ink-4/70 px-5 text-sm font-medium tracking-tight text-ink-9 ${FOCUS} ${EASE} hover:border-ink-4 hover:bg-ink-1 active:translate-y-px`}
          >
            {nav.secondaryCta.label}
          </a>
          <a
            href={nav.primaryCta.href}
            className={`inline-flex min-h-11 items-center whitespace-nowrap bg-accent px-5 text-sm font-medium tracking-tight text-ink-0 ${FOCUS} ${EASE} hover:opacity-90 active:translate-y-px`}
          >
            {nav.primaryCta.label}
          </a>
        </div>

        <details className="group relative ml-auto lg:hidden">
          <summary
            aria-label="Menu"
            className={`flex h-11 w-11 cursor-pointer list-none items-center justify-center border border-ink-4/70 text-ink-9 [&::-webkit-details-marker]:hidden ${FOCUS} ${EASE} hover:bg-ink-1`}
          >
            <span className="group-open:hidden" aria-hidden="true">
              <List size={20} weight="regular" />
            </span>
            <span className="hidden group-open:block" aria-hidden="true">
              <X size={20} weight="regular" />
            </span>
          </summary>
          <div className="absolute right-0 top-full mt-2 w-56 border border-ink-4/60 bg-ink-1 p-3">
            <ul className="flex flex-col">
              {nav.items.map((item) => (
                <li key={item.href}>
                  <a
                    href={item.href}
                    className={`flex min-h-11 items-center px-2 text-sm text-ink-7 ${FOCUS} ${EASE} hover:bg-ink-2 hover:text-ink-9 active:translate-y-px`}
                  >
                    {item.label}
                  </a>
                </li>
              ))}
            </ul>
            <div className="mt-3 flex flex-col gap-2 border-t border-ink-4/60 pt-3">
              <a
                href={nav.secondaryCta.href}
                className={`flex min-h-11 w-full items-center justify-center whitespace-nowrap border border-ink-4/70 px-5 text-sm font-medium tracking-tight text-ink-9 ${FOCUS} ${EASE} hover:bg-ink-2 active:translate-y-px`}
              >
                {nav.secondaryCta.label}
              </a>
              <a
                href={nav.primaryCta.href}
                className={`flex min-h-11 w-full items-center justify-center whitespace-nowrap bg-accent px-5 text-sm font-medium tracking-tight text-ink-0 ${FOCUS} ${EASE} hover:opacity-90 active:translate-y-px`}
              >
                {nav.primaryCta.label}
              </a>
            </div>
          </div>
        </details>
      </nav>
    </header>
  );
}