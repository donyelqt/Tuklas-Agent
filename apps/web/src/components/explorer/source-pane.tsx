import { explorer } from "@/lib/explorer-copy";
import type { NodeSource } from "./types";

/**
 * The source pane: the text of one node, its page span and its hierarchy
 * path.
 *
 * The data is the real response of `GET /v1/nodes/{id}/source`
 * (docs/api.md section 3): the node text, the page range, and the
 * hierarchy path as the ancestor titles, root first.
 */
export function SourcePane({
  source,
  loading,
  error,
}: {
  source: NodeSource | null;
  loading: boolean;
  error: string | null;
}) {
  return (
    <section
      aria-labelledby="source-pane-heading"
      className="flex h-full min-h-0 flex-col overflow-hidden border-rule bg-ink-1"
    >
      <header className="border-b border-rule px-4 py-3">
        <h2
          id="source-pane-heading"
          className="font-mono text-[11px] tracking-[0.18em] text-ink-7 uppercase"
        >
          {explorer.source.title}
        </h2>
        <p className="mt-1 font-mono text-[11px] text-ink-7">
          {explorer.source.endpoint}
        </p>
      </header>

      <div className="min-h-0 flex-1 overflow-auto">
        {error !== null ? (
          // The source failure belongs to the source pane. It used to
          // share the ask error, so a failed fetch and a failed answer
          // were rendered in the same place as the same banner.
          <div role="alert" className="px-4 py-6">
            <h3 className="font-mono text-[11px] tracking-[0.18em] text-ink-7 uppercase">
              {explorer.source.failureTitle}
            </h3>
            <p className="mt-2 text-sm leading-relaxed text-ink-9">{error}</p>
          </div>
        ) : loading ? (
          <p className="px-4 py-6 text-sm text-ink-7">{explorer.source.loading}</p>
        ) : source === null ? (
          <p className="px-4 py-6 text-sm leading-relaxed text-ink-7">
            {explorer.source.empty}
          </p>
        ) : (
          <div className="flex flex-col gap-4 px-4 py-4">
            <SourceDetail source={source} />
          </div>
        )}
      </div>
    </section>
  );
}

/**
 * The detail of one node: its id, its page span, its hierarchy path and
 * its text.
 *
 * It is a separate export because the chat view shows the cited node
 * inline, and the two views have to agree about what a citation refers
 * to. Rendering the detail twice would let the explorer's source pane and
 * the chat's inline source drift apart.
 */
export function SourceDetail({ source }: { source: NodeSource }) {
  return (
    <>
      <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 font-mono text-[11px]">
        <dt className="tracking-[0.12em] text-ink-7 uppercase">
          {explorer.source.nodeLabel}
        </dt>
        <dd className="break-all text-ink-9">{source.node_id}</dd>
        <dt className="tracking-[0.12em] text-ink-7 uppercase">
          {explorer.source.pagesLabel}
        </dt>
        <dd className="tabular-nums text-ink-9">
          {source.page_start === null ? "-" : source.page_start}
          {source.page_end !== null && source.page_end !== source.page_start
            ? ` - ${source.page_end}`
            : ""}
        </dd>
        <dt className="tracking-[0.12em] text-ink-7 uppercase">
          {explorer.source.pathLabel}
        </dt>
        <dd className="text-ink-9">
          {source.hierarchy_path.length > 0 ? source.hierarchy_path.join(" / ") : "-"}
        </dd>
      </dl>
      <div className="border-t border-rule pt-4">
        {source.text.length > 0 ? (
          <p className="font-sans text-sm leading-relaxed whitespace-pre-wrap text-ink-9">
            {source.text}
          </p>
        ) : (
          <p className="text-sm text-ink-7">{explorer.source.emptyText}</p>
        )}
      </div>
    </>
  );
}
