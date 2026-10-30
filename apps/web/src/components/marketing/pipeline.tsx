import { pipeline } from "@/lib/copy";

/** Seven-step ledger: one band background, rules between rows, no cards. */
export function Pipeline(): React.JSX.Element {
  return (
    <section
      id="pipeline"
      aria-labelledby="pipeline-headline"
      className="scroll-mt-24 bg-ink-0 py-20 md:py-28"
    >
      <div className="mx-auto flex max-w-6xl flex-col gap-12 px-4 sm:px-6 lg:px-8">
        <div className="flex max-w-3xl flex-col gap-4">
          <h2
            id="pipeline-headline"
            className="font-sans text-3xl font-semibold leading-tight tracking-tight text-ink-9 md:text-4xl"
          >
            {pipeline.headline}
          </h2>
          <p className="max-w-[62ch] text-lg leading-relaxed text-ink-7">
            {pipeline.lead}
          </p>
        </div>

        <ol className="grid grid-cols-1 bg-ink-1 lg:grid-cols-2">
          {pipeline.steps.map((step, index) => (
            <li
              key={step.name}
              className={[
                "flex gap-4 border-l border-ink-4/60 py-5 pl-5 pr-5 lg:pr-10",
                index % 2 === 0 ? "lg:border-l-0" : "",
                index >= 2 ? "lg:border-t" : "",
              ]
                .filter(Boolean)
                .join(" ")}
            >
              <span
                aria-hidden="true"
                className="font-mono text-[11px] leading-6 tracking-[0.18em] text-ink-7"
              >
                {String(index + 1).padStart(2, "0")}
              </span>
              <div className="flex flex-col gap-2">
                <h3 className="font-sans text-base font-semibold tracking-tight text-ink-9">
                  {step.name}
                </h3>
                <p className="max-w-[44ch] text-sm leading-relaxed text-ink-7">
                  {step.body}
                </p>
              </div>
            </li>
          ))}
        </ol>
      </div>
    </section>
  );
}