"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Moon, Sun } from "@phosphor-icons/react/ssr";
import { BrandMark } from "@/components/brand/brand-mark";
import { explorer } from "@/lib/explorer-copy";
import {
  API_BASE_URL,
  ExplorerError,
  fetchDocuments,
  fetchNodeSource,
  fetchTree,
  streamAsk,
  toUnreachableFailure,
} from "./api";
import { AnswerPane } from "./answer-pane";
import { ChatView } from "./chat-view";
import { SourcePane } from "./source-pane";
import { TreePane } from "./tree-pane";
import type { AskTurn } from "./turn";
import type { DocumentSummary, NodeSource, TreeNode } from "./types";

interface TreeState {
  documentId: string;
  depth: number;
  root: TreeNode;
}

interface SourceState {
  nodeId: string;
  source: NodeSource;
}

/**
 * The document explorer: the three panes of architecture.md section 7.
 *
 * The explorer is a CLIENT runtime. It is the browser, not a server, that
 * talks to the local API, which is what lets the route be a static export.
 *
 * Two rules shape the state handling:
 *
 * 1. A failure never becomes an empty pane. An unreachable API, a 404 and
 *    an empty document list are three different claims, and only the third
 *    one is an empty pane.
 * 2. The streamed steps are kept in the order the server sent them, and
 *    the terminal `done` event's AskResponse is the answer that is
 *    rendered, so the interface shows what the backend returned rather
 *    than a client-side reassembly of it.
 *
 * The loaded tree and the loaded source carry the id they belong to, so a
 * pane can tell "not loaded yet" from "loaded for something else". That
 * keeps the loading state DERIVED rather than a second set of flags that
 * has to be kept in step with the data.
 */
export function Explorer() {
  const [documents, setDocuments] = useState<DocumentSummary[] | null>(null);
  const [documentId, setDocumentId] = useState("");
  // Tree endpoint counts levels with root at level 1, so api.md's depth=3
  // returns document -> title -> chapter and misses articles. This pane
  // needs the Article level, so it asks for depth 5 and exposes the rest
  // in the depth control.
  const [depth, setDepth] = useState(5);
  const [treeState, setTreeState] = useState<TreeState | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [sourceState, setSourceState] = useState<SourceState | null>(null);
  const [question, setQuestion] = useState("");
  // One ask runtime, two views: the chat keeps the whole transcript; the
  // explorer pane shows the latest turn. One list is what lets them share it.
  const [turns, setTurns] = useState<AskTurn[]>([]);
  const [asking, setAsking] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [sourceError, setSourceError] = useState<string | null>(null);
  const [apiUnreachable, setApiUnreachable] = useState(false);
  // The header is a mode switcher, not a label: the same runtime renders as
  // the three-pane explorer or a full-screen chat. Both panels stay mounted;
  // the inactive one is `hidden` so a tab's aria-controls always names a real
  // element. The discovery UI is identical in both modes.
  const [mode, setMode] = useState<"explorer" | "chat">("explorer");
  // First client render reads the theme from the DOM, because the pre-paint
  // script may already have set data-explorer-theme before this module loads.
  // Server render has no DOM, so it defaults to dark; suppressHydrationWarning
  // silences the one-frame icon gap.
  const initialTheme: "dark" | "light" = (() => {
    if (typeof window === "undefined") {
      return "dark";
    }
    const attribute = document.documentElement.getAttribute(
      "data-explorer-theme",
    );
    if (attribute === "light" || attribute === "dark") {
      return attribute;
    }
    try {
      const stored = window.localStorage.getItem("tuklas-explorer-theme");
      return stored === "light" || stored === "dark" ? stored : "dark";
    } catch {
      return "dark";
    }
  })();
  const [theme, setTheme] = useState<"dark" | "light">(initialTheme);

  // The effect writes the theme to the DOM and to storage. It never calls
  // setState: the state is already correct from the render-time read above,
  // so the effect only synchronizes the external systems that React does
  // not own. It runs on mount and again after every toggle, and its cleanup
  // removes the attribute when the explorer unmounts, so a client
  // navigation back to the marketing route cannot inherit it.
  useEffect(() => {
    const root = document.documentElement;
    if (theme === "light") {
      root.setAttribute("data-explorer-theme", "light");
    } else {
      root.removeAttribute("data-explorer-theme");
    }
    try {
      window.localStorage.setItem("tuklas-explorer-theme", theme);
    } catch {
      // Storage can be unavailable; the theme still works for this visit.
    }
    return () => {
      root.removeAttribute("data-explorer-theme");
    };
  }, [theme]);

  const onToggleTheme = useCallback(() => {
    const next: "dark" | "light" = theme === "light" ? "dark" : "light";
    const root = document.documentElement;
    if (next === "light") {
      root.setAttribute("data-explorer-theme", "light");
    } else {
      root.removeAttribute("data-explorer-theme");
    }
    try {
      window.localStorage.setItem("tuklas-explorer-theme", next);
    } catch {
      // Storage can be unavailable; the theme still works for this visit.
    }
    setTheme(next);
  }, [theme]);
  // The transcript's turn counter. It lives in a ref because it is an
  // identity, not a value the interface renders.
  const nextTurnId = useRef(1);

  // The document list. It is loaded once on mount, and a failure here is
  // the loud "the API is not reachable" state rather than an empty list.
  useEffect(() => {
    const controller = new AbortController();
    fetchDocuments(controller.signal)
      .then((list) => {
        setDocuments(list.data);
        setLoadError(null);
        setApiUnreachable(false);
        setDocumentId((current) => (current === "" ? list.data[0].id : current));
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) {
          return;
        }
        setDocuments(null);
        setLoadError(
          error instanceof ExplorerError
            ? error.failure.message
            : toUnreachableFailure(error).message,
        );
        setApiUnreachable(
          !(error instanceof ExplorerError) || error.failure.kind === "unreachable",
        );
      });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (documentId === "") {
      return;
    }
    const controller = new AbortController();
    fetchTree(documentId, depth, controller.signal)
      .then((result) => {
        setTreeState({ documentId, depth, root: result.root });
        setLoadError(null);
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) {
          return;
        }
        setLoadError(
          error instanceof ExplorerError
            ? error.failure.message
            : toUnreachableFailure(error).message,
        );
      });
    return () => controller.abort();
  }, [documentId, depth]);

  // The source of the selected node. A citation click and a tree click
  // both land here, so the source pane behaves the same either way.
  useEffect(() => {
    if (selectedId === null) {
      return;
    }
    const controller = new AbortController();
    fetchNodeSource(selectedId, controller.signal)
      .then((result) => {
        setSourceState({ nodeId: selectedId, source: result });
        setSourceError(null);
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) {
          return;
        }
        // The source failure is its own state. It used to share the ask
        // error, which meant a failed source fetch and a failed answer
        // rendered the same banner, and the answer's own failure was
        // shown twice.
        setSourceError(
          error instanceof ExplorerError
            ? error.failure.message
            : toUnreachableFailure(error).message,
        );
      });
    return () => controller.abort();
  }, [selectedId]);

  const onSelectNode = useCallback((nodeId: string) => {
    // The source of the previous node is cleared here, not in the fetch
    // effect, because a failed fetch must not leave the previous node's
    // text on screen next to the new node's failure.
    setSourceState(null);
    setSourceError(null);
    setSelectedId(nodeId);
  }, []);

  const onDocumentChange = useCallback((value: string) => {
    // The selected node belongs to the document that was active.
    // Switching documents clears it, and with it the source that was
    // fetched for it.
    setSourceState(null);
    setSourceError(null);
    setSelectedId(null);
    setDocumentId(value);
  }, []);

  // The in-flight ask, so a second question cancels the first instead of
  // leaving two streams writing into the same transcript. One run at a
  // time is also what the API's own contract expects.
  const activeAsk = useRef<AbortController | null>(null);

  // A stream that is still running when the view goes away is cancelled,
  // not left to call back into a component that is gone.
  useEffect(() => {
    return () => {
      activeAsk.current?.abort();
    };
  }, []);

  const onAsk = useCallback(
    (
      asked: string,
      retryOf: number | null = null,
    ) => {
      const trimmed = asked.trim();
      if (documentId === "" || trimmed.length === 0) {
        return;
      }
      activeAsk.current?.abort();
      const controller = new AbortController();
      activeAsk.current = controller;
      // The turn id is the transcript's key. A retry REUSES the id of the
      // turn it replaces, so the failed turn is answered in place rather
      // than joined by a second copy of the same question. A new question
      // takes the next id, so the two can never collide.
      const turnId = retryOf ?? nextTurnId.current;
      if (retryOf === null) {
        nextTurnId.current += 1;
      }
      setAsking(true);
      setTurns((current) => {
        const fresh = {
          id: turnId,
          documentId,
          question: trimmed,
          steps: [],
          tokenText: "",
          response: null,
          status: "asking" as const,
          error: null,
        };
        if (retryOf === null) {
          return [...current, fresh];
        }
        return current.map((turn) => (turn.id === retryOf ? fresh : turn));
      });
      // The question stays in the explorer's input field: there it is the
      // one place the question is written down. The chat clears its own
      // field when it sends, because there the transcript is the record.
      streamAsk(
        { document_id: documentId, question: trimmed },
        {
          onStep: (step) =>
            setTurns((current) =>
              current.map((turn) =>
                turn.id === turnId
                  ? { ...turn, steps: [...turn.steps, step] }
                  : turn,
              ),
            ),
          onToken: (text) =>
            setTurns((current) =>
              current.map((turn) =>
                turn.id === turnId
                  ? { ...turn, tokenText: turn.tokenText + text }
                  : turn,
              ),
            ),
          onDone: (result) => {
            if (activeAsk.current === controller) {
              activeAsk.current = null;
            }
            setTurns((current) =>
              current.map((turn) =>
                turn.id === turnId
                  ? { ...turn, response: result, status: "done" }
                  : turn,
              ),
            );
            setAsking(false);
          },
        },
        controller.signal,
      ).catch((error: unknown) => {
        if (controller.signal.aborted) {
          // The run was cancelled because a newer question replaced it.
          // The turn stays on the transcript, marked as superseded, so the
          // transcript does not show a question that was never answered.
          // A run cancelled by unmount is not marked: there is no
          // transcript left to mark it in.
          if (activeAsk.current !== controller) {
            setTurns((current) =>
              current.map((turn) =>
                turn.id === turnId
                  ? { ...turn, status: "error", error: explorer.chat.superseded }
                  : turn,
              ),
            );
          }
          return;
        }
        setAsking(false);
        const message =
          error instanceof ExplorerError
            ? `${error.failure.code}: ${error.failure.message}`
            : toUnreachableFailure(error).message;
        setTurns((current) =>
          current.map((turn) =>
            turn.id === turnId ? { ...turn, status: "error", error: message } : turn,
          ),
        );
      });
    },
    [documentId],
  );

  const tree =
    treeState !== null && treeState.documentId === documentId && treeState.depth === depth
      ? treeState.root
      : null;
  const treeLoading = documentId !== "" && tree === null;
  const source =
    sourceState !== null && sourceState.nodeId === selectedId ? sourceState.source : null;
  const sourceLoading = selectedId !== null && source === null;
  // The explorer's answer pane shows the LATEST turn. The chat keeps the
  // whole transcript; the pane is a single-answer surface, so the newest
  // run is the one it renders.
  const latestTurn = turns.length === 0 ? null : turns[turns.length - 1];

  return (
    <div className="flex h-dvh min-h-0 flex-1 flex-col overflow-hidden bg-ink-0 text-ink-9">
      <header className="border-b border-rule bg-ink-1 px-4 py-3 sm:px-6">
        <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
          <h1 className="font-sans text-base font-semibold tracking-tight text-ink-9">
            {explorer.header.title}
          </h1>
          <p className="text-xs text-ink-7">{explorer.header.subtitle}</p>
          <span
            data-brand-lockup
            className="inline-flex items-center gap-1.5 font-mono text-[11px] uppercase tracking-[0.18em]"
          >
            <BrandMark size={16} className="shrink-0" />
            <span className="text-ink-9">{explorer.header.wordmark}</span>
          </span>
          {/*
            The theme toggle sits in the header's blank run, between the
            subtitle and the tablist. The tablist carries `ml-auto`, so it
            stays pinned to the right end and neither it nor the API
            readout moves.
          */}
          <button
            type="button"
            data-theme-toggle
            aria-pressed={theme === "light"}
            aria-label={explorer.header.themeToggleLabel}
            onClick={onToggleTheme}
            className="inline-flex h-6 self-center items-center gap-1.5 border border-rule px-2 font-mono text-[11px] text-ink-7 transition-colors duration-120 hover:text-ink-9 focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2"
          >
            {theme === "light" ? (
              <Sun aria-hidden="true" size={14} weight="regular" />
            ) : (
              <Moon aria-hidden="true" size={14} weight="regular" />
            )}
            <span>{theme === "light" ? explorer.header.themeLightLabel : explorer.header.themeDarkLabel}</span>
          </button>
          {/*
            The view switcher is a tablist, and it is a tablist for a real
            reason: the two views share one runtime, and a tablist is what
            says "one thing, two views" to a screen reader. The arrow keys
            move between the tabs, which is the tab pattern's contract, and
            each tab names the panel it controls.

            It sits at the RIGHT end of the header, in the blank run the
            baseline left between the subtitle and the API readout, and it
            is `h-6` so it fits inside the header's existing 24px content
            band instead of growing it. Both facts are load-bearing:

            * The header's height is the explorer's most visible dimension.
              A switcher that grew the row pushed every pane down, so the
              explorer stopped matching the build it replaced. The switcher
              takes the space the header already had.
            * `self-center` keeps the switcher out of the baseline group.
              The h1, the subtitle and the API readout share one text
              baseline; a fourth baseline-aligned item would shift them
              all. Centering it leaves their metrics untouched.

            `ml-auto` is what moves the API readout's right edge: the
            switcher takes the free space in front of it, and the readout
            keeps the right edge the baseline gave it.
          */}
          <div
            role="tablist"
            aria-label={explorer.header.viewLabel}
            className="ml-auto flex h-6 self-center items-center border border-rule bg-ink-2"
            onKeyDown={(event) => {
              if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") {
                return;
              }
              event.preventDefault();
              const next = mode === "explorer" ? "chat" : "explorer";
              setMode(next);
              document.getElementById(`view-tab-${next}`)?.focus();
            }}
          >
            <button
              type="button"
              role="tab"
              id="view-tab-explorer"
              aria-selected={mode === "explorer"}
              aria-controls="view-panel-explorer"
              tabIndex={mode === "explorer" ? 0 : -1}
              onClick={() => setMode("explorer")}
              className={`inline-flex h-full items-center justify-center px-2.5 font-sans text-sm font-medium tracking-tight transition-[background-color,color,transform] duration-120 ease-[cubic-bezier(.23,1,.32,1)] active:scale-[0.97] focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 ${
                mode === "explorer"
                  ? "bg-ink-4 text-ink-9"
                  : "text-ink-7 hover:text-ink-9"
              }`}
            >
              {explorer.header.explorerTabLabel}
            </button>
            <button
              type="button"
              role="tab"
              id="view-tab-chat"
              aria-selected={mode === "chat"}
              aria-controls="view-panel-chat"
              tabIndex={mode === "chat" ? 0 : -1}
              onClick={() => setMode("chat")}
              className={`inline-flex h-full items-center justify-center px-2.5 font-sans text-sm font-medium tracking-tight transition-[background-color,color,transform] duration-120 ease-[cubic-bezier(.23,1,.32,1)] active:scale-[0.97] focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 ${
                mode === "chat"
                  ? "bg-ink-4 text-ink-9"
                  : "text-ink-7 hover:text-ink-9"
              }`}
            >
              {explorer.chat.tabLabel}
            </button>
          </div>
          <span className="font-mono text-[11px] text-ink-7">
            {explorer.header.apiLabel}: {API_BASE_URL}
          </span>
        </div>
      </header>

      {loadError !== null ? (
        <div
          role="alert"
          className="border-b border-rule-strong bg-ink-2 px-4 py-4 sm:px-6"
        >
          <h2 className="font-mono text-[11px] tracking-[0.18em] text-ink-7 uppercase">
            {apiUnreachable ? explorer.failure.title : explorer.failure.apiTitle}
          </h2>
          <p className="mt-1 text-sm leading-relaxed text-ink-9">{loadError}</p>
          {apiUnreachable ? (
            <p className="mt-1 text-xs leading-relaxed text-ink-7">
              {explorer.failure.transportNote}
            </p>
          ) : null}
        </div>
      ) : null}

      {/*
        Both panels are mounted and the inactive one carries the `hidden`
        attribute. A tab's `aria-controls` must name an element that
        exists, and an unmounted panel would make the reference dangling.
        `hidden` also keeps the inactive view out of the tab order and
        out of the accessibility tree.
      */}
      <div
        role="tabpanel"
        id="view-panel-explorer"
        aria-labelledby="view-tab-explorer"
        hidden={mode !== "explorer"}
        className="grid min-h-0 flex-1 grid-cols-1 divide-y divide-rule overflow-y-auto border-b border-rule lg:grid-cols-3 lg:divide-x lg:divide-y-0 lg:overflow-hidden"
      >
          <div className="flex h-[70vh] min-h-0 flex-col overflow-hidden lg:h-auto lg:min-h-0">
            <div className="flex items-center gap-3 border-b border-rule px-4 py-2">
              <label
                htmlFor="document-select"
                className="font-mono text-[11px] tracking-[0.12em] text-ink-7 uppercase"
              >
                {explorer.tree.documentLabel}
              </label>
              <select
                id="document-select"
                value={documentId}
                onChange={(event) => {
                  onDocumentChange(event.target.value);
                }}
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
            {documents !== null && documents.length === 0 ? (
              <p className="px-4 py-4 text-sm leading-relaxed text-ink-7">
                {explorer.tree.documentEmpty}
              </p>
            ) : null}
            <div className="min-h-0 flex-1 overflow-hidden">
              <TreePane
                root={tree}
                loading={treeLoading}
                selectedId={selectedId}
                onSelect={onSelectNode}
                depth={depth}
                onDepthChange={setDepth}
              />
            </div>
          </div>

          <div className="flex h-[70vh] min-h-0 flex-col overflow-hidden lg:h-auto">
            <div className="min-h-0 flex-1 overflow-hidden">
              <SourcePane source={source} loading={sourceLoading} error={sourceError} />
            </div>
          </div>

          <div className="flex h-[70vh] min-h-0 flex-col overflow-hidden lg:h-auto">
            <div className="min-h-0 flex-1 overflow-hidden">
              <AnswerPane
                question={question}
                onQuestionChange={setQuestion}
                onAsk={() => onAsk(question)}
                asking={asking}
                canAsk={documentId !== "" && question.trim().length > 0}
                steps={latestTurn?.steps ?? []}
                tokenText={latestTurn?.tokenText ?? ""}
                response={latestTurn?.response ?? null}
                error={latestTurn?.error ?? null}
                onCite={onSelectNode}
              />
            </div>
          </div>
      </div>

      <div
        role="tabpanel"
        id="view-panel-chat"
        aria-labelledby="view-tab-chat"
        hidden={mode !== "chat"}
        className="flex min-h-0 flex-1 flex-col overflow-hidden"
      >
        <ChatView
          documentId={documentId}
          documents={documents}
          onDocumentChange={onDocumentChange}
          turns={turns}
          asking={asking}
          onAsk={onAsk}
          onCite={onSelectNode}
          source={source}
          sourceLoading={sourceLoading}
          sourceError={sourceError}
        />
      </div>

      {/*
        The ask failure is shown in the place it happened: in the chat it is
        the failed turn itself, because a transcript that hid the failure
        would read as a question that was never answered. The explorer's
        answer pane renders the failed turn's error in place, for the same
        reason. There is no separate banner, because a second rendering of
        the same failure reads as two failures.
      */}
    </div>
  );
}
