import { explorer } from "@/lib/explorer-copy";
import { AnswerMarkdown } from "./answer-markdown";
import type { AskResponse, Step } from "./types";

/**
 * The two pieces of a run that both views render: the agent's streamed
 * steps, and the answer itself with its citations and metrics.
 *
 * They live in one module because the explorer's answer pane and the
 * chat's transcript show the same run of the same agent. Rendering them
 * twice would let the two views drift, and a chat answer that read
 * differently from the explorer's answer would be a second, quieter claim
 * about the same output.
 *
 * `StepList` and `AnswerBody` return fragments, not wrappers, so the
 * caller owns the vertical rhythm: in the answer pane they are children of
 * a `gap-6` column, and in the chat they are children of the turn.
 */
export function StepList({ steps }: { steps: Step[] }) {
  return (
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
  );
}

/**
 * The answer: the failure, the pending state, the refusal, or the cited
 * answer, with the citation list and the metrics below it.
 *
 * The four states are four different claims, so they are rendered
 * differently:
 *
 * - a failure: the run did not produce an answer. It is shown as a
 *   failure, never as a pending answer, because a reader looking at a
 *   panel that says "working" when the run already failed would wait
 *   for an answer that is not coming;
 * - no response yet: the model is still working, or the deltas have
 *   arrived and the `done` event has not;
 * - `insufficient: true`: the gate refused. The canned text is shown as
 *   a refusal, with no citation list, and it is not presented as an
 *   answer;
 * - an answer: the `done` event's `answer_markdown`, which is the
 *   authoritative text. The token deltas are what the model produced, but
 *   the `done` event is what the interface renders, so the view shows
 *   exactly what the backend returned.
 */
export function AnswerBody({
  tokenText,
  response,
  error,
  onCite,
}: {
  tokenText: string;
  response: AskResponse | null;
  error: string | null;
  onCite(nodeId: string): void;
}) {
  const citationTargets: Record<string, string> = {};
  if (response !== null) {
    for (const citation of response.citations) {
      citationTargets[String(citation.n)] = citation.node_id;
    }
  }

  return (
    <>
      {error !== null ? (
        <section role="alert" aria-label={explorer.answer.failureTitle}>
          <h3 className="font-mono text-[11px] tracking-[0.18em] text-ink-7 uppercase">
            {explorer.answer.failureTitle}
          </h3>
          <p className="mt-2 text-sm leading-relaxed text-ink-9">{error}</p>
        </section>
      ) : response === null ? (
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

      {error === null && response !== null ? (
        <section aria-label={explorer.answer.citationsTitle}>
          <h3 className="font-mono text-[11px] tracking-[0.18em] text-ink-7 uppercase">
            {explorer.answer.citationsTitle}
          </h3>
          {response.citations.length === 0 ? (
            <p className="mt-2 text-sm text-ink-7">{explorer.answer.citationsEmpty}</p>
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

      {error === null && response !== null ? (
        <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 border-t border-rule pt-3 font-mono text-[11px]">
          <dt className="tracking-[0.12em] text-ink-7 uppercase">
            {explorer.answer.iterationsLabel}
          </dt>
          <dd className="tabular-nums text-ink-9">{response.iterations}</dd>
          <dt className="tracking-[0.12em] text-ink-7 uppercase">
            {explorer.answer.latencyLabel}
          </dt>
          <dd className="tabular-nums text-ink-9">{response.latency_ms} ms</dd>
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

      {error === null && response !== null && response.tokens_source !== undefined ? (
        <p className="text-xs leading-relaxed text-ink-7">
          {response.tokens_source === "provider"
            ? explorer.answer.tokensProviderNote
            : explorer.answer.tokensEstimatedNote}
        </p>
      ) : null}
    </>
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
