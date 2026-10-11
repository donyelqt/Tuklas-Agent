import type { ReactElement } from "react";
import { comparison } from "@/lib/copy";

function Arm({
  label,
  items,
  ruleClass,
  headingClass,
}: {
  label: string;
  items: string[];
  ruleClass: string;
  headingClass: string;
}): ReactElement {
  return (
    <div className={`border-l-2 pl-6 ${ruleClass}`}>
      <h3
        className={`font-mono text-[11px] font-medium uppercase tracking-[0.18em] ${headingClass}`}
      >
        {label}
      </h3>
      <ul className="mt-5 flex flex-col gap-3">
        {items.map((item) => (
          <li
            key={item}
            className="max-w-[46ch] text-sm leading-relaxed text-ink-7"
          >
            {item}
          </li>
        ))}
      </ul>
    </div>
  );
}

export function ComparisonArms(): React.JSX.Element {
  return (
    <section
      id="comparison"
      aria-labelledby="comparison-heading"
      className="scroll-mt-24 bg-ink-0 py-20 lg:py-28"
    >
      <div className="mx-auto flex max-w-7xl flex-col gap-12 px-4 sm:px-6 lg:px-8">
        <div className="flex flex-col gap-4">
          <h2
            id="comparison-heading"
            className="max-w-[24ch] font-sans text-3xl font-semibold leading-tight tracking-tight text-ink-9 md:text-4xl"
          >
            {comparison.headline}
          </h2>
          <p className="max-w-[66ch] text-base leading-relaxed text-ink-7 md:text-lg">
            {comparison.lead}
          </p>
        </div>

        <div className="grid grid-cols-1 gap-10 md:grid-cols-2 md:gap-12">
          <Arm
            label={comparison.tuklas.label}
            items={comparison.tuklas.items}
            ruleClass="border-accent"
            headingClass="text-accent"
          />
          <Arm
            label={comparison.vector.label}
            items={comparison.vector.items}
            ruleClass="border-accent-2"
            headingClass="text-accent-2"
          />
        </div>

        <div className="flex flex-col gap-6">
          <dl className="grid grid-cols-1 sm:grid-cols-2 sm:gap-x-12">
            {comparison.facts.map((fact) => (
              <div
                key={fact.label}
                className="flex flex-col gap-1 border-t border-ink-4/60 py-4 sm:grid sm:grid-cols-[minmax(0,12rem)_minmax(0,1fr)] sm:gap-x-6"
              >
                <dt className="font-mono text-[11px] font-medium uppercase tracking-[0.18em] text-ink-7">
                  {fact.label}
                </dt>
                <dd className="font-mono text-sm leading-relaxed text-ink-9">
                  {fact.value}
                </dd>
              </div>
            ))}
          </dl>

          <div className="flex max-w-[66ch] flex-col gap-3">
            <p className="text-sm leading-relaxed text-ink-7">
              {comparison.decisionRule}
            </p>
            <p className="text-sm leading-relaxed text-ink-7">
              {comparison.expectedPattern}
            </p>
          </div>
        </div>

        <div className="border-t-2 border-accent pt-8">
          <p className="max-w-[58ch] font-sans text-xl font-medium leading-relaxed text-ink-9 md:text-2xl">
            {comparison.status}
          </p>
        </div>
      </div>
    </section>
  );
}