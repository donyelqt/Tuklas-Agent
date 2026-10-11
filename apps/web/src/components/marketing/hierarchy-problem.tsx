import { problem } from "@/lib/copy";

/**
 * Two-column comparison, not a three-card row. The single permitted accent
 * exception on the page lives here: the structured column is the one the
 * product is about, so it carries the accent rule and heading.
 */
export function HierarchyProblem(): React.JSX.Element {
  return (
    <section
      id="problem"
      aria-labelledby="problem-headline"
      className="scroll-mt-24 bg-ink-0 py-20 md:py-28"
    >
      <div className="mx-auto flex max-w-6xl flex-col gap-12 px-4 sm:px-6 lg:px-8">
        <div className="flex max-w-3xl flex-col gap-4">
          <h2
            id="problem-headline"
            className="font-sans text-3xl font-semibold leading-tight tracking-tight text-ink-9 md:text-4xl"
          >
            {problem.headline}
          </h2>
          <p className="max-w-[62ch] text-lg leading-relaxed text-ink-7">
            {problem.lead}
          </p>
        </div>

        <div className="grid grid-cols-1 gap-x-12 gap-y-10 md:grid-cols-2">
          <div>
            <h3 className="border-l border-ink-4/60 pl-4 font-mono text-[11px] uppercase tracking-[0.18em] text-ink-7">
              {problem.flat.label}
            </h3>
            <ul className="mt-6 list-none divide-y divide-ink-4/60">
              {problem.flat.items.map((item) => (
                <li
                  key={item}
                  className="flex gap-3 py-4 text-sm leading-relaxed text-ink-7"
                >
                  <span
                    aria-hidden="true"
                    className="mt-1.5 h-1 w-1 shrink-0 border border-ink-5"
                  />
                  <span className="max-w-[46ch]">{item}</span>
                </li>
              ))}
            </ul>
          </div>

          <div>
            <h3 className="border-l border-accent pl-4 font-mono text-[11px] uppercase tracking-[0.18em] text-accent">
              {problem.structured.label}
            </h3>
            <ul className="mt-6 list-none divide-y divide-ink-4/60">
              {problem.structured.items.map((item) => (
                <li
                  key={item}
                  className="flex gap-3 py-4 text-sm leading-relaxed text-ink-7"
                >
                  <span
                    aria-hidden="true"
                    className="mt-1.5 h-1 w-1 shrink-0 border border-ink-5"
                  />
                  <span className="max-w-[46ch]">{item}</span>
                </li>
              ))}
            </ul>
          </div>
        </div>

        <p className="max-w-[66ch] text-sm leading-relaxed text-ink-7">
          {problem.note}
        </p>
      </div>
    </section>
  );
}