/**
 * The header carries only the two calls to action. Section navigation lives in
 * the left rail, `rail.items` below, so listing the same six links in both
 * places would be duplication with two answers to the same question.
 */
export const nav = {
  primaryCta: { label: "Read the spec", href: "https://github.com/donyelqt/Tuklas-Agent/tree/main/docs" },
  secondaryCta: { label: "View on GitHub", href: "https://github.com/donyelqt/Tuklas-Agent" },
};

export const hero = {
  /**
   * The headline is split so the payoff phrase can carry the accent colour.
   * Same font, same weight, colour only, which is the emphasis rule: no
   * second family is injected into a sans headline.
   */
  headlineLead: "Retrieval starts at the",
  headlineAccent: "provision, not the chunk",
  subhead:
    "Tuklas navigates a document tree with lexical, structural, and reference signals, and cites an article and page on every claim.",
  ctaPrimary: "Read the spec",
  ctaSecondary: "View on GitHub",
  command: {
    label: "Clone",
    command: "git clone https://github.com/donyelqt/Tuklas-Agent.git",
  },
  /**
   * The lockup above the headline names the project and the person behind it.
   * Both are literal strings here because the lockup is the single documented
   * exception to the copy.ts rule, recorded in DESIGN.md section 5.
   */
  wordmark: "Tuklas",
  byline: "by Doniele Antonio",
};

/**
 * The left rail mirrors the numbered feature rail in the reference design
 * language. It is navigation, not decoration: every entry is a real anchor to
 * a section that exists on this page, and `aria-current` marks the one in view.
 */
export const rail = {
  items: [
    { n: "01", label: "Hierarchy", href: "#problem" },
    { n: "02", label: "Pipeline", href: "#pipeline" },
    { n: "03", label: "Tools", href: "#tools" },
    { n: "04", label: "Comparison", href: "#comparison" },
    { n: "05", label: "Guardrails", href: "#guardrails" },
    { n: "06", label: "Run locally", href: "#run-locally" },
  ],
};

/**
 * The strip carries two kinds of thing and the label has to admit that.
 *
 * ADR-004 fixes the generation interface to ollama, llama.cpp and vLLM, and
 * rejects cloud backends, so the three runtimes are the only entries that can
 * be described as targeted. The models beside them are examples of what those
 * runtimes can serve locally, not providers Tuklas claims to support.
 */
export const backends = {
  label: "Local runtimes, and the open-weight models they serve",
  backends: [
    { name: "Ollama", slug: "ollama" },
    { name: "llama.cpp", slug: "llamacpp" },
    { name: "vLLM", slug: "vllm" },
  ],
  models: [
    { name: "Gemma", slug: "googlegemini" },
    { name: "Qwen", slug: "qwen" },
    { name: "Mistral", slug: "mistralai" },
    { name: "DeepSeek", slug: "deepseek" },
  ],
};

export const problem = {
  headline: "A statute is a tree, and a chunk is not",
  lead:
    "Splitting a document into flat windows discards the relationships a legal answer has to rest on.",
  flat: {
    label: "What a flat pipeline carries",
    items: [
      "A fixed-size window of text cut at an arbitrary offset",
      "A vector with no parent, child, or sibling",
      "A citation that points at a window, not an article",
      "No way to walk to the neighbouring provision",
    ],
  },
  structured: {
    label: "What the structure carries",
    items: [
      "Book IV, Title II, Chapter 1, Articles 1318, 1319, 1320 as parent and child",
      "Sibling provisions that share a parent",
      "Cross references between articles, parsed into graph edges",
      "A stable location: article, section, and page",
    ],
  },
  note: "Structure is kept at ingest so a query can use it, rather than trying to recover it from text.",
  previewCaption:
    "The explorer tree the agent navigates, as returned by the documents tree endpoint.",
};

export const pipeline = {
  headline: "Seven steps from PDF to cited answer",
  lead: "Each step is a contract in the codebase, not a stage name on a slide.",
  steps: [
    {
      name: "Ingest",
      body: "PyMuPDF extracts pages, text, and metadata from a text-layer PDF, and identical bytes are rejected by hash.",
    },
    {
      name: "Parse",
      body: "A regex and heading-match parser writes a Book, Title, Chapter, Section, and Article tree, or flags a flat fallback.",
    },
    {
      name: "Index",
      body: "SQLite stores the node tree, with BM25 over leaf bodies and an identifier index built at ingest.",
    },
    {
      name: "Plan",
      body: "The planner emits one to four sub-questions, and a plan with none is treated as invalid.",
    },
    {
      name: "Retrieve",
      body: "The agent navigates the tree through seven tools, scoring lexical, structural, and reference signals in two passes.",
    },
    {
      name: "Cite",
      body: "Every factual sentence renders a citation that resolves to an article and a page.",
    },
    {
      name: "Abstain",
      body: "When the kept evidence is insufficient, the answer returns no citations instead of an unsupported claim.",
    },
  ],
};

export const tools = {
  headline: "Seven tools are the only way in",
  lead: "The agent has no direct database or file access to the document. Every node it sees arrives through one of these signatures.",
  signature: `search_nodes(query: str, document_id: str, top_k: int = 10) -> list[NodeHit]
inspect_node(node_id: str) -> NodeFull
list_children(node_id: str, limit: int = 50) -> list[NodeSummary]
get_parent(node_id: str) -> NodeSummary | None
expand_context(node_id: str, radius: int = 1) -> NodeFull
follow_reference(reference_id: str) -> FollowResult
get_source(node_id: str) -> Source
select_evidence(hits: list[NodeHit], sub_questions: list[str]) -> EvidenceSet`,
  caption: "Signatures are enforced in the agent wrappers in apps/api/agent/, with output caps in docs/architecture.md section 4.",
};

export const comparison = {
  headline: "The comparison is designed, not won",
  lead: "Two retrieval arms, one dataset, one generation model. Both arms run the same agent and the same judge; the arms' retrieval layers draw on different signals, and the comparison's outcome is undecided.",
  tuklas: {
    label: "Tuklas",
    items: [
      "BM25 over leaf bodies, with the heading line kept in the corpus",
      "Structural signal from parent, child, and sibling position",
      "Reference signal from cross-reference edges",
      "No dense embedding on the primary path",
    ],
  },
  vector: {
    label: "Dense vector baseline",
    items: [
      "Dense embeddings and similarity search",
      "Quarantined to experiments/vector/",
      "Importable only by the evaluation runner",
      "Scanned by the CI import rule",
    ],
  },
  facts: [
    { label: "Dataset", value: "72 questions: 24 dev for tuning, 48 test reported" },
    { label: "Categories", value: "7 categories, at least 3 dev and 6 test each" },
    { label: "Primary metric", value: "Recall@5" },
    { label: "Secondary metric", value: "MRR@10" },
    { label: "Generation", value: "Same model, backend, and seed in both arms, temperature 0" },
    { label: "Judge", value: "Arm-blinded, sentence-level faithfulness scoring" },
    {
      label: "Spot check",
      value: "20 percent of test answers re-scored; disagreement above 15 percent invalidates the run",
    },
    {
      label: "Provisional margins",
      value: "0.10 on Recall@5 and faithfulness, 0.20 on any single category gap",
    },
    { label: "Margin status", value: "Placeholders recorded in the eval config, not results" },
  ],
  decisionRule: "Both numbers are published per category and overall. There is no win ceremony.",
  expectedPattern:
    "The expected pattern is competitive on cross-reference and multi-hop questions, weaker on paraphrase-heavy definitions, and a uniform sweep across every category is treated as a leakage smell rather than a win.",
  status: "The controlled comparison has been run and its reports are published in data/eval-reports. The outcome is undecided, so no metric on this page reports a measured value.",
};

/**
 * One edge of the compiled graph, transcribed from the graph builder in
 * apps/api/agent/graph.py. The `kind` is a discriminated union because the
 * graph has exactly two kinds of edge and the retry kind is the back path.
 */
type ArchitectureEdge = { from: string; to: string; kind: "flow" | "retry" };

/**
 * The layer that owns each graph node, keyed by the node name. The values
 * are layer ids from `layers` below.
 */
type ArchitectureLayerMap = Record<string, string>;

/**
 * The three drawings of the same graph the switcher offers. The `id` is
 * the key the component reads, and the `label` is the name on the switcher.
 */
type ArchitectureVariant = {
  id: "node-graph" | "pipeline-rail" | "terminal-graph";
  label: string;
};

/**
 * The nine edges of the compiled graph, in graph order. The gate has two
 * outgoing edges: the pass route to the context builder, and the one retry
 * that loops back into inspection.
 */
const architectureEdges: ArchitectureEdge[] = [
  { from: "query_plan", to: "search", kind: "flow" },
  { from: "search", to: "inspect", kind: "flow" },
  { from: "inspect", to: "expand_or_backtrack", kind: "flow" },
  { from: "expand_or_backtrack", to: "select_evidence", kind: "flow" },
  { from: "select_evidence", to: "sufficiency_gate", kind: "flow" },
  { from: "sufficiency_gate", to: "build_context", kind: "flow" },
  { from: "sufficiency_gate", to: "broaden_search", kind: "retry" },
  { from: "broaden_search", to: "inspect", kind: "retry" },
  { from: "build_context", to: "generate", kind: "flow" },
];

/**
 * The owning layer of each graph node, derived from what the node calls.
 * The API surface and the store are not graph nodes, so they appear only
 * in the layer stack.
 */
const architectureLayerOf: ArchitectureLayerMap = {
  query_plan: "agent",
  search: "tools",
  inspect: "tools",
  expand_or_backtrack: "tools",
  select_evidence: "retrieval",
  sufficiency_gate: "agent",
  broaden_search: "tools",
  build_context: "retrieval",
  generate: "generation",
};

/**
 * The three drawings the switcher offers. Every one of them reads the same
 * edge table and the same layer map, so the switcher changes the drawing
 * and never the data.
 */
const architectureVariants: ArchitectureVariant[] = [
  { id: "node-graph", label: "Node graph" },
  { id: "pipeline-rail", label: "Pipeline rail" },
  { id: "terminal-graph", label: "Terminal graph" },
];

/**
 * The architecture section is a second reading of the same system the
 * pipeline section describes in time order: the pipeline says what happens
 * next, this says which package owns which step.
 *
 * Every node, edge, and label below is traceable to the implementation.
 * The layer names are the real package names under apps/api/, the workflow
 * steps are the real graph node names in apps/api/agent/graph.py, and the
 * five retrieval channels are the five keys in
 * apps/api/retrieval/scoring.py WEIGHT_DEFAULTS. Nothing here is a
 * simplification that changes what the code does.
 *
 * The dense baseline is drawn as a sibling arm outside the shipped path,
 * because that is what it is: an evaluation arm imported only by
 * apps/api/evaluation/. It is not part of the retrieval the agent runs.
 */
export const architecture = {
  headline: "Which package owns which step",
  lead:
    "The agent orchestrates and nothing else. It reads the document tree only through seven tool signatures, and every layer below is a real module boundary in apps/api/.",
  /**
   * The switcher that picks which drawing of the same graph is on screen.
   * All three read the same `workflow.edges` table and the same `layerOf`
   * map, so the toggle changes the drawing and never the data.
   */
  diagram: {
    caption: "Diagram view",
    variants: architectureVariants,
  },
  layersCaption: "Package layers",
  /**
   * The layers, top to bottom, roughly in the order a request travels. The
   * store is written at ingest and read by retrieval before generation, so
   * it is drawn last. The `owning` field is the package path, so the
   * diagram doubles as a map from the page to the repository.
   */
  layers: [
    {
      id: "api",
      name: "FastAPI surface",
      owning: "apps/api/api/",
      body: "Routes under the /v1 prefix. Upload, ask, tree, and evaluation endpoints.",
    },
    {
      id: "agent",
      name: "Tuklas agent",
      owning: "apps/api/agent/",
      body: "A LangGraph state machine. It plans, calls tools, reads a gate verdict, and never scores anything itself.",
    },
    {
      id: "tools",
      name: "Tool layer",
      owning: "apps/api/agent/tools.py",
      body: "Seven thin wrappers. Validate the arguments, call retrieval, record a trace entry, spend one iteration.",
    },
    {
      id: "retrieval",
      name: "Retrieval engine",
      owning: "apps/api/retrieval/",
      body: "Owns the index, the scoring, the evidence selection, and the context budget.",
    },
    {
      id: "generation",
      name: "Generation",
      owning: "apps/api/generation/",
      body: "Renders the opaque prompt, calls the local backend, assembles citations, enforces the advice blocklist.",
    },
    {
      id: "store",
      name: "Store",
      owning: "apps/api/ingestion/db.py",
      body: "One SQLite file holds the documents, the node tree, the query log, and the evaluation rows.",
    },
  ],
  /**
   * The owning layer of each graph node, derived from what the node calls.
   * The API surface and the store are not graph nodes, so they appear only
   * in the layer stack.
   */
  layerOf: architectureLayerOf,
  /**
   * The nine graph nodes, in the order the compiled graph visits them.
   * `branch` marks the one place the graph loops: the sufficiency gate
   * can send the run back through one broaden-retry.
   */
  workflow: {
    caption: "The compiled graph, node by node",
    lead:
      "Nine graph nodes and one back-edge. The sufficiency gate can send a run back through a single retry before the answer is built. A separate, non-graph retry re-plans an empty plan once.",
    branchLabel: "RETRY",
    legend: "legend",
    /**
     * The edges are a separate table because the graph is not a list: a node
     * can have more than one outgoing edge, and one of them can go backwards.
     */
    edges: architectureEdges,
    steps: [
      { name: "query_plan", body: "Split the question into one to four sub-questions. An empty plan is invalid and the run fails closed." },
      { name: "search", body: "One search_nodes call per sub-question, top_k 10, capped at 20, until the iteration budget runs out." },
      { name: "inspect", body: "Inspect the top three candidates per sub-question. A node that cannot contribute evidence is marked irrelevant." },
      { name: "expand_or_backtrack", body: "The inspect wrapper marks a branch that carried nothing as irrelevant; this node expands one surviving branch per sub-question with radius 1." },
      { name: "select_evidence", body: "Retrieval picks the kept set and decides which node supports which sub-question." },
      { name: "sufficiency_gate", body: "The gate passes when every planned sub-question has a supporter in the kept set." },
      { name: "broaden_search", body: "The one retry. For each failing sub-question it builds one alternative query: the identifier-only form when the sub-question names a provision, otherwise the stopword-stripped form. It spends from the same budget.", branch: true },
      { name: "build_context", body: "Evidence becomes an opaque prompt context under a 12,000 character budget." },
      { name: "generate", body: "The local model answers from that context. The prompt requires every factual sentence to carry a citation." },
    ],
  },
  /**
   * The five retrieval channels. These are the five weights the scorer
   * actually multiplies, named by their key in WEIGHT_DEFAULTS.
   */
  channels: {
    caption: "Five channels, one rank",
    lead:
      "Retrieval scores every node in the document on five channels, all weights 1.0 by default, then ranks once. There is no embedding on this path.",
    items: [
      { key: "lex", label: "Lexical", body: "BM25 over one document's leaf bodies and section-intro nodes, with the heading line kept in the corpus." },
      { key: "ident", label: "Identifier", body: "A canonical label lookup. A node owning an exact article label scores 1.0; a node that merely inherits a container label scores 0.5. Only a unique owner of an exact label is put first in the rank." },
      { key: "title", label: "Title", body: "Jaccard overlap between the query tokens and the node's heading tokens." },
      { key: "ref", label: "Reference", body: "A node reachable within two reference hops from a lexical seed would score 1.0. The channel is 0 across the current corpus: ingestion does not parse reference edges yet." },
      { key: "hier", label: "Hierarchy", body: "A node whose parent is the parent of a lexical seed scores 1.0." },
    ],
    footnote:
      "The rank is one order: the node the query identifies first, then score descending, then depth, position, and id.",
  },
  /**
   * The dense baseline, drawn apart from the shipped path on purpose.
   */
  baseline: {
    label: "Evaluation arm",
    name: "Dense vector baseline",
    owning: "experiments/vector/",
    body:
      "One dense embedding per node of the lexical corpus, ranked by a min-max-normalized cosine. It sits beside the shipped path, not inside it: only apps/api/evaluation/ may import it, and the CI import scan fails the build if anything else does.",
    status: "The comparison is run and its reports are published. The outcome is undecided.",
  },
  footnote:
    "The node tree is written at ingest and read through the tools. The agent writes no document rows of its own: its only database write is the ask-trace row that records the run.",
};

export const guardrails = {
  headline: "Guardrails that are checked, not promised",
  lead: "Each item below is a contract with a mechanical check behind it.",
  items: [
    {
      title: "Local-first",
      body: "No document bytes leave the machine. Generation runs against a local backend behind one interface.",
    },
    {
      title: "Vectorless quarantine",
      body: "The primary path cannot import the embedding libraries. A CI import scan fails the build on a deny-list match.",
    },
    {
      title: "Refusal trigger",
      body: "If no kept evidence supports a sub-question, the answer refuses and returns an empty citation list.",
    },
    {
      title: "Advice blocklist",
      body: "Generated output is rejected when it contains advice phrasing, in the prompt and again after generation.",
    },
    {
      title: "Persistent disclaimer",
      body: "The disclaimer below stays visible wherever an answer is shown, not only on first use.",
    },
  ],
  disclaimer:
    "Tuklas is a research discovery tool, not a lawyer. Verify provisions before relying on them.",
};

export const runLocally = {
  headline: "Run it from a clone",
  lead: "The intended local setup for a builder. Nothing here is a download or a release.",
  status:
    "Build state: the specification, the API service, the retrieval engine, the agent loop, the evaluation harness and the controlled comparison are implemented and tested. The comparison's outcome is undecided, so no metric on this page reports a measured value.",
  commands: [
    { label: "Clone", command: "git clone https://github.com/donyelqt/Tuklas-Agent.git" },
    { label: "Install", command: "poetry install" },
    { label: "API", command: "poetry run uvicorn apps.api.api.app:app --reload" },
    { label: "Web", command: "npm --prefix apps/web run dev" },
  ],
  repoTree: `apps/web/                      Next.js App Router, marketing at /, explorer at /app/*
apps/api/                      FastAPI service under the /v1 prefix
  api/  ingestion/  retrieval/  agent/  generation/  evaluation/  models/
data/
  documents/  indexes/  datasets/  eval-reports/
experiments/
  vector/                      dense baseline, imported only by evaluation
docs/                          specification, contracts, decisions, run reports
scripts/`,
  caption: "Intended local setup, not an installer. The API entrypoint is the app factory in apps/api/api/app.py; the published comparison reports live in data/eval-reports.",
};

export const footer = {
  note: "Tuklas Agent is a local-first research artifact for private law document discovery. Built to be read, ablated, and reproduced.",
  links: [
    { label: "Specification", href: "https://github.com/donyelqt/Tuklas-Agent/tree/main/docs" },
    { label: "Retrieval contract", href: "https://github.com/donyelqt/Tuklas-Agent/blob/main/docs/retrieval.md" },
    { label: "Evaluation framework", href: "https://github.com/donyelqt/Tuklas-Agent/blob/main/docs/evaluation.md" },
    { label: "Repository", href: "https://github.com/donyelqt/Tuklas-Agent" },
  ],
  legal:
    "2026 Tuklas Agent. Research use, not legal advice. Built by Doniele Antonio.",
};