'use client';

import { useCallback, useEffect, useRef, useState } from "react";
import type * as React from "react";

/** Stable hook for browser verification. */
export const COPY_COMMAND_TEST_ID = "copy-command";

/** How long the confirmation label stays on screen, in ms. */
const CONFIRM_MS = 1600;

const MONO_META =
  "font-mono text-[11px] uppercase tracking-[0.18em] text-ink-7";

export function CopyCommand({
  command,
  label,
  className,
}: {
  command: string;
  label?: string;
  className?: string;
}): React.JSX.Element {
  const [copied, setCopied] = useState(false);
  const timerRef = useRef<number | null>(null);

  const clearTimer = useCallback(() => {
    if (timerRef.current !== null) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  useEffect(() => clearTimer, [clearTimer]);

  const handleCopy = useCallback(async () => {
    clearTimer();
    try {
      await navigator.clipboard.writeText(command);
      setCopied(true);
      timerRef.current = window.setTimeout(() => {
        timerRef.current = null;
        setCopied(false);
      }, CONFIRM_MS);
    } catch {
      // Clipboard refused. Leave the label alone rather than claiming a copy
      // that did not happen.
      setCopied(false);
    }
  }, [clearTimer, command]);

  const containerClass = [
    "flex flex-wrap items-center gap-x-3 gap-y-2",
    "border border-ink-4/60 bg-ink-2 px-3 py-2",
    className,
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <div data-testid={COPY_COMMAND_TEST_ID} className={containerClass}>
      <div className="flex min-w-0 flex-1 flex-wrap items-baseline gap-x-2 gap-y-1">
        <span aria-hidden="true" className={`shrink-0 ${MONO_META}`}>
          $
        </span>
        {label === undefined ? null : (
          <span className={`shrink-0 ${MONO_META}`}>{label}</span>
        )}
        <code className="min-w-0 break-words font-mono text-ink-9 [overflow-wrap:anywhere]">
          {command}
        </code>
      </div>

      <div className="relative ml-auto shrink-0">
        <button
          type="button"
          onClick={handleCopy}
          aria-label="Copy command"
          className={[
            "inline-flex min-h-11 min-w-11 items-center justify-center",
            "px-2 py-2 font-mono text-[11px] uppercase tracking-[0.18em]",
            "transition-colors duration-150 hover:text-ink-9",
            "active:translate-y-px",
            "focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2",
            copied ? "text-accent" : "text-ink-7",
          ].join(" ")}
        >
          {copied ? "copied" : "copy"}
        </button>
        <span
          role="status"
          aria-live="polite"
          className="absolute size-px overflow-hidden whitespace-nowrap [clip-path:inset(50%)]"
        >
          {copied ? "copied" : ""}
        </span>
      </div>
    </div>
  );
}

export default CopyCommand;
