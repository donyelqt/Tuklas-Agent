import { architecture } from "@/lib/copy";
import { ArchitectureNodeGraph } from "./architecture-node-graph";
import { ArchitecturePipelineRail } from "./architecture-pipeline-rail";
import { ArchitectureTerminalGraph } from "./architecture-terminal-graph";

const TOGGLE_CSS = `
  [data-arch-panel] { display: none; }
  #arch-view-terminal-graph:checked ~ [data-arch-panels] [data-arch-panel="terminal-graph"],
  #arch-view-node-graph:checked ~ [data-arch-panels] [data-arch-panel="node-graph"],
  #arch-view-pipeline-rail:checked ~ [data-arch-panels] [data-arch-panel="pipeline-rail"] {
    display: block;
  }
  /* Active-view indicator: the checked radio's label gets the accent rule
     and the muted foreground; unchecked labels stay quiet. The labels sit
     in a later sibling of the radios, so the general combinator reaches
     them where the adjacent peer selector cannot. */
  #arch-view-terminal-graph:checked ~ [role="radiogroup"] label[for="arch-view-terminal-graph"],
  #arch-view-node-graph:checked ~ [role="radiogroup"] label[for="arch-view-node-graph"],
  #arch-view-pipeline-rail:checked ~ [role="radiogroup"] label[for="arch-view-pipeline-rail"] {
    color: var(--color-ink-7);
    border-bottom-color: var(--color-accent);
  }
`;

function Layer({
  index,
  name,
  owning,
  body,
}: {
  index: number;
  name: string;
  owning: string;
  body: string;
}) {
  return (
    <li className="grid grid-cols-[auto_1fr] gap-x-5 border-t border-ink-4/60 py-6 sm:gap-x-8">
      <div className="flex flex-col items-center">
        <span
          aria-hidden="true"
          className="font-mono text-[11px] leading-5 tracking-[0.16em] text-ink-7"
        >
          {String(index + 1).padStart(2, "0")}
        </span>
        <span aria-hidden="true" className="mt-2 h-full w-px bg-ink-4/60" />
      </div>
      <div className="flex flex-col gap-2 pb-1">
        <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
          <h3 className="font-sans text-base font-semibold tracking-tight text-ink-9">
            {name}
          </h3>
          <code className="font-mono text-[11px] tracking-tight text-ink-7">
            {owning}
          </code>
        </div>
        <p className="max-w-[58ch] text-sm leading-relaxed text-ink-7">{body}</p>
      </div>
    </li>
  );
}

export function ArchitectureDiagram(): React.JSX.Element {
  return (
    <section
      id="architecture"
      aria-labelledby="architecture-headline"
      data-testid="architecture-diagram"
      className="scroll-mt-24 bg-ink-0 py-20 md:py-28"
    >
      <div className="mx-auto flex max-w-6xl flex-col gap-14 px-4 sm:px-6 lg:px-8">
        <div className="flex max-w-3xl flex-col gap-4">
          <h2
            id="architecture-headline"
            className="font-sans text-3xl font-semibold leading-tight tracking-tight text-ink-9 md:text-4xl"
          >
            {architecture.headline}
          </h2>
          <p className="max-w-[62ch] text-lg leading-relaxed text-ink-7">
            {architecture.lead}
          </p>
        </div>

        {/* The layers, in request order. */}
        <div className="flex flex-col gap-2">
          <h3 className="font-sans text-xl font-semibold tracking-tight text-ink-9">
            {architecture.layersCaption}
          </h3>
          <ol className="border-b border-ink-4/60">
            {architecture.layers.map((layer, index) => (
              <Layer key={layer.id} index={index} {...layer} />
            ))}
          </ol>
        </div>

        {/* The compiled graph, drawn in one of three views. */}
        <div className="flex flex-col gap-6">
          <div className="flex flex-col gap-2">
            <h3 className="font-sans text-xl font-semibold tracking-tight text-ink-9">
              {architecture.workflow.caption}
            </h3>
            <p className="max-w-[62ch] text-base leading-relaxed text-ink-7">
              {architecture.workflow.lead}
            </p>
          </div>

          {/* The view switcher. Three radios, hidden, control which panel is
              visible. The radios are direct siblings of [data-arch-panels] so
              the :checked ~ sibling rule in the inline <style> can show only the
              checked view. The labels carry the same aria-pressed pattern the
              rest of the page uses for toggles. */}
          <div data-arch-view="true" className="relative isolate flex flex-col">
            <style dangerouslySetInnerHTML={{ __html: TOGGLE_CSS }} />
            {/* Radios live as direct siblings of [data-arch-panels] (not wrapped)
                 so the :checked ~ combinator reaches the panels. */}
            {architecture.diagram.variants.map((variant) => (
              <input
                key={variant.id}
                type="radio"
                id={`arch-view-${variant.id}`}
                name="arch-view"
                value={variant.id}
                aria-label={variant.label}
                defaultChecked={variant.id === "terminal-graph"}
                className="sr-only"
              />
            ))}
            <div
              aria-label={architecture.diagram.caption}
              role="radiogroup"
              tabIndex={-1}
              className="inline-flex items-center gap-3"
            >
              {architecture.diagram.variants.map((variant) => (
                <label
                  key={variant.id}
                  htmlFor={`arch-view-${variant.id}`}
                  data-testid={`arch-view-label-${variant.id}`}
                  className={
                    "cursor-pointer border-b-2 border-transparent px-1 py-2 font-mono text-[11px] uppercase tracking-[0.16em] text-ink-9 transition-colors duration-150 " +
                    "hover:text-ink-7"
                  }
                >
                  {variant.label}
                </label>
              ))}
            </div>

            <p className="max-w-[62ch] text-sm leading-relaxed text-ink-7">
              {architecture.workflow.legend}
            </p>

            <div data-arch-panels className="pt-2">
              <div data-arch-panel="terminal-graph" className="arch-panel">
                <ArchitectureTerminalGraph />
              </div>
              <div data-arch-panel="node-graph" className="arch-panel">
                <ArchitectureNodeGraph />
              </div>
              <div data-arch-panel="pipeline-rail" className="arch-panel">
                <ArchitecturePipelineRail />
              </div>
            </div>
          </div>
        </div>

        {/* The five retrieval channels. */}
        <div className="flex flex-col gap-6">
          <div className="flex flex-col gap-2">
            <h3 className="font-sans text-xl font-semibold tracking-tight text-ink-9">
              {architecture.channels.caption}
            </h3>
            <p className="max-w-[62ch] text-base leading-relaxed text-ink-7">
              {architecture.channels.lead}
            </p>
          </div>
          <dl className="grid grid-cols-1 gap-x-10 sm:grid-cols-2 lg:grid-cols-3">
            {architecture.channels.items.map((item) => (
              <li
                key={item.key}
                className="flex flex-col gap-2 border-t border-ink-4/60 py-5"
              >
                <dt className="flex items-baseline gap-x-3">
                  <code className="font-mono text-[11px] tracking-tight text-accent">
                    {item.key}
                  </code>
                  <span className="font-sans text-sm font-semibold tracking-tight text-ink-9">
                    {item.label}
                  </span>
                </dt>
                <dd className="max-w-[46ch] text-sm leading-relaxed text-ink-7">
                  {item.body}
                </dd>
              </li>
            ))}
          </dl>
          <p className="max-w-[66ch] border-t border-rule-strong pt-6 text-sm leading-relaxed text-ink-7">
            {architecture.channels.footnote}
          </p>
        </div>

        {/* The dense baseline, drawn apart from the shipped path. */}
        <div className="flex flex-col gap-3 border-l-2 border-accent-2 bg-ink-1 px-6 py-6">
          <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
            <span className="font-mono text-[10px] tracking-[0.18em] text-ink-7">
              {architecture.baseline.label.toUpperCase()}
            </span>
            <h3 className="font-sans text-lg font-semibold tracking-tight text-ink-9">
              {architecture.baseline.name}
            </h3>
            <code className="font-mono text-[11px] tracking-tight text-ink-7">
              {architecture.baseline.owning}
            </code>
          </div>
          <p className="max-w-[66ch] text-sm leading-relaxed text-ink-7">
            {architecture.baseline.body}
          </p>
          <p className="max-w-[66ch] text-sm leading-relaxed text-ink-9">
            {architecture.baseline.status}
          </p>
        </div>

        <p className="max-w-[66ch] border-t border-rule-strong pt-6 text-sm leading-relaxed text-ink-7">
          {architecture.footnote}
        </p>
      </div>
    </section>
  );
}
