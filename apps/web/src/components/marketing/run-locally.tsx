import type { ReactElement } from "react";
import { CopyCommand } from "@/components/marketing/copy-command";
import { runLocally } from "@/lib/copy";

function RepoTree(): ReactElement {
  return (
    <div
      role="region"
      aria-label="Repository directory tree, horizontally scrollable code block"
      tabIndex={0}
      className="h-[16rem] w-full overflow-x-auto border border-ink-4/60 bg-ink-2 focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2"
    >
      <pre className="m-0 px-5 py-4 font-mono text-[13px] leading-[1.7] text-ink-7">
        <code>{runLocally.repoTree}</code>
      </pre>
    </div>
  );
}

export function RunLocally(): React.JSX.Element {
  return (
    <section
      id="run-locally"
      aria-labelledby="run-locally-heading"
      className="scroll-mt-24 bg-ink-0 py-20 lg:py-28"
    >
      <div className="mx-auto flex max-w-6xl flex-col gap-12 px-4 sm:px-6 lg:px-8">
        <div className="flex flex-col gap-4">
          <h2
            id="run-locally-heading"
            className="max-w-[20ch] font-sans text-3xl font-semibold leading-tight tracking-tight text-ink-9 md:text-4xl"
          >
            {runLocally.headline}
          </h2>
          <p className="max-w-[66ch] text-base leading-relaxed text-ink-7 md:text-lg">
            {runLocally.lead}
          </p>
        </div>

        <p className="max-w-[58ch] border-t border-ink-4/60 pt-6 text-base leading-relaxed text-ink-7 md:text-lg">
          {runLocally.status}
        </p>

        <div className="flex w-full flex-col gap-6">
          {runLocally.commands.map((entry) => (
            <CopyCommand
              key={entry.label}
              command={entry.command}
              label={entry.label}
            />
          ))}
        </div>

        <RepoTree />

        <p className="max-w-[66ch] text-sm leading-relaxed text-ink-7">
          {runLocally.caption}
        </p>
      </div>
    </section>
  );
}