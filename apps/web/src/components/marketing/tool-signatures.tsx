import type { ReactElement } from "react";
import { tools } from "@/lib/copy";

const KEYWORDS =
  /^(and|as|assert|async|await|break|class|continue|def|del|elif|else|except|False|finally|for|from|global|if|import|in|is|lambda|None|nonlocal|not|or|pass|raise|return|True|try|while|with|yield)$/;

const TOKEN =
  /(?<comment>#[^\n]*)|(?<string>"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')|(?<word>[A-Za-z_][A-Za-z0-9_]*)|(?<rest>\s+|[^\sA-Za-z_])/g;

const PLAIN = "text-ink-9";
const CLASS_BY_GROUP: Record<string, string> = {
  comment: "text-ink-7 italic",
  string: "text-ink-7",
  keyword: "text-accent",
  function: "text-ink-9",
  parameter: "text-ink-9",
  plain: PLAIN,
};

/**
 * Splits Python signature text into typed tokens. Every input character ends
 * up in exactly one output token, in order, so the rendered result stays
 * selectable and copyable as the original plain text.
 */
function tokenize(source: string): Array<{ kind: string; text: string }> {
  const tokens: Array<{ kind: string; text: string }> = [];
  TOKEN.lastIndex = 0;
  let cursor = 0;
  let match: RegExpExecArray | null;

  while ((match = TOKEN.exec(source)) !== null) {
    const groups = match.groups ?? {};
    if (match.index > cursor) {
      tokens.push({ kind: "plain", text: source.slice(cursor, match.index) });
    }

    const text = match[0];
    if (groups.comment !== undefined) {
      tokens.push({ kind: "comment", text });
    } else if (groups.string !== undefined) {
      tokens.push({ kind: "string", text });
    } else if (groups.word !== undefined) {
      const after = source.slice(TOKEN.lastIndex);
      if (KEYWORDS.test(text)) {
        tokens.push({ kind: "keyword", text });
      } else if (/^\s*\(/.test(after)) {
        tokens.push({ kind: "function", text });
      } else if (/^\s*:/.test(after)) {
        tokens.push({ kind: "parameter", text });
      } else {
        tokens.push({ kind: "plain", text });
      }
    } else {
      tokens.push({ kind: "plain", text });
    }

    cursor = match.index + text.length;
  }

  if (cursor < source.length) {
    tokens.push({ kind: "plain", text: source.slice(cursor) });
  }
  return tokens;
}

function Signature(): ReactElement {
  const tokens = tokenize(tools.signature);
  return (
    <pre className="m-0 px-5 py-4 font-mono text-[13px] leading-[1.7] text-ink-9">
      <code>
        {tokens.map((token, i) => (
          <span key={i} className={CLASS_BY_GROUP[token.kind] ?? PLAIN}>
            {token.text}
          </span>
        ))}
      </code>
    </pre>
  );
}

export function ToolSignatures(): React.JSX.Element {
  return (
    <section
      id="tools"
      aria-labelledby="tools-heading"
      className="scroll-mt-24 bg-ink-1 py-20 lg:py-28"
    >
      <div className="mx-auto flex max-w-6xl flex-col gap-10 px-4 sm:px-6 lg:px-8">
        <div className="flex flex-col gap-4">
          <h2
            id="tools-heading"
            className="max-w-[20ch] font-sans text-3xl font-semibold leading-tight tracking-tight text-ink-9 md:text-4xl"
          >
            {tools.headline}
          </h2>
          <p className="max-w-[66ch] text-base leading-relaxed text-ink-7 md:text-lg">
            {tools.lead}
          </p>
        </div>

        <div
          role="region"
          aria-label="Tool signatures, horizontally scrollable code block"
          tabIndex={0}
          className="h-[18rem] w-full overflow-x-auto border border-ink-4/60 bg-ink-2 focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2"
        >
          <Signature />
        </div>

        <p className="max-w-[66ch] text-sm leading-relaxed text-ink-7">
          {tools.caption}
        </p>
      </div>
    </section>
  );
}