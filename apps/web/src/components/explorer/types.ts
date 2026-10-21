/**
 * The wire shapes of the Tuklas API, as the explorer consumes them.
 *
 * Every type here is a transcription of a frozen contract in `docs/api.md`:
 * the document list (section 1-2), the tree (section 2), the node source
 * (section 3), the AskResponse (section 3), and the uniform error body
 * (section 1). Nothing is invented here: a field that the API does not send
 * is not in these types, and a field the API may omit is optional.
 */

/** One row of `GET /v1/documents` (api.md section 2). */
export interface DocumentSummary {
  id: string;
  name: string;
  page_count: number;
  created_at: string;
}

/** The 1-indexed pagination envelope (api.md section 1). */
export interface Pagination {
  page: number;
  page_size: number;
  total_items: number;
  total_pages: number;
}

/** The 200 body of `GET /v1/documents`. */
export interface DocumentList {
  data: DocumentSummary[];
  pagination: Pagination;
}

/** One node of `GET /v1/documents/{id}/tree` (api.md section 2). */
export interface TreeNode {
  id: string;
  type: string;
  title: string;
  depth: number;
  position: number;
  page_start: number | null;
  page_end: number | null;
  child_count: number;
  children: TreeNode[];
}

/** The tree response: a root plus the root's children. */
export interface TreeResponse {
  root: TreeNode;
  children: TreeNode[];
}

/** The 200 body of `GET /v1/nodes/{id}/source` (api.md section 3). */
export interface NodeSource {
  node_id: string;
  text: string;
  page_start: number | null;
  page_end: number | null;
  hierarchy_path: string[];
}

/** One citation of an AskResponse (api.md section 3). */
export interface Citation {
  n: number;
  node_id: string;
  label: string;
  page_start: number | null;
  page_end: number | null;
}

/** The AskResponse (api.md section 3). `tokens_source` is optional. */
export interface AskResponse {
  answer_markdown: string;
  citations: Citation[];
  evidence_ids: string[];
  iterations: number;
  latency_ms: number;
  tokens: { in: number; out: number };
  tokens_source?: "estimated" | "provider";
  insufficient: boolean;
}

/** The uniform error body every endpoint returns on failure (api.md section 1). */
export interface ErrorBody {
  error: {
    code: string;
    message: string;
    details: Record<string, unknown>;
  };
}

/**
 * One streamed step, in the order the server sent it.
 *
 * The union mirrors the frozen event sequence of api.md section 3:
 * `plan` -> (`tool_call`, `tool_result`)* -> `evidence` -> `token`* -> `done`.
 * The explorer keeps them in one ordered list so the rendered order IS the
 * received order, and a server that reordered the sequence would show the
 * reorder rather than hide it.
 */
export type Step =
  | { kind: "plan"; subQuestions: string[] }
  | { kind: "tool_call"; tool: string; args: Record<string, unknown> }
  | { kind: "tool_result"; tool: string; resultIds: string[]; relevant: boolean }
  | { kind: "evidence"; evidenceIds: string[]; insufficient: boolean };
