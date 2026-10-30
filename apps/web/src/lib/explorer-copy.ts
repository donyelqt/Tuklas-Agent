/**
 * Every user-visible string in the document explorer at /app.
 *
 * The marketing route has its own audited copy surface in `lib/copy.ts`. The
 * explorer is a separate runtime with its own contract (docs/architecture.md
 * section 7), so it gets its own module rather than a second section inside
 * the marketing file: the marketing grep stays a single-purpose audit of the
 * landing page, and the explorer strings stay in one place.
 *
 * The persistent disclaimer wording is the one PRD US-2 pins, and it is the
 * same string the generation prompt carries (apps/api/generation/prompts.py
 * DISCLAIMER). It is a legal requirement, not decoration.
 */
export const explorer = {
  meta: {
    title: "Document explorer",
    description:
      "A local document explorer: the document tree, the cited source text, and a cited answer from a running Tuklas API.",
  },

  header: {
    title: "Document explorer",
    subtitle: "Runs against a local API. The document never leaves this machine.",
    apiLabel: "API base URL",
    /** The tablist's accessible name: the header switches the view. */
    viewLabel: "View",
    explorerTabLabel: "Explorer",
    wordmark: "Tuklas",
    themeToggleLabel: "Switch theme",
    themeLightLabel: "Light",
    themeDarkLabel: "Dark",
  },

  /**
   * PRD US-2. The exact wording, unchanged.
   */
  disclaimer:
    "Tuklas is a research discovery tool, not a lawyer. Verify provisions before relying on them.",

  tree: {
    title: "Tree",
    endpoint: "GET /v1/documents/{id}/tree?depth=N",
    documentLabel: "Document",
    documentPlaceholder: "Select a document",
    documentEmpty: "No document has been ingested yet. Upload a PDF with POST /v1/documents and the tree appears here.",
    documentError: "The document list could not be loaded.",
    depthLabel: "Depth",
    loading: "Loading the tree.",
    empty: "No tree is loaded for this document.",
    truncated: "More nodes below this level are not loaded. Raise the depth to include them.",
    outsideLoadedLevels:
      "The selected node is below the loaded levels.",
    loadDeeper: "Load one level deeper",
    pageLabel: "p.",
    typeLabel: "type",
    nodeIdLabel: "Node",
    moreNodes: "more nodes below",
  },

  source: {
    title: "Source",
    endpoint: "GET /v1/nodes/{id}/source",
    empty: "Select a node in the tree, or a citation in the answer, to read the source text.",
    loading: "Loading the source text.",
    /** The heading on a source that could not be fetched. */
    failureTitle: "The source could not be read",
    pageLabel: "Page",
    pagesLabel: "Pages",
    pathLabel: "Path",
    nodeLabel: "Node",
    emptyText: "This node carries no extracted text.",
  },

  answer: {
    title: "Answer",
    endpoint: "POST /v1/ask/stream",
    questionLabel: "Question",
    questionPlaceholder: "Ask about a provision in the active document.",
    askLabel: "Ask",
    /** The heading on an answer that failed to arrive. */
    failureTitle: "The run failed",
    askingLabel: "Asking",
    askDisabled: "Select a document and type a question first.",
    idle: "No question has been asked yet. The agent steps, the evidence, and the answer appear here.",
    /** Shown while a run is in flight and no step has arrived yet. */
    pending:
      "The run is under way. The agent steps, the evidence, and the answer appear here as they arrive.",
    stepsTitle: "Agent steps",
    stepsEmpty: "No step has been streamed yet.",
    planLabel: "Plan",
    toolCallLabel: "Tool call",
    toolResultLabel: "Tool result",
    evidenceLabel: "Evidence",
    relevantLabel: "relevant",
    irrelevantLabel: "not relevant",
    insufficientLabel: "insufficient",
    sufficientLabel: "sufficient",
    noSteps: "no result",
    answerTitle: "Answer",
    answerPending:
      "The answer text is released in one batch once the model has finished and the whole answer has been checked, so it appears at once rather than typing out.",
    refusalTitle: "No answer",
    refusalNote:
      "The kept evidence did not support an answer, so none is shown and no citation is offered.",
    citationsTitle: "Citations",
    citationsEmpty: "The answer carries no citation.",
    iterationsLabel: "iterations",
    latencyLabel: "agent latency",
    tokensLabel: "tokens",
    tokensEstimatedNote:
      "The token counts are a character-count estimate, not a measured provider count.",
    tokensProviderNote: "The token counts are the numbers the local model backend reported.",
    evidenceCountLabel: "evidence nodes",
  },

  /**
   * The chat view: the same question-and-answer runtime as the explorer's
   * answer pane, in a centered, full-screen form. It is a different
   * interface to the same tool, not a different tool: it reuses
   * `answer.questionPlaceholder`, `answer.askLabel` and `explorer.disclaimer`
   * rather than restating them, so one action has one label in both views.
   *
   * It is document-scoped because the API is: `streamAsk` requires a
   * `document_id`, so the chat view asks about the active document and the
   * header's document selector stays visible in this mode. There is no
   * document-less mode this API can serve.
   */
  chat: {
    tabLabel: "Chat",
    welcome: "Ask about the active document.",
    welcomeHint:
      "Type a question and ask. The agent steps, the evidence, and the cited answer appear here.",
    modeNote:
      "Questions are answered from the active document. Switch documents above.",
    /** The transcript's accessible name. */
    transcriptLabel: "Conversation",
    /**
     * The note on a turn that was replaced by a newer question before it
     * finished. It is not a failure: the run was cancelled on purpose.
     */
    superseded: "This question was replaced by a newer one before it finished.",
  },

  failure: {
    title: "The API could not be reached",
    transportNote:
      "The explorer shows nothing rather than an empty pane, because an empty pane would read as a document with no matches.",
    apiTitle: "The API returned an error",
    retryLabel: "Retry",
    codeLabel: "Code",
    detailsLabel: "Details",
  },
} as const;
