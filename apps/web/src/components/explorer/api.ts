import { site } from "@/lib/site";
import type {
  AskResponse,
  DocumentList,
  ErrorBody,
  NodeSource,
  Step,
  TreeResponse,
} from "./types";

/**
 * The API client for the document explorer.
 *
 * The base URL is read from `NEXT_PUBLIC_API_BASE_URL` (default
 * `http://127.0.0.1:8000`) through `lib/site.ts`, the same module the
 * marketing route reads, so there is exactly one place the URL is decided.
 * The explorer is a browser-side runtime: it calls the local API from the
 * browser, which is why it can be a static export.
 */
export const API_BASE_URL: string = site.apiBaseUrl;

/**
 * A failure the explorer can show without inventing anything.
 *
 * `kind` separates the two failures a user can act on differently:
 * `unreachable` means the request never completed (the API is down, the
 * port is wrong, or the browser blocked the cross-origin read), and `http`
 * means the API answered with an error body. The `code` is the frozen code
 * from api.md section 1 when the API sent one, and a client-side label
 * otherwise. The explorer never turns a failure into an empty result: an
 * empty pane would read as "no matches", which is a different claim.
 */
export interface ExplorerFailure {
  kind: "unreachable" | "http";
  code: string;
  message: string;
  details: Record<string, unknown>;
  status: number | null;
}

export class ExplorerError extends Error {
  readonly failure: ExplorerFailure;

  constructor(failure: ExplorerFailure) {
    super(failure.message);
    this.name = "ExplorerError";
    this.failure = failure;
  }
}

const UNREACHABLE_CODE = "API_UNREACHABLE";
const UNEXPECTED_CODE = "UNEXPECTED_RESPONSE";

export function toUnreachableFailure(error: unknown): ExplorerFailure {
  const reason = error instanceof Error ? error.message : String(error);
  return {
    kind: "unreachable",
    code: UNREACHABLE_CODE,
    message: `The request to ${API_BASE_URL} did not complete. ${reason}`,
    details: {},
    status: null,
  };
}

/** Read the uniform error body of a failed response (api.md section 1). */
async function readFailure(response: Response): Promise<ExplorerError> {
  let body: unknown = null;
  try {
    body = await response.json();
  } catch {
    body = null;
  }
  const error = (body as ErrorBody | null)?.error;
  if (error && typeof error.code === "string" && error.code.length > 0) {
    return new ExplorerError({
      kind: "http",
      code: error.code,
      message: typeof error.message === "string" ? error.message : "",
      details: error.details && typeof error.details === "object" ? error.details : {},
      status: response.status,
    });
  }
  return new ExplorerError({
    kind: "http",
    code: UNEXPECTED_CODE,
    message: `The API answered ${response.status} without an error body.`,
    details: {},
    status: response.status,
  });
}

/** `GET /v1/documents?page=1&pageSize=20` (api.md section 1-2). */
export async function fetchDocuments(signal: AbortSignal): Promise<DocumentList> {
  const response = await fetch(`${API_BASE_URL}/v1/documents?page=1&pageSize=20`, {
    headers: { Accept: "application/json" },
    cache: "no-store",
    signal,
  });
  if (!response.ok) {
    throw await readFailure(response);
  }
  return (await response.json()) as DocumentList;
}

/** `GET /v1/documents/{id}/tree?depth=N` (api.md section 2). */
export async function fetchTree(
  documentId: string,
  depth: number,
  signal: AbortSignal,
): Promise<TreeResponse> {
  const response = await fetch(
    `${API_BASE_URL}/v1/documents/${encodeURIComponent(documentId)}/tree?depth=${depth}`,
    { headers: { Accept: "application/json" }, cache: "no-store", signal },
  );
  if (!response.ok) {
    throw await readFailure(response);
  }
  return (await response.json()) as TreeResponse;
}

export async function fetchNodeSource(
  nodeId: string,
  signal: AbortSignal,
): Promise<NodeSource> {
  const response = await fetch(
    `${API_BASE_URL}/v1/nodes/${encodeURIComponent(nodeId)}/source`,
    { headers: { Accept: "application/json" }, cache: "no-store", signal },
  );
  if (!response.ok) {
    throw await readFailure(response);
  }
  return (await response.json()) as NodeSource;
}

interface SseFrame {
  event: string;
  data: string;
}

/**
 * Split an SSE frame into its fields.
 *
 * The server writes `event: <name>` and one `data: <json>` line per frame
 * (api.md section 3). A comment line, an `id:` field or a `retry:` field
 * would be ignored rather than break the parse, and a `data:` field split
 * over several lines is joined with a newline, as the SSE specification
 * requires.
 */
function parseFrame(raw: string): SseFrame | null {
  let event = "";
  const dataLines: string[] = [];
  for (const line of raw.split("\n")) {
    if (line.length === 0 || line.startsWith(":")) {
      continue;
    }
    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? "" : line.slice(colon + 1);
    if (value.startsWith(" ")) {
      value = value.slice(1);
    }
    if (field === "event") {
      event = value;
    } else if (field === "data") {
      dataLines.push(value);
    }
  }
  if (event.length === 0 && dataLines.length === 0) {
    return null;
  }
  return { event, data: dataLines.join("\n") };
}

/** Narrow an unknown JSON value to a record. */
function asRecord(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null
    ? (value as Record<string, unknown>)
    : {};
}

/** Narrow an unknown JSON value to a list of strings. */
function asStringList(value: unknown): string[] {
  return Array.isArray(value) ? value.map((item) => String(item)) : [];
}

/**
 * Check a `done` payload against the frozen AskResponse shape (api.md
 * section 3).
 *
 * The `done` event carries the authoritative answer, and the interface
 * reads its fields directly. A payload that is not an AskResponse would
 * therefore be read as one, and the read would throw inside a render,
 * which takes the whole page down rather than showing a failed run.
 *
 * So the shape is checked here, where a failure can still be reported as
 * the contract violation it is. The check is on the fields the interface
 * reads, not on a full structural clone: it asks "is this the shape this
 * code will read", not "is this the shape the server intended".
 */
function isAskResponse(value: unknown): value is AskResponse {
  if (typeof value !== "object" || value === null) {
    return false;
  }
  const record = value as Record<string, unknown>;
  if (typeof record.answer_markdown !== "string") {
    return false;
  }
  if (!Array.isArray(record.citations)) {
    return false;
  }
  if (!Array.isArray(record.evidence_ids)) {
    return false;
  }
  if (typeof record.insufficient !== "boolean") {
    return false;
  }
  return true;
}

/**
 * Turn one SSE payload into a step of the frozen sequence.
 *
 * The payload is the schema object itself (api.md section 3): the event name
 * travels in the `event:` line, never inside the JSON. A payload the
 * explorer does not recognise is dropped rather than guessed at, so a
 * future event cannot be rendered as a step it is not.
 */
function toStep(event: string, payload: unknown): Step | null {
  const data = asRecord(payload);
  switch (event) {
    case "plan":
      return { kind: "plan", subQuestions: asStringList(data.sub_questions) };
    case "tool_call":
      return {
        kind: "tool_call",
        tool: String(data.tool ?? ""),
        args: asRecord(data.args),
      };
    case "tool_result":
      return {
        kind: "tool_result",
        tool: String(data.tool ?? ""),
        resultIds: asStringList(data.result_ids),
        relevant: Boolean(data.relevant),
      };
    case "evidence":
      return {
        kind: "evidence",
        evidenceIds: asStringList(data.evidence_ids),
        insufficient: Boolean(data.insufficient),
      };
    default:
      return null;
  }
}

export interface AskStreamHandlers {
  /** A live step of the frozen sequence, in the order the server sent it. */
  onStep(step: Step): void;
  /** One `token` event. The deltas are a replay that arrives in a burst. */
  onToken(text: string): void;
  onDone(response: AskResponse): void;
}

/**
 * `POST /v1/ask/stream`, read as a stream (api.md section 3).
 *
 * The response is consumed frame by frame as it arrives, so the plan, the
 * tool calls, the tool results and the evidence event reach the interface
 * while the agent is still working. The `token` events are a replay of the
 * model's deltas that the advice oracle releases in one burst once the whole
 * answer has been checked, so they are appended as text but never presented
 * as live typing.
 */
export async function streamAsk(
  body: { document_id: string; question: string; max_iterations?: number },
  handlers: AskStreamHandlers,
  signal: AbortSignal,
): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/v1/ask/stream`, {
    method: "POST",
    headers: {
      Accept: "text/event-stream",
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
    signal,
  });
  if (!response.ok) {
    // A bad body or a down LLM answers with the uniform error body, not
    // with an SSE stream (api.md section 1).
    throw await readFailure(response);
  }
  if (!response.body) {
    throw new ExplorerError({
      kind: "unreachable",
      code: UNEXPECTED_CODE,
      message: "The API answered with a stream the browser could not read.",
      details: {},
      status: null,
    });
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) {
        break;
      }
      buffer += decoder.decode(value, { stream: true });
      // Normalise CRLF line endings so the frame boundary is one rule.
      buffer = buffer.replace(/\r\n/g, "\n");
      let boundary = buffer.indexOf("\n\n");
      while (boundary !== -1) {
        const frame = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        handleFrame(frame, handlers);
        boundary = buffer.indexOf("\n\n");
      }
    }
  } catch (error) {
    // A stream that failed is closed, not left half-open: the reader is
    // cancelled so the connection does not outlive the failure.
    await reader.cancel().catch(() => undefined);
    throw error;
  }
}

function handleFrame(raw: string, handlers: AskStreamHandlers): void {
  const frame = parseFrame(raw);
  if (frame === null) {
    return;
  }
  let payload: unknown;
  try {
    payload = frame.data.length > 0 ? JSON.parse(frame.data) : {};
  } catch {
    // A frame the explorer cannot read is a contract violation, and saying
    // so is better than dropping it and showing a partial run as complete.
    throw new ExplorerError({
      kind: "http",
      code: UNEXPECTED_CODE,
      message: `The API sent a ${frame.event} event the explorer could not read.`,
      details: {},
      status: null,
    });
  }
  if (frame.event === "token") {
    const text = asRecord(payload).text;
    handlers.onToken(typeof text === "string" ? text : "");
    return;
  }
  if (frame.event === "done") {
    const askResponse = asRecord(payload).ask_response;
    if (!isAskResponse(askResponse)) {
      // The `done` event carries the authoritative answer (api.md section
      // 3). A payload that is not an AskResponse is a contract violation,
      // and saying so is better than rendering half an answer.
      throw new ExplorerError({
        kind: "http",
        code: UNEXPECTED_CODE,
        message: "The API sent a done event the explorer could not read.",
        details: {},
        status: null,
      });
    }
    handlers.onDone(askResponse);
    return;
  }
  if (frame.event === "error") {
    const body = asRecord(asRecord(payload).error);
    throw new ExplorerError({
      kind: "http",
      code: String(body.code ?? UNEXPECTED_CODE),
      message: typeof body.message === "string" ? body.message : "",
      details: asRecord(body.details),
      status: null,
    });
  }
  const step = toStep(frame.event, payload);
  if (step !== null) {
    handlers.onStep(step);
  }
}
