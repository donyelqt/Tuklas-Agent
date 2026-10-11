import { footer } from "@/lib/copy";

const FOCUS =
  "focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2";
const EASE = "transition-[color,border-color,background-color] duration-150 ease-[cubic-bezier(.4,0,.2,1)]";

export function SiteFooter() {
  return (
    <footer className="border-t border-[color:var(--color-rule)] bg-ink-0">
      <div className="mx-auto flex max-w-7xl flex-col gap-6 px-4 py-12 sm:px-6 lg:flex-row lg:items-start lg:justify-between lg:px-8">
        <div className="max-w-[60ch]">
          <p className="text-sm leading-relaxed text-ink-7">{footer.note}</p>
          <p className="mt-3 text-sm text-ink-7">{footer.legal}</p>
        </div>
        <ul className="flex flex-wrap gap-x-6 gap-y-2">
          {footer.links.map((link) => (
            <li key={link.href}>
              <a
                href={link.href}
                className={`inline-flex min-h-11 items-center text-sm text-ink-7 underline-offset-4 hover:text-ink-9 hover:underline ${FOCUS} ${EASE} active:translate-y-px`}
              >
                {link.label}
              </a>
            </li>
          ))}
        </ul>
      </div>
    </footer>
  );
}