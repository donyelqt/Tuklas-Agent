import { CaretRight } from "@phosphor-icons/react/ssr";
import { explorer } from "@/lib/explorer-copy";
import type { TreeNode } from "./types";

/**
 * The tree pane: the document's Book / Title / Chapter / Article structure.
 *
 * The data is the real response of `GET /v1/documents/{id}/tree?depth=N`
 * (docs/api.md section 2): a root plus its children, each node carrying
 * its type, title, page span and child count. The visual language is the
 * one the marketing route's tree preview already established: hairline
 * rules, a mono node id, a page number, no radius and no shadow.
 */

const FOCUS =
  "focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2";
const EASE =
  "transition-[background-color,border-color,color,opacity] duration-150 ease-[cubic-bezier(.4,0,.2,1)]";

function NodeRow({
  node,
  selectedId,
  onSelect,
}: {
  node: TreeNode;
  selectedId: string | null;
  onSelect(nodeId: string): void;
}) {
  const selected = node.id === selectedId;
  return (
    <li>
      <button
        type="button"
        onClick={() => onSelect(node.id)}
        aria-current={selected ? "true" : undefined}
        className={`flex w-full items-baseline gap-3 py-1.5 pr-3 pl-3 text-left ${FOCUS} ${EASE} ${
          selected
            ? "bg-ink-3 text-ink-9"
            : "text-ink-7 hover:bg-ink-2 hover:text-ink-9"
        }`}
      >
        <span className="min-w-0 flex-1 truncate font-sans text-sm">
          {node.title}
        </span>
        <span className="font-mono text-[11px] tracking-[0.12em] text-ink-7 uppercase">
          {node.type}
        </span>
        {node.page_start !== null ? (
          <span className="font-mono text-[11px] tabular-nums text-ink-7">
            {explorer.tree.pageLabel} {node.page_start}
          </span>
        ) : null}
      </button>
      {node.children.length > 0 ? (
        <ul className="ml-3 border-l border-rule pl-3">
          {node.children.map((child) => (
            <NodeRow
              key={child.id}
              node={child}
              selectedId={selectedId}
              onSelect={onSelect}
            />
          ))}
        </ul>
      ) : null}
    </li>
  );
}

/** One row of the tree, with its children nested under it. */
function NodeList({
  nodes,
  selectedId,
  onSelect,
}: {
  nodes: TreeNode[];
  selectedId: string | null;
  onSelect(nodeId: string): void;
}) {
  return (
    <ul>
      {nodes.map((node) => (
        <NodeRow
          key={node.id}
          node={node}
          selectedId={selectedId}
          onSelect={onSelect}
        />
      ))}
    </ul>
  );
}

/** Whether the tree contains the node the source pane is showing. */
function containsNode(node: TreeNode, nodeId: string): boolean {
  if (node.id === nodeId) {
    return true;
  }
  return node.children.some((child) => containsNode(child, nodeId));
}

export function TreePane({
  root,
  loading,
  selectedId,
  onSelect,
  depth,
  onDepthChange,
}: {
  root: TreeNode | null;
  loading: boolean;
  selectedId: string | null;
  onSelect(nodeId: string): void;
  depth: number;
  onDepthChange(depth: number): void;
}) {
  return (
    <section
      aria-labelledby="tree-pane-heading"
      className="flex h-full min-h-0 flex-col overflow-hidden border-rule bg-ink-1"
    >
      <header className="border-b border-rule px-4 py-3">
        <h2
          id="tree-pane-heading"
          className="font-mono text-[11px] tracking-[0.18em] text-ink-7 uppercase"
        >
          {explorer.tree.title}
        </h2>
        <p className="mt-1 font-mono text-[11px] text-ink-7">{explorer.tree.endpoint}</p>
      </header>

      <div className="flex items-center gap-2 border-b border-rule px-4 py-2">
        <label
          htmlFor="tree-depth"
          className="font-mono text-[11px] tracking-[0.12em] text-ink-7 uppercase"
        >
          {explorer.tree.depthLabel}
        </label>
        <select
          id="tree-depth"
          value={depth}
          onChange={(event) => onDepthChange(Number(event.target.value))}
          className={`border border-ink-4 bg-ink-2 px-2 py-1 font-mono text-[11px] text-ink-9 ${FOCUS} ${EASE}`}
        >
          {[3, 4, 5, 6, 7].map((value) => (
            <option key={value} value={value}>
              {value}
            </option>
          ))}
        </select>
      </div>

      <div className="min-h-0 flex-1 overflow-auto">
        {loading ? (
          <p className="px-4 py-6 text-sm leading-relaxed text-ink-7">
            {explorer.tree.loading}
          </p>
        ) : root === null ? (
          <p className="px-4 py-6 text-sm leading-relaxed text-ink-7">
            {explorer.tree.empty}
          </p>
        ) : (
          <>
            {selectedId !== null && !containsNode(root, selectedId) ? (
              <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-rule px-4 py-2">
                <p className="font-mono text-[11px] leading-relaxed text-ink-7">
                  {explorer.tree.outsideLoadedLevels}
                </p>
                <button
                  type="button"
                  onClick={() => onDepthChange(depth + 1)}
                  className={`font-mono text-[11px] text-accent underline underline-offset-2 hover:opacity-80 ${FOCUS}`}
                >
                  {explorer.tree.loadDeeper}
                </button>
              </div>
            ) : null}
            <NodeList
              nodes={[root]}
              selectedId={selectedId}
              onSelect={onSelect}
            />
            {root.children.length > 0 &&
            root.children.some((child) => child.child_count > child.children.length) ? (
              <p className="flex items-center gap-2 border-t border-rule px-4 py-2 font-mono text-[11px] text-ink-7">
                <CaretRight size={12} weight="regular" aria-hidden="true" />
                {explorer.tree.truncated}
              </p>
            ) : null}
          </>
        )}
      </div>
    </section>
  );
}
