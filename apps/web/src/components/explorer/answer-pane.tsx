import { WarningCircle } from "@phosphor-icons/react/ssr";
import { explorer } from "@/lib/explorer-copy";
import { AnswerBody, StepList } from "./answer-body";
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
  error,
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
  error: string | null;
  onCite(nodeId: string): void;
}) {
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
        {steps.length === 0 && response === null && error === null ? (
          // A run in flight and a pane that has never been used are two
          // different states. Saying "no question has been asked yet"
          // while the agent is working would misreport the run as idle.
          <p className="px-4 py-6 text-sm leading-relaxed text-ink-7">
            {asking ? explorer.answer.pending : explorer.answer.idle}
          </p>
        ) : (
          <div className="flex flex-col gap-6 px-4 py-4">
            {steps.length > 0 ? <StepList steps={steps} /> : null}

            {/*
              The answer section has four states, and they are four
              different claims, so they are rendered differently. The
              states are described in `answer-body.tsx`, which is where
              they are rendered.
            */}
            <AnswerBody
              tokenText={tokenText}
              response={response}
              error={error}
              onCite={onCite}
            />
          </div>
        )}
      </div>
    </section>
  );
}
