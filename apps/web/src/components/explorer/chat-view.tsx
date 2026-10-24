"use client";

import { useEffect, useRef, useState } from "react";
import { BrandMark } from "@/components/brand/brand-mark";
import { WarningCircle, PaperPlaneTilt } from "@phosphor-icons/react/ssr";
import { explorer } from "@/lib/explorer-copy";
import { AnswerBody, StepList } from "./answer-body";
import { SourceDetail } from "./source-pane";
import type { AskTurn } from "./turn";
import type { DocumentSummary, NodeSource } from "./types";

/**
 * The chat view: the explorer's question-and-answer runtime as a full-screen
 * chat.
 *
 * It is the same tool as `answer-pane.tsx`, not a different one. The
 * streamed steps, the refusal, the cited answer, the metrics and the
 * disclaimer render through `answer-body.tsx`, which the answer pane also
 * uses, so one run of the agent reads the same in both views.
 *
 * What makes this a chat rather than a second answer pane:
 *
 * 1. It keeps a TRANSCRIPT. The turns are a list, so a previous question
 *    and its answer stay on screen while the next one is asked. The
 *    explorer shows the latest turn only.
 * 2. A citation opens the source INLINE, because the chat has no source
 *    pane beside it. The source is the same node the explorer would show,
 *    fetched by the same call.
 * 3. The question input is docked at the bottom and clears after a
 *    question is sent.
 *
 * It is document-scoped because the API is: `streamAsk` requires a
 * `document_id`, so the chat asks about the active document and the
 * document selector stays visible in this mode.
 */
export function ChatView({
  documentId,
  documents,
  onDocumentChange,
  turns,
  asking,
  onAsk,
  onCite,
  source,
  sourceLoading,
  sourceError,
}: {
  documentId: string;
  documents: DocumentSummary[] | null;
  onDocumentChange(value: string): void;
  turns: AskTurn[];
  asking: boolean;
  onAsk(question: string, retryOf?: number | null): void;
  onCite(nodeId: string): void;
  source: NodeSource | null;
  sourceLoading: boolean;
  sourceError: string | null;
}) {
  // The chat owns its question field. The explorer's field keeps the
  // question after a run, because there it is the one place the question
  // is written down; a shared field would let the chat's clear-on-send
  // erase the explorer's record.
  const [question, setQuestion] = useState("");
  const canAsk = documentId !== "" && question.trim().length > 0;
  const transcriptEnd = useRef<HTMLDivElement | null>(null);

  // A new turn scrolls into view, because a transcript that leaves the
  // newest exchange below the fold reads as a chat that dropped it.
  useEffect(() => {
    transcriptEnd.current?.scrollIntoView({ block: "end" });
  }, [turns.length]);

  const documentName = (id: string): string => {
    const match = documents?.find((document) => document.id === id);
    return match === undefined ? id : match.name;
  };

  return (
    <section className="flex min-h-0 flex-1 flex-col overflow-hidden bg-ink-0 text-ink-9">
      <header className="border-b border-rule bg-ink-1 px-4 py-3 sm:px-6">
        <div className="flex items-center gap-3">
          <label
            htmlFor="chat-document-select"
            className="font-mono text-[11px] tracking-[0.12em] text-ink-7 uppercase"
          >
            {explorer.tree.documentLabel}
          </label>
          <select
            id="chat-document-select"
            value={documentId}
            onChange={(event) => onDocumentChange(event.target.value)}
            className="min-h-9 min-w-0 flex-1 border border-ink-4 bg-ink-2 px-2 text-sm text-ink-9 focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2"
          >
            {documents === null || documents.length === 0 ? (
              <option value="">{explorer.tree.documentPlaceholder}</option>
            ) : (
              documents.map((document) => (
                <option key={document.id} value={document.id}>
                  {document.name} ({document.page_count})
                </option>
              ))
            )}
          </select>
        </div>
        <p className="mt-1 font-mono text-[11px] text-ink-7">{explorer.chat.modeNote}</p>
      </header>

      <div className="min-h-0 flex-1 overflow-auto">
        {turns.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center px-6 py-8 text-center">
            <span
              data-brand-lockup
              className="flex items-center gap-2 font-mono text-[11px] uppercase tracking-[0.18em]"
            >
              <BrandMark size={32} className="shrink-0" />
              <span className="text-ink-9">{explorer.header.wordmark}</span>
            </span>
            <p className="mt-6 max-w-md text-base leading-relaxed text-ink-9">
              {explorer.chat.welcome}
            </p>
            <p className="mt-2 max-w-md text-sm leading-relaxed text-ink-7">
              {explorer.chat.welcomeHint}
            </p>
          </div>
        ) : (
          <ol
            aria-label={explorer.chat.transcriptLabel}
            className="flex flex-col gap-6 px-4 py-4 sm:px-6"
          >
            {turns.map((turn) => (
              <li key={turn.id} className="flex flex-col gap-6">
                <div className="border-l border-rule pl-3">
                  <p className="font-mono text-[11px] tracking-[0.12em] text-ink-7 uppercase">
                    {explorer.answer.questionLabel}
                  </p>
                  <p className="mt-1 text-sm leading-relaxed text-ink-9">{turn.question}</p>
                  <p className="mt-1 font-mono text-[11px] text-ink-7">
                    {explorer.tree.documentLabel}: {documentName(turn.documentId)}
                  </p>
                </div>

                {turn.steps.length > 0 ? <StepList steps={turn.steps} /> : null}

                {turn.status === "error" ? (
                  <div role="alert" className="border border-rule-strong bg-ink-2 px-3 py-2">
                    <h3 className="font-mono text-[11px] tracking-[0.18em] text-ink-7 uppercase">
                      {explorer.failure.apiTitle}
                    </h3>
                    <p className="mt-1 text-sm leading-relaxed text-ink-9">{turn.error}</p>
                    <button
                      type="button"
                      onClick={() => onAsk(turn.question, turn.id)}
                      className="mt-2 font-mono text-[11px] tracking-[0.12em] text-accent underline underline-offset-2 hover:opacity-80 focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2"
                    >
                      {explorer.failure.retryLabel}
                    </button>
                  </div>
                ) : (
                  <AnswerBody
                    tokenText={turn.tokenText}
                    response={turn.response}
                    error={null}
                    onCite={onCite}
                  />
                )}
              </li>
            ))}
            <div ref={transcriptEnd} aria-hidden="true" />
          </ol>
        )}
      </div>

      {/*
        The cited source, inline. The chat has no source pane beside it, so
        the node a citation points at is shown here instead. It is the same
        node the explorer's source pane shows, fetched by the same call, so
        the two views cannot disagree about what a citation refers to.
      */}
      {source !== null || sourceLoading || sourceError !== null ? (
        <section
          aria-labelledby="chat-source-heading"
          className="max-h-[40vh] shrink-0 overflow-auto border-t border-rule bg-ink-1 px-4 py-3 sm:px-6"
        >
          <h2
            id="chat-source-heading"
            className="font-mono text-[11px] tracking-[0.18em] text-ink-7 uppercase"
          >
            {explorer.source.title}
          </h2>
          <p className="mt-1 font-mono text-[11px] text-ink-7">
            {explorer.source.endpoint}
          </p>
          {sourceError !== null ? (
            <div role="alert" className="mt-2">
              <h3 className="font-mono text-[11px] tracking-[0.18em] text-ink-7 uppercase">
                {explorer.source.failureTitle}
              </h3>
              <p className="mt-1 text-sm leading-relaxed text-ink-9">{sourceError}</p>
            </div>
          ) : sourceLoading ? (
            <p className="mt-2 text-sm text-ink-7">{explorer.source.loading}</p>
          ) : source !== null ? (
            <div className="mt-2 flex flex-col gap-4">
              <SourceDetail source={source} />
            </div>
          ) : null}
        </section>
      ) : null}

      {/*
        The persistent disclaimer. It sits outside the scroll container on
        purpose, for the same legal reason as in the answer pane: it must
        be there before a question has been asked, and it must not scroll
        away with the transcript.
      */}
      <div
        role="note"
        className="flex items-start gap-2 border-t border-rule-strong bg-ink-2 px-4 py-3"
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
        onSubmit={(event) => {
          event.preventDefault();
          if (canAsk && !asking) {
            // The question goes onto the transcript, so the field clears:
            // a chat that keeps the question in the box asks it twice.
            onAsk(question.trim());
            setQuestion("");
          }
        }}
        className="border-t border-rule bg-ink-1 px-4 py-3 sm:px-6"
      >
        <label
          htmlFor="chat-question"
          className="font-mono text-[11px] tracking-[0.12em] text-ink-7 uppercase"
        >
          {explorer.answer.questionLabel}
        </label>
        <div className="mt-1 flex flex-col gap-2 sm:flex-row">
          <input
            id="chat-question"
            type="text"
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
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
            <span className="flex items-center justify-center gap-1.5">
              <PaperPlaneTilt size={16} weight="regular" aria-hidden="true" />
              {asking ? explorer.answer.askingLabel : explorer.answer.askLabel}
            </span>
          </button>
        </div>
      </form>
    </section>
  );
}
