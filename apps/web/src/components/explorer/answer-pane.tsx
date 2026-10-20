import { WarningCircle } from "@phosphor-icons/react/ssr";
import { explorer } from "@/lib/explorer-copy";
import { AnswerMarkdown } from "./answer-markdown";
import type { AskResponse, Step } from "./types";

/**
 * The answer pane: the question, the agent's streamed steps, the evidence
 * set, and the cited answer.
 *
 * The disclaimer banner is NOT decoration and NOT part of the scrolling
 * answer. PRD US-2 makes it a legal requirement, so it is rendered outside
 * the scroll container: it is present before any question is asked, it
 * stays put while the answer scrolls under it, and it survives a change of
 * answer. Everything below it is scrollable.
 */
export function AnswerPane({
  question,
  onQuestionChange,
  onAsk,
  asking,
  canAsk,
  steps,
  tokenText,
  response,
  onCite,
}: {
  question: string;
  onQuestionChange(value: string): void;
  onAsk(): void;
  asking: boolean;
  canAsk: boolean;
  steps: Step[];
  tokenText: string;
  response: AskResponse | null;
  onCite(nodeId: string): void;
}) {
  const citationTargets: Record<string, string> = {};
  if (response !== null) {
    for (const citation of response.citations) {
      citationTargets[String(citation.n)] = citation.node_id;
    }
  }

  return (
    <section
      aria-labelledby="answer-pane-heading"
      className="flex h-full min-h-0 flex-col overflow-hidden border-rule bg-ink-1"
    >
      <header className="border-b border-rule px-4 py-3">
        <h2
          id="answer-pane-heading"
          className="font-mono text-[11px] tracking-[0.18em] text-ink-7 uppercase"
        >
          {explorer.answer.title}
        </h2>
        <p className="mt-1 font-mono text-[11px] text-ink-7">
          {explorer.answer.endpoint}
        </p>
      </header>

      {/*
        The persistent disclaimer. It sits outside the scroll container on
        purpose: it must not scroll away with the answer, and it must be
        there before a question has been asked at all.
      */}
      <div
        role="note"
        className="flex items-start gap-2 border-b border-rule-strong bg-ink-2 px-4 py-3"
      >
        <WarningCircle
          size={16}
          weight="regular"
          aria-hidden="true"
          className="mt-0.5 shrink-0 text-accent"
        />
        <p className="text-xs leading-relaxed text-ink-9">{explorer.disclaimer}</p>
      </div>

      <form
        className="flex flex-col gap-2 border-b border-rule px-4 py-3"
        onSubmit={(event) => {
          event.preventDefault();
          if (canAsk && !asking) {
            onAsk();
          }
        }}
      >
        <label
          htmlFor="ask-question"
          className="font-mono text-[11px] tracking-[0.12em] text-ink-7 uppercase"
        >
          {explorer.answer.questionLabel}
        </label>
        <div className="flex flex-col gap-2 sm:flex-row">
          <input
            id="ask-question"
            type="text"
            value={question}
            onChange={(event) => onQuestionChange(event.target.value)}
            placeholder={explorer.answer.questionPlaceholder}
            disabled={asking}
            className="min-h-11 min-w-0 flex-1 border border-ink-4 bg-ink-2 px-3 text-sm text-ink-9 placeholder:text-ink-7 focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2"
          />
          <button
            type="submit"
            disabled={!canAsk || asking}
            title={canAsk ? undefined : explorer.answer.askDisabled}
            className="min-h-11 shrink-0 bg-accent px-5 text-sm font-medium tracking-tight text-ink-0 transition-opacity duration-150 hover:opacity-90 focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 disabled:cursor-not-allowed disabled:opacity-40"
          >
            {asking ? explorer.answer.askingLabel : explorer.answer.askLabel}
          </button>
        </div>
      </form>

      <div className="min-h-0 flex-1 overflow-auto">
        {steps.length === 0 && response === null ? (
          <p className="px-4 py-6 text-sm leading-relaxed text-ink-7">
            {explorer.answer.idle}
          </p>
        ) : (
          <div className="flex flex-col gap-6 px-4 py-4">
            {steps.length > 0 ? (
              <section aria-label={explorer.answer.stepsTitle}>
                <h3 className="font-mono text-[11px] tracking-[0.18em] text-ink-7 uppercase">
                  {explorer.answer.stepsTitle}
                </h3>
                <ol className="mt-2 flex flex-col gap-2">
                  {steps.map((step, index) => (
                    <StepRow key={index} step={step} />
                  ))}
                </ol>
              </section>
            ) : null}

            {/*
              The answer section has three states, and they are three
              different claims, so they are rendered differently:

              - no response yet: the model is still working, or the deltas
                have arrived and the `done` event has not;
              - `insufficient: true`: the gate refused. The canned text is
                shown as a refusal, with no citation list, and it is not
                presented as an answer;
              - an answer: the `done` event's `answer_markdown`, which is
                the authoritative text. The token deltas are what the
                model produced, but the `done` event is what the
                interface renders, so the pane shows exactly what the
                backend returned.
            */}
            {response === null ? (
              <section aria-label={explorer.answer.answerTitle}>
                <h3 className="font-mono text-[11px] tracking-[0.18em] text-ink-7 uppercase">
                  {explorer.answer.answerTitle}
                </h3>
                {tokenText.length > 0 ? (
                  <div className="mt-2">
                    <AnswerMarkdown
                      text={tokenText}
                      citationTargets={citationTargets}
                      onCite={onCite}
                    />
                  </div>
                ) : null}
                <p className="mt-2 text-xs leading-relaxed text-ink-7">
                  {explorer.answer.answerPending}
                </p>
              </section>
            ) : response.insufficient ? (
              <section aria-label={explorer.answer.refusalTitle}>
                <h3 className="font-mono text-[11px] tracking-[0.18em] text-ink-7 uppercase">
                  {explorer.answer.refusalTitle}
                </h3>
                <p className="mt-2 text-sm leading-relaxed text-ink-9">
                  {response.answer_markdown}
                </p>
                <p className="mt-2 text-xs leading-relaxed text-ink-7">
                  {explorer.answer.refusalNote}
                </p>
              </section>
            ) : (
              <section aria-label={explorer.answer.answerTitle}>
                <h3 className="font-mono text-[11px] tracking-[0.18em] text-ink-7 uppercase">
                  {explorer.answer.answerTitle}
                </h3>
                <div className="mt-2">
                  <AnswerMarkdown
                    text={response.answer_markdown}
                    citationTargets={citationTargets}
                    onCite={onCite}
                  />
                </div>
              </section>
            )}

            {response !== null ? (
              <section aria-label={explorer.answer.citationsTitle}>
                <h3 className="font-mono text-[11px] tracking-[0.18em] text-ink-7 uppercase">
                  {explorer.answer.citationsTitle}
                </h3>
                {response.citations.length === 0 ? (
                  <p className="mt-2 text-sm text-ink-7">
                    {explorer.answer.citationsEmpty}
                  </p>
                ) : (
                  <ul className="mt-2 flex flex-col gap-1">
                    {response.citations.map((citation) => (
                      <li key={citation.n}>
                        <button
                          type="button"
                          onClick={() => onCite(citation.node_id)}
                          className="flex w-full items-baseline gap-3 border border-transparent px-2 py-1.5 text-left transition-[background-color,border-color] duration-150 hover:border-rule hover:bg-ink-2 focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2"
                        >
                          <span className="font-mono text-[11px] text-accent">
                            [{citation.n}]
                          </span>
                          <span className="min-w-0 flex-1 truncate text-sm text-ink-9">
                            {citation.label}
                          </span>
                          {citation.page_start !== null ? (
                            <span className="font-mono text-[11px] tabular-nums text-ink-7">
                              {explorer.tree.pageLabel} {citation.page_start}
                            </span>
                          ) : null}
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </section>
            ) : null}

            {response !== null ? (
              <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 border-t border-rule pt-3 font-mono text-[11px]">
                <dt className="tracking-[0.12em] text-ink-7 uppercase">
                  {explorer.answer.iterationsLabel}
                </dt>
                <dd className="tabular-nums text-ink-9">{response.iterations}</dd>
                <dt className="tracking-[0.12em] text-ink-7 uppercase">
                  {explorer.answer.latencyLabel}
                </dt>
                <dd className="tabular-nums text-ink-9">
                  {response.latency_ms} ms
                </dd>
                <dt className="tracking-[0.12em] text-ink-7 uppercase">
                  {explorer.answer.tokensLabel}
                </dt>
                <dd className="tabular-nums text-ink-9">
                  {response.tokens.in} in / {response.tokens.out} out
                </dd>
                {response.tokens_source !== undefined ? (
                  <>
                    <dt className="tracking-[0.12em] text-ink-7 uppercase">
                      {explorer.answer.evidenceCountLabel}
                    </dt>
                    <dd className="tabular-nums text-ink-9">
                      {response.evidence_ids.length}
                    </dd>
                  </>
                ) : null}
              </dl>
            ) : null}

            {response !== null && response.tokens_source !== undefined ? (
              <p className="text-xs leading-relaxed text-ink-7">
                {response.tokens_source === "provider"
                  ? explorer.answer.tokensProviderNote
                  : explorer.answer.tokensEstimatedNote}
              </p>
            ) : null}
          </div>
        )}
      </div>
    </section>
  );
}

/** One streamed step, rendered in the order the server sent it. */
function StepRow({ step }: { step: Step }) {
  if (step.kind === "plan") {
    return (
      <li className="border-l border-rule pl-3">
        <p className="font-mono text-[11px] tracking-[0.12em] text-ink-7 uppercase">
          {explorer.answer.planLabel}
        </p>
        <ul className="mt-1 flex list-disc flex-col gap-0.5 pl-4">
          {step.subQuestions.map((question, index) => (
            <li key={index} className="text-sm leading-relaxed text-ink-9">
              {question}
            </li>
          ))}
        </ul>
      </li>
    );
  }
  if (step.kind === "tool_call") {
    return (
      <li className="border-l border-rule pl-3">
        <p className="font-mono text-[11px] tracking-[0.12em] text-ink-7 uppercase">
          {explorer.answer.toolCallLabel}
        </p>
        <p className="mt-1 font-mono text-xs break-words text-ink-9">
          {step.tool}
          {Object.keys(step.args).length > 0 ? (
            <span className="text-ink-7"> {JSON.stringify(step.args)}</span>
          ) : null}
        </p>
      </li>
    );
  }
  if (step.kind === "tool_result") {
    return (
      <li className="border-l border-rule pl-3">
        <p className="font-mono text-[11px] tracking-[0.12em] text-ink-7 uppercase">
          {explorer.answer.toolResultLabel}
        </p>
        <p className="mt-1 font-mono text-xs break-words text-ink-9">
          {step.tool}
          <span
            className={
              step.relevant ? "ml-2 text-ink-7" : "ml-2 text-ink-7 line-through"
            }
          >
            {step.relevant
              ? explorer.answer.relevantLabel
              : explorer.answer.irrelevantLabel}
          </span>
          {step.resultIds.length > 0 ? (
            <span className="ml-2 text-ink-7">{step.resultIds.join(", ")}</span>
          ) : (
            <span className="ml-2 text-ink-7">{explorer.answer.noSteps}</span>
          )}
        </p>
      </li>
    );
  }
  return (
    <li className="border-l border-rule pl-3">
      <p className="font-mono text-[11px] tracking-[0.12em] text-ink-7 uppercase">
        {explorer.answer.evidenceLabel}
      </p>
      <p className="mt-1 font-mono text-xs break-words text-ink-9">
        <span className="text-ink-7">
          {step.insufficient
            ? explorer.answer.insufficientLabel
            : explorer.answer.sufficientLabel}
        </span>
        {step.evidenceIds.length > 0 ? (
          <span className="ml-2 text-ink-7">{step.evidenceIds.join(", ")}</span>
        ) : null}
      </p>
    </li>
  );
}
