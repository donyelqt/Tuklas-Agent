export const nav = {
  items: [
    { label: "Problem", href: "#problem" },
    { label: "Pipeline", href: "#pipeline" },
    { label: "Tools", href: "#tools" },
    { label: "Comparison", href: "#comparison" },
    { label: "Guardrails", href: "#guardrails" },
  ],
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
      "Cross references between articles, resolved as graph edges",
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
      body: "SQLite stores nodes and reference edges, with BM25 over leaf bodies and an identifier index built at ingest.",
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
  lead: "Two retrieval arms, one dataset, one generation model. The arms differ only in retrieval, and nothing has been measured yet.",
  tuklas: {
    label: "Tuklas",
    items: [
      "BM25 over leaf bodies with headings stripped",
      "Structural signal from parent, child, and sibling position",
      "Reference signal from resolved cross-reference edges",
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
  status: "No evaluation run has happened yet, so no metric on this page reports a measured value.",
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
    "Build state: the specification, the evaluation contract, and the Next.js shell are in the repository. The API service, the retrieval engine, and the agent loop are specified but not implemented, and the comparison has not been run.",
  commands: [
    { label: "Clone", command: "git clone https://github.com/donyelqt/Tuklas-Agent.git" },
    { label: "Install", command: "uv sync" },
    { label: "API", command: "uvicorn apps.api.main:app --reload" },
    { label: "Web", command: "npm --prefix apps/web run dev" },
  ],
  repoTree: `apps/web/                      Next.js App Router, marketing at /, explorer at /app/*
apps/api/                      FastAPI service under the /v1 prefix
  api/  ingestion/  retrieval/  agent/  generation/  evaluation/  models/
data/
  documents/  indexes/  datasets/
experiments/
  vector/                      dense baseline, imported only by evaluation
  vectorless/  results/
docs/                          specification and contracts
scripts/`,
  caption: "Intended local setup, not an installer. The API entrypoint is specified in docs/architecture.md and does not exist yet.",
};

export const footer = {
  note: "Tuklas Agent is a local-first research artifact for private law document discovery. Built to be read, ablated, and reproduced.",
  links: [
    { label: "Specification", href: "https://github.com/donyelqt/Tuklas-Agent/tree/main/docs" },
    { label: "Retrieval contract", href: "https://github.com/donyelqt/Tuklas-Agent/blob/main/docs/retrieval.md" },
    { label: "Evaluation framework", href: "https://github.com/donyelqt/Tuklas-Agent/blob/main/docs/evaluation.md" },
    { label: "Repository", href: "https://github.com/donyelqt/Tuklas-Agent" },
  ],
  legal: "2026 Tuklas Agent. Research use, not legal advice.",
};