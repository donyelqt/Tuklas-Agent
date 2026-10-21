"use client";

import { useCallback, useEffect, useState } from "react";
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
import { SourcePane } from "./source-pane";
import { TreePane } from "./tree-pane";
import type { AskResponse, DocumentSummary, NodeSource, Step, TreeNode } from "./types";

/** The tree of one document at one depth, tagged with what produced it. */
interface TreeState {
  documentId: string;
  depth: number;
  root: TreeNode;
}

/** The source of one node, tagged with the node it belongs to. */
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
  // The tree endpoint counts LEVELS with the root at level 1
  // (apps/api/ingestion/service.py `_build_tree_node`), so the `depth=3`
  // in the api.md example returns document -> title -> chapter and never
  // reaches the articles. The explorer's tree pane is for the
  // Book/Title/Chapter/Article structure, so it asks for the level that
  // carries it and exposes the others in the depth control.
  const [depth, setDepth] = useState(5);
  const [treeState, setTreeState] = useState<TreeState | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [sourceState, setSourceState] = useState<SourceState | null>(null);
  const [question, setQuestion] = useState("");
  const [steps, setSteps] = useState<Step[]>([]);
  const [tokenText, setTokenText] = useState("");
  const [response, setResponse] = useState<AskResponse | null>(null);
  const [asking, setAsking] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [askError, setAskError] = useState<string | null>(null);
  const [apiUnreachable, setApiUnreachable] = useState(false);

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

  // The tree of the active document.
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
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) {
          return;
        }
        setAskError(
          error instanceof ExplorerError
            ? error.failure.message
            : toUnreachableFailure(error).message,
        );
      });
    return () => controller.abort();
  }, [selectedId]);

  const onSelectNode = useCallback((nodeId: string) => {
    setSelectedId(nodeId);
  }, []);

  const onAsk = useCallback(() => {
    if (documentId === "" || question.trim().length === 0) {
      return;
    }
    const controller = new AbortController();
    setAsking(true);
    setAskError(null);
    setSteps([]);
    setTokenText("");
    setResponse(null);
    streamAsk(
      { document_id: documentId, question: question.trim() },
      {
        onStep: (step) => setSteps((current) => [...current, step]),
        onToken: (text) => setTokenText((current) => current + text),
        onDone: (result) => {
          setResponse(result);
          setAsking(false);
        },
      },
      controller.signal,
    ).catch((error: unknown) => {
      if (controller.signal.aborted) {
        return;
      }
      setAsking(false);
      setAskError(
        error instanceof ExplorerError
          ? `${error.failure.code}: ${error.failure.message}`
          : toUnreachableFailure(error).message,
      );
    });
  }, [documentId, question]);

  const tree =
    treeState !== null && treeState.documentId === documentId && treeState.depth === depth
      ? treeState.root
      : null;
  const treeLoading = documentId !== "" && tree === null;
  const source =
    sourceState !== null && sourceState.nodeId === selectedId ? sourceState.source : null;
  const sourceLoading = selectedId !== null && source === null;

  return (
    <div className="flex h-dvh min-h-0 flex-1 flex-col overflow-hidden">
      <header className="border-b border-rule bg-ink-1 px-4 py-3 sm:px-6">
        <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
          <h1 className="font-sans text-base font-semibold tracking-tight text-ink-9">
            {explorer.header.title}
          </h1>
          <p className="text-xs text-ink-7">{explorer.header.subtitle}</p>
          <span className="ml-auto font-mono text-[11px] text-ink-7">
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

      <div className="grid min-h-0 flex-1 grid-cols-1 divide-y divide-rule overflow-y-auto border-b border-rule lg:grid-cols-3 lg:divide-x lg:divide-y-0 lg:overflow-hidden">
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
                setDocumentId(event.target.value);
                setSelectedId(null);
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
            <SourcePane source={source} loading={sourceLoading} />
          </div>
        </div>

        <div className="flex h-[70vh] min-h-0 flex-col overflow-hidden lg:h-auto">
          <div className="min-h-0 flex-1 overflow-hidden">
            <AnswerPane
              question={question}
              onQuestionChange={setQuestion}
              onAsk={onAsk}
              asking={asking}
              canAsk={documentId !== "" && question.trim().length > 0}
              steps={steps}
              tokenText={tokenText}
              response={response}
              onCite={onSelectNode}
            />
          </div>
        </div>
      </div>

      {askError !== null ? (
        <div
          role="alert"
          className="border-t border-rule-strong bg-ink-2 px-4 py-3 sm:px-6"
        >
          <h2 className="font-mono text-[11px] tracking-[0.18em] text-ink-7 uppercase">
            {explorer.failure.apiTitle}
          </h2>
          <p className="mt-1 text-sm leading-relaxed text-ink-9">{askError}</p>
        </div>
      ) : null}
    </div>
  );
}
