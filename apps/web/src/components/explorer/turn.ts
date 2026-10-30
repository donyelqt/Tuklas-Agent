import type { AskResponse, Step } from "./types";

/**
 * One question-and-answer exchange in the chat transcript.
 *
 * The chat keeps a LIST of turns rather than one set of fields, because a
 * chat is a transcript: the previous question and its answer stay on screen
 * while the next one is asked. The explorer's answer pane renders the
 * LATEST turn, so the two views share one runtime and one history instead
 * of keeping two copies of the same run.
 *
 * `status` is the state of this turn's run: `asking` until the stream
 * ends, then `done` or `error`. It lives on the turn rather than on a
 * single flag for the whole view, because a finished turn and a failed
 * turn are different claims and the interface has to be able to say which
 * one it is.
 */
export interface AskTurn {
  /** Stable identity within the transcript, used as the React key. */
  id: number;
  documentId: string;
  question: string;
  /** The streamed agent steps, in the order the server sent them. */
  steps: Step[];
  /** The replayed token text, shown only while the run is unfinished. */
  tokenText: string;
  response: AskResponse | null;
  status: "asking" | "done" | "error";
  /** The failure message, set only when `status` is `error`. */
  error: string | null;
}
