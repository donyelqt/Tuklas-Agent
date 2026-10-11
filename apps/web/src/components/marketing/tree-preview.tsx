import type { ReactNode } from "react";
import { hero } from "@/lib/copy";

/**
 * TreePreview renders the tree pane of the document explorer (PRD F6) from the
 * real response shapes of `GET /v1/documents/{id}/tree?depth=3` (docs/api.md
 * section 2) and `GET /v1/nodes/{id}` (docs/api.md section 3). Every id, type,
 * title and page number below is a field the API returns: the Book IV / Title
 * II / Chapter 1 path with Articles 1318, 1319 and 1320 as siblings, the
 * example the product docs use throughout.
 *
 * Static Server Component: no client directive, no state, no effects, no
 * event handlers. Rendering it twice produces identical markup.
 */

type NodeType = "Book" | "Title" | "Chapter" | "Section" | "Article";

/** Node entry of `GET /v1/documents/{id}/tree?depth=3` (docs/api.md section 2). */
interface TreeNode {
  id: string;
  type: NodeType;
  title: string;
  depth: number;
  position: number;
  page_start: number;
  child_count: number;
  /** Reference label such as `[1]`, required by PRD F6 (PRD F6 cites tree nodes). */
  citation?: string;
}

/** `root` as posted by `POST /v1/documents` as `tree_root`: no page number. */
interface TreeRoot {
  id: string;
  type: NodeType;
  title: string;
  child_count: number;
}

/** The tree endpoint response: a root plus a flat, depth-ordered node list. */
interface TreeResponse {
  root: TreeRoot;
  children: TreeNode[];
}

/** A node once the flat depth-ordered list has been nested under the root. */
interface NestedNode {
  id: string;
  type: NodeType;
  title: string;
  page_start?: number;
  citation?: string;
  nodes: NestedNode[];
}

const LABELS = {
  endpoint: "GET /v1/documents/{id}/tree?depth=3",
  page: "p.",
} as const;

const TREE_RESPONSE: TreeResponse = {
  root: {
    id: "node_book_iv",
    type: "Book",
    title: "Book IV",
    child_count: 1,
  },
  children: [
    {
      id: "node_title_ii",
      type: "Title",
      title: "Title II",
      depth: 1,
      position: 1,
      page_start: 1743,
      child_count: 1,
    },
    {
      id: "node_chapter_1",
      type: "Chapter",
      title: "Chapter 1",
      depth: 2,
      position: 1,
      page_start: 1743,
      child_count: 3,
    },
    {
      id: "node_article_1318",
      type: "Article",
      title: "Article 1318",
      depth: 3,
      position: 1,
      page_start: 1743,
      child_count: 0,
      citation: "[1]",
    },
    {
      id: "node_article_1319",
      type: "Article",
      title: "Article 1319",
      depth: 3,
      position: 2,
      page_start: 1744,
      child_count: 0,
    },
    {
      id: "node_article_1320",
      type: "Article",
      title: "Article 1320",
      depth: 3,
      position: 3,
      page_start: 1745,
      child_count: 0,
    },
  ],
};

/**
 * Nest the flat, depth-ordered `children` list under `root`. The endpoint sends
 * `depth` and `position` rather than `parent_id`, so the parent of an entry is
 * the closest still-open node one level above it.
 */
function nestTree(response: TreeResponse): NestedNode {
  const root: NestedNode = { ...response.root, nodes: [] };
  const open: NestedNode[] = [root];
  const ordered = [...response.children].sort(
    (a, b) => a.depth - b.depth || a.position - b.position,
  );

  for (const entry of ordered) {
    const branch: NestedNode = {
      id: entry.id,
      type: entry.type,
      title: entry.title,
      page_start: entry.page_start,
      citation: entry.citation,
      nodes: [],
    };
    while (open.length - 1 >= entry.depth) open.pop();
    const parent = open[open.length - 1];
    if (parent) parent.nodes.push(branch);
    open.push(branch);
  }

  return root;
}

const TREE = nestTree(TREE_RESPONSE);

function NodeMeta({ node }: { node: NestedNode }): ReactNode {
  return (
    <span className="ml-auto flex shrink-0 items-baseline gap-3">
      <span className="hidden truncate font-mono text-[11px] text-ink-7 lg:inline">
        {node.id}
      </span>
      {node.page_start !== undefined ? (
        <span className="font-mono text-[11px] tabular-nums text-ink-7">
          {LABELS.page} {node.page_start}
        </span>
      ) : null}
    </span>
  );
}

function NodeRow({ node, divider }: { node: NestedNode; divider: boolean }): ReactNode {
  return (
    <li className={divider ? "border-t border-ink-4/60" : undefined}>
      <div className="flex items-baseline gap-3 py-1.5 pr-4 pl-4">
        {node.citation ? (
          <span className="shrink-0 font-mono text-[11px] text-accent">
            {node.citation}
          </span>
        ) : null}
        <span className="min-w-0 flex-1 truncate font-sans text-sm text-ink-9">
          {node.title}
        </span>
        <NodeMeta node={node} />
      </div>
      {node.nodes.length > 0 ? (
        <div className="ml-4 border-l border-ink-4/60 pl-4">
          <NodeList nodes={node.nodes} />
        </div>
      ) : null}
    </li>
  );
}

function NodeList({ nodes }: { nodes: NestedNode[] }): ReactNode {
  return (
    <ul>
      {nodes.map((node, index) => (
        <NodeRow key={node.id} node={node} divider={index > 0} />
      ))}
    </ul>
  );
}

export function TreePreview() {
  return (
    <div data-testid="tree-preview" className="w-full max-w-md border border-ink-4/60 bg-ink-1">
      <div className="border-b border-ink-4/60 px-4 py-3">
        <span className="font-mono text-[11px] tracking-[0.18em] text-ink-7 uppercase">
          {LABELS.endpoint}
        </span>
      </div>
      <div className="bg-ink-2">
        <NodeList nodes={[TREE]} />
      </div>
      <div className="border-t border-ink-4/60 px-4 py-2 font-mono text-[11px] leading-snug text-ink-7">
        {hero.previewCaption}
      </div>
    </div>
  );
}