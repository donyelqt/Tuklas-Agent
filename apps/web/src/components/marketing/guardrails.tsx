import type { ReactElement } from "react";
import { guardrails } from "@/lib/copy";

function Item({ title, body }: { title: string; body: string }): ReactElement {
  return (
    <div className="border-t border-ink-4/60 py-6">
      <h3 className="font-sans text-base font-semibold tracking-tight text-ink-9">
        {title}
      </h3>
      <p className="mt-2 max-w-[52ch] text-sm leading-relaxed text-ink-7">
        {body}
      </p>
    </div>
  );
}

export function Guardrails(): React.JSX.Element {
  const columns: Array<Array<{ title: string; body: string }>> = [
    guardrails.items.slice(0, Math.ceil(guardrails.items.length / 2)),
    guardrails.items.slice(Math.ceil(guardrails.items.length / 2)),
  ];

  return (
    <section
      id="guardrails"
      aria-labelledby="guardrails-heading"
      className="scroll-mt-24 bg-ink-1 py-20 lg:py-28"
    >
      <div className="mx-auto flex max-w-6xl flex-col gap-12 px-4 sm:px-6 lg:px-8">
        <div className="flex flex-col gap-4">
          <h2
            id="guardrails-heading"
            className="max-w-[22ch] font-sans text-3xl font-semibold leading-tight tracking-tight text-ink-9 md:text-4xl"
          >
            {guardrails.headline}
          </h2>
          <p className="max-w-[66ch] text-base leading-relaxed text-ink-7 md:text-lg">
            {guardrails.lead}
          </p>
        </div>

        <div className="grid grid-cols-1 gap-x-12 lg:grid-cols-2">
          {columns.map((column, columnIndex) => (
            <div key={columnIndex} className="flex flex-col">
              {column.map((item) => (
                <Item key={item.title} title={item.title} body={item.body} />
              ))}
            </div>
          ))}
        </div>

        <div className="border-t border-rule-strong pt-10">
          <p className="max-w-[52ch] font-sans text-2xl font-medium leading-snug tracking-tight text-ink-9 md:text-3xl">
            {guardrails.disclaimer}
          </p>
        </div>
      </div>
    </section>
  );
}