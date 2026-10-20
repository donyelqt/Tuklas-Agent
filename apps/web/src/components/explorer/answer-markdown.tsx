import type { ReactNode } from "react";
import { explorer } from "@/lib/explorer-copy";

/**
 * The answer renderer for the markdown the model returned.
 *
 * The renderer is deliberately small: it handles the block forms the
 * generation prompt can produce (headings, paragraphs, bullet lists,
 * ordered lists) and the inline forms (bold, italic, code, and the `[n]`
 * citation markers the prompt asks the model to write). Anything it does
 * not recognise is rendered as text, so a model that returns an unusual
 * shape still shows its content rather than a gap.
 *
 * The citation markers are the load-bearing part: the prompt tells the
 * model to cite an evidence entry by its number alone, so `[1]` in the text
 * is a reference to the first citation of the AskResponse. A marker with
 * no matching citation is left as plain text, because the explorer will
 * not invent a target for it.
 */
export function AnswerMarkdown({
  text,
  citationTargets,
  onCite,
}: {
  text: string;
  citationTargets: Record<string, string>;
  onCite(nodeId: string): void;
}) {
  const blocks = text.split(/\n{2,}/);
  return (
    <div className="flex flex-col gap-3">
      {blocks.map((block, index) => {
        const heading = /^(#{1,6})\s+(.*)$/.exec(block);
        if (heading !== null) {
          const level = heading[1].length;
          const size = level === 1 ? "text-lg" : level === 2 ? "text-base" : "text-sm";
          return (
            <p
              key={index}
              className={`${size} font-semibold tracking-tight text-ink-9`}
            >
              {renderInline(heading[2], citationTargets, onCite)}
            </p>
          );
        }
        const lines = block.split("\n");
        if (lines.every((line) => /^\s*[-*]\s+/.test(line))) {
          return (
            <ul key={index} className="flex list-disc flex-col gap-1 pl-5">
              {lines.map((line, lineIndex) => (
                <li
                  key={lineIndex}
                  className="text-sm leading-relaxed text-ink-9 marker:text-ink-7"
                >
                  {renderInline(
                    line.replace(/^\s*[-*]\s+/, ""),
                    citationTargets,
                    onCite,
                  )}
                </li>
              ))}
            </ul>
          );
        }
        if (lines.every((line) => /^\s*\d+[.)]\s+/.test(line))) {
          return (
            <ol key={index} className="flex list-decimal flex-col gap-1 pl-5">
              {lines.map((line, lineIndex) => (
                <li
                  key={lineIndex}
                  className="text-sm leading-relaxed text-ink-9 marker:text-ink-7"
                >
                  {renderInline(
                    line.replace(/^\s*\d+[.)]\s+/, ""),
                    citationTargets,
                    onCite,
                  )}
                </li>
              ))}
            </ol>
          );
        }
        return (
          <p key={index} className="text-sm leading-relaxed text-ink-9">
            {renderInline(block, citationTargets, onCite)}
          </p>
        );
      })}
    </div>
  );
}

const INLINE_PATTERN = /(\*\*[^*]+\*\*|\*[^*\n]+\*|`[^`]+`|\[\d+\])/g;

function renderInline(
  text: string,
  citationTargets: Record<string, string>,
  onCite: (nodeId: string) => void,
): ReactNode {
  const parts = text.split(INLINE_PATTERN);
  return parts.map((part, index) => {
    if (part.length === 0) {
      return null;
    }
    if (part.startsWith("**") && part.endsWith("**")) {
      return (
        <strong key={index} className="font-semibold text-ink-9">
          {part.slice(2, -2)}
        </strong>
      );
    }
    if (part.startsWith("*") && part.endsWith("*") && part.length > 2) {
      return (
        <em key={index} className="italic">
          {part.slice(1, -1)}
        </em>
      );
    }
    if (part.startsWith("`") && part.endsWith("`") && part.length > 2) {
      return (
        <code
          key={index}
          className="border border-rule bg-ink-2 px-1 font-mono text-[0.85em] text-ink-9"
        >
          {part.slice(1, -1)}
        </code>
      );
    }
    const marker = /^\[(\d+)\]$/.exec(part);
    if (marker !== null) {
      const target = citationTargets[marker[1]];
      if (target === undefined) {
        return <span key={index}>{part}</span>;
      }
      return (
        <button
          key={index}
          type="button"
          onClick={() => onCite(target)}
          title={explorer.answer.citationsTitle}
          className="mx-0.5 font-mono text-[11px] text-accent underline underline-offset-2 hover:opacity-80 focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2"
        >
          {part}
        </button>
      );
    }
    return <span key={index}>{part}</span>;
  });
}
