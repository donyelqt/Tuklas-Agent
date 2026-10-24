# Tuklas Agent: Landing Page Copy Deck

**Status:** Copy contract. Load-bearing for `apps/web/src/lib/copy.ts`.
 **Sources of truth:** `docs/PRD.md` (section 2, 5, 6, 7), `docs/evaluation.md` (section 1, 7), `docs/retrieval.md` (section 5), `docs/architecture.md` (section 1, 4), `docs/landing-page/DESIGN.md` (sections 2, 7), `docs/decisions/ADR-004-local-llm-interface.md`.
**Rules applied:** no superiority claim, no fabricated metric, no legal-advice framing, no social proof, no fake install, no em-dash or en-dash anywhere, no inline strings in JSX.

Every string below is transcribed verbatim into `apps/web/src/lib/copy.ts`. Nothing in that file exists outside this document.

---

## Nav

**Layout intent:** a sticky single-row nav with anchor links, then the two locked CTAs.

Export: `nav`

| Field | String |
|---|---|
| `items[0].label` | Problem |
| `items[0].href` | `#problem` |
| `items[1].label` | Pipeline |
| `items[1].href` | `#pipeline` |
| `items[2].label` | Tools |
| `items[2].href` | `#tools` |
| `items[3].label` | Comparison |
| `items[3].href` | `#comparison` |
| `items[4].label` | Guardrails |
| `items[4].href` | `#guardrails` |
| `primaryCta.label` | Read the spec |
| `primaryCta.href` | https://github.com/donyelqt/Tuklas-Agent/tree/main/docs |
| `secondaryCta.label` | View on GitHub |
| `secondaryCta.href` | https://github.com/donyelqt/Tuklas-Agent |

CTA intent lock from DESIGN.md section 7: "Read the spec" is the only read-docs intent, "View on GitHub" is the only repository intent, reused verbatim in nav, hero, and footer.

---

## Hero

**Layout intent:** asymmetric split, copy left, real tree preview right, headline over at most two desktop lines.

Export: `hero`

| Field | String |
|---|---|
| `headline` | Retrieval starts at the provision, not the chunk |
| `subhead` | Tuklas navigates a document tree with lexical, structural, and reference signals, and cites an article and page on every claim. |
| `ctaPrimary` | Read the spec |
| `ctaSecondary` | View on GitHub |
| `previewCaption` | The explorer tree the agent navigates, as returned by the documents tree endpoint. |

Headline is 8 words. Subhead is 20 words.

### Hero options considered

The claim had to be about the method, since no comparison has been run.

- **Chosen:** "Retrieval starts at the provision, not the chunk". It names the unit of retrieval, which is the actual design decision in `docs/PRD.md` section 5 F2, and it reads as a method statement rather than a result.
- Rejected: "Vectorless RAG for legal documents". Accurate but it is a category label, it says nothing about what changes, and it leads with the absence rather than the design.
- Rejected: "Citation-first retrieval for statutes". Half true, and "citation-first" flatters a generation-time property as if it were a retrieval property.
- Rejected: "Answers with the article and a page". That is a result framing, and no run exists to support it.

No kicker, no version label, no trust strip. Three text elements plus the preview caption, per DESIGN.md section 7.

---

 ## Backends strip

 **Layout intent:** one infinite marquee of wordmark logos under a single label, logos only, no text under any logo.

 Export: `backends`

 The label admits two kinds of thing (three runtimes + four open-weight models, per DESIGN.md §7), not one:

 | Field | String |
 |---|---|
 | `label` | Local runtimes, and the open-weight models they serve |
 | `backends[0]` / `backends[1]` / `backends[2]` | Ollama / ollama | llama.cpp / llamacpp | vLLM / vllm |
 | `models[0]` / `models[1]` / `models[2]` / `models[3]` | Gemma / googlegemini | Qwen / qwen | Mistral / mistralai | DeepSeek / deepseek |

 ADR-004 fixes the generation interface to `ollama | llamacpp | vllm` and rejects cloud backends; the three runtimes are the only entries the strip may list as supported. The four models are examples of what those runtimes serve locally, not providers Tuklas claims to support.

---

## Hierarchy problem

**Layout intent:** two labelled columns, flat pipeline on one side, kept structure on the other, with one note line beneath.

Export: `problem`

| Field | String |
|---|---|
| `headline` | A statute is a tree, and a chunk is not |
| `lead` | Splitting a document into flat windows discards the relationships a legal answer has to rest on. |
| `flat.label` | What a flat pipeline carries |
| `structured.label` | What the structure carries |
| `flat.items[0]` | A fixed-size window of text cut at an arbitrary offset |
| `flat.items[1]` | A vector with no parent, child, or sibling |
| `flat.items[2]` | A citation that points at a window, not an article |
| `flat.items[3]` | No way to walk to the neighbouring provision |
| `structured.items[0]` | Book IV, Title II, Chapter 1, Articles 1318, 1319, 1320 as parent and child |
| `structured.items[1]` | Sibling provisions that share a parent |
| `structured.items[2]` | Cross references between articles, parsed into graph edges |
| `structured.items[3]` | A stable location: article, section, and page |
| `note` | Structure is kept at ingest so a query can use it, rather than trying to recover it from text. |

Lead is 15 words. The hierarchy example is the PRD section 2 example. No competitor is named, and the PRD section 2 evidence entry records hierarchy loss as an assumption, so the copy describes what a flat pipeline drops rather than claiming a measured cost.

---

## Pipeline

**Layout intent:** numbered divide-list, seven rows, verb name in bold plus one sentence.

Export: `pipeline`

| Field | String |
|---|---|
| `headline` | Seven steps from PDF to cited answer |
| `lead` | Each step is a contract in the codebase, not a stage name on a slide. |
| `steps[0].name` | Ingest |
| `steps[0].body` | PyMuPDF extracts pages, text, and metadata from a text-layer PDF, and identical bytes are rejected by hash. |
| `steps[1].name` | Parse |
| `steps[1].body` | A regex and heading-match parser writes a Book, Title, Chapter, Section, and Article tree, or flags a flat fallback. |
| `steps[2].name` | Index |
| `steps[2].body` | SQLite stores the node tree, with BM25 over leaf bodies and an identifier index built at ingest. |
| `steps[3].name` | Plan |
| `steps[3].body` | The planner emits one to four sub-questions, and a plan with none is treated as invalid. |
| `steps[4].name` | Retrieve |
| `steps[4].body` | The agent navigates the tree through seven tools, scoring lexical, structural, and reference signals in two passes. |
| `steps[5].name` | Cite |
| `steps[5].body` | Every factual sentence renders a citation that resolves to an article and a page. |
| `steps[6].name` | Abstain |
| `steps[6].body` | When the kept evidence is insufficient, the answer returns no citations instead of an unsupported claim. |

Step numbers `01` through `07` are rendered by the component, not stored as copy. Longest body is 20 words.

---

## Tools

**Layout intent:** one monospace code block of the seven tool signatures, with a caption naming where they are enforced.

Export: `tools`

| Field | String |
|---|---|
| `headline` | Seven tools are the only way in |
| `lead` | The agent has no direct database or file access to the document. Every node it sees arrives through one of these signatures. |
| `caption` | Signatures are enforced in the agent wrappers in apps/api/agent/, with output caps in docs/architecture.md section 4. |

`signature`, one template literal, transcribed from `docs/retrieval.md` section 5 with parameter names and defaults unchanged:

```text
search_nodes(query: str, document_id: str, top_k: int = 10) -> list[NodeHit]
inspect_node(node_id: str) -> NodeFull
list_children(node_id: str, limit: int = 50) -> list[NodeSummary]
get_parent(node_id: str) -> NodeSummary | None
expand_context(node_id: str, radius: int = 1) -> NodeFull
follow_reference(reference_id: str) -> FollowResult
get_source(node_id: str) -> Source
select_evidence(hits: list[NodeHit], sub_questions: list[str]) -> EvidenceSet
```

No tool was invented and no default was changed. The seven count covers `search_nodes`, `inspect_node`, `list_children`, `get_parent`, `expand_context`, `follow_reference`, `get_source`, and `select_evidence` as the retrieval-owned evidence step that closes the same contract.

---

## Architecture

**Layout intent:** a layered grid. Package rows in roughly request order, with the store drawn last because it is written at ingest and read by retrieval before generation, each with a gutter rule that shows the direction of travel, then the compiled graph as a node grid, then the five retrieval channels as a definition list, then the dense baseline drawn apart from the shipped path.

Export: `architecture`

| Field | String |
|---|---|
| `headline` | Which package owns which step |
| `lead` | The agent orchestrates and nothing else. It reads the document tree only through seven tool signatures, and every layer below is a real module boundary in apps/api/. |
| `workflow.caption` | The compiled graph, node by node |
| `workflow.lead` | Nine graph nodes and one back-edge. The sufficiency gate can send a run back through a single retry before the answer is built. A separate, non-graph retry re-plans an empty plan once. |
| `channels.caption` | Five channels, one rank |
| `channels.lead` | Retrieval scores every node in the document on five channels, all weights 1.0 by default, then ranks once. There is no embedding on this path. |
| `channels.footnote` | The rank is one order: the node the query identifies first, then score descending, then depth, position, and id. |
| `baseline.label` | Evaluation arm |
| `baseline.name` | Dense vector baseline |
| `baseline.owning` | experiments/vector/ |
| `baseline.body` | One dense embedding per node of the lexical corpus, ranked by a min-max-normalized cosine. It sits beside the shipped path, not inside it: only apps/api/evaluation/ may import it, and the CI import scan fails the build if anything else does. |
| `baseline.status` | The comparison is run and its reports are published. The outcome is undecided. |
| `footnote` | The node tree is written at ingest and read through the tools. The agent writes no document rows of its own: its only database write is the ask-trace row that records the run. |

`layers`, six entries, each with `id`, `name`, `owning`, and `body`:

| `name` | `owning` | `body` |
|---|---|---|
| FastAPI surface | apps/api/api/ | Routes under the /v1 prefix. Upload, ask, tree, and evaluation endpoints. |
| Tuklas agent | apps/api/agent/ | A LangGraph state machine. It plans, calls tools, reads a gate verdict, and never scores anything itself. |
| Tool layer | apps/api/agent/tools.py | Seven thin wrappers. Validate the arguments, call retrieval, record a trace entry, spend one iteration. |
| Retrieval engine | apps/api/retrieval/ | Owns the index, the scoring, the evidence selection, and the context budget. |
| Generation | apps/api/generation/ | Renders the opaque prompt, calls the local backend, assembles citations, enforces the advice blocklist. |
| Store | apps/api/ingestion/db.py | One SQLite file holds the documents, the node tree, the query log, and the evaluation rows. |

`workflow.steps`, nine entries. The `name` is the real graph node name in `apps/api/agent/graph.py`:

| `name` | `body` |
|---|---|
| query_plan | Split the question into one to four sub-questions. An empty plan is invalid and the run fails closed. |
| search | One search_nodes call per sub-question, top_k 10, capped at 20, until the iteration budget runs out. |
| inspect | Inspect the top three candidates per sub-question. A node that cannot contribute evidence is marked irrelevant. |
| expand_or_backtrack | The inspect wrapper marks a branch that carried nothing as irrelevant; this node expands one surviving branch per sub-question with radius 1. |
| select_evidence | Retrieval picks the kept set and decides which node supports which sub-question. |
| sufficiency_gate | The gate passes when every planned sub-question has a supporter in the kept set. |
| broaden_search | The one retry. For each failing sub-question it builds one alternative query: the identifier-only form when the sub-question names a provision, otherwise the stopword-stripped form. It spends from the same budget. |
| build_context | Evidence becomes an opaque prompt context under a 12,000 character budget. |
| generate | The local model answers from that context. The prompt requires every factual sentence to carry a citation. |

`broaden_search` is the only entry that carries the `branch: true` marker, because it is the only node the gate can loop back to.

`channels.items`, five entries. The `key` is the real weight key in `apps/api/retrieval/scoring.py` `WEIGHT_DEFAULTS`:

| `key` | `label` | `body` |
|---|---|---|
| lex | Lexical | BM25 over one document's leaf bodies and section-intro nodes, with the heading line kept in the corpus. |
| ident | Identifier | A canonical label lookup. A node owning an exact article label scores 1.0; a node that merely inherits a container label scores 0.5. Only a unique owner of an exact label is put first in the rank. |
| title | Title | Jaccard overlap between the query tokens and the node's heading tokens. |
| ref | Reference | A node reachable within two reference hops from a lexical seed would score 1.0. The channel is 0 across the current corpus: ingestion does not parse reference edges yet. |
| hier | Hierarchy | A node whose parent is the parent of a lexical seed scores 1.0. |

Every layer name is a real package path. Every workflow step is a real node in the compiled graph. Every channel key is a real weight key. The nine graph nodes are the nine `graph.add_node` calls in `apps/api/agent/graph.py`, and `broaden_search` is the only conditional edge back.

The dense baseline is drawn as a sibling of the shipped path, never inside it, because that is what it is: an evaluation arm imported only by `apps/api/evaluation/`. No metric appears on this section and no comparison is stated.

---

## Comparison

**Layout intent:** two-arm diagram, magenta for the Tuklas arm, cyan for the quarantined dense baseline, with the real dataset facts as a labelled definition list below and a status line on its own.

Export: `comparison`

| Field | String |
|---|---|
| `headline` | The comparison is designed, not won |
| `lead` | Two retrieval arms, one dataset, one generation model. Both arms run the same agent and the same judge; the arms' retrieval layers draw on different signals, and the comparison's outcome is undecided. |
| `tuklas.label` | Tuklas |
| `tuklas.items[0]` | BM25 over leaf bodies, with the heading line kept in the corpus |
| `tuklas.items[1]` | Structural signal from parent, child, and sibling position |
| `tuklas.items[2]` | Reference signal from cross-reference edges |
| `tuklas.items[3]` | No dense embedding on the primary path |
| `vector.label` | Dense vector baseline |
| `vector.items[0]` | Dense embeddings and similarity search |
| `vector.items[1]` | Quarantined to experiments/vector/ |
| `vector.items[2]` | Importable only by the evaluation runner |
| `vector.items[3]` | Scanned by the CI import rule |
| `facts[0].label` / `.value` | Dataset / 72 questions: 24 dev for tuning, 48 test reported |
| `facts[1].label` / `.value` | Categories / 7 categories, at least 3 dev and 6 test each |
| `facts[2].label` / `.value` | Primary metric / Recall@5 |
| `facts[3].label` / `.value` | Secondary metric / MRR@10 |
| `facts[4].label` / `.value` | Generation / Same model, backend, and seed in both arms, temperature 0 |
| `facts[5].label` / `.value` | Judge / Arm-blinded, sentence-level faithfulness scoring |
| `facts[6].label` / `.value` | Spot check / 20 percent of test answers re-scored; disagreement above 15 percent invalidates the run |
| `facts[7].label` / `.value` | Provisional margins / 0.10 on Recall@5 and faithfulness, 0.20 on any single category gap |
| `facts[8].label` / `.value` | Margin status / Placeholders recorded in the eval config, not results |
| `decisionRule` | Both numbers are published per category and overall. There is no win ceremony. |
| `expectedPattern` | The expected pattern is competitive on cross-reference and multi-hop questions, weaker on paraphrase-heavy definitions, and a uniform sweep across every category is treated as a leakage smell rather than a win. |
| `status` | The controlled comparison has been run and its reports are published in data/eval-reports. The outcome is undecided, so no metric on this page reports a measured value. |

Every fact traces to `docs/evaluation.md` sections 1, 3, 4, and 6, or `docs/PRD.md` section 6. The 0.10 and 0.20 values are stated as provisional margins and immediately labelled as placeholders in the eval config, per the PRD. No percentage sign appears anywhere: the spot-check shares are written as words. `Recall@5` and `MRR@10` appear as metric names with no measured value attached.

---

## Guardrails

**Layout intent:** two-column divide-list, then the disclaimer on its own full-width band.

Export: `guardrails`

| Field | String |
|---|---|
| `headline` | Guardrails that are checked, not promised |
| `lead` | Each item below is a contract with a mechanical check behind it. |
| `items[0].title` | Local-first |
| `items[0].body` | No document bytes leave the machine. Generation runs against a local backend behind one interface. |
| `items[1].title` | Vectorless quarantine |
| `items[1].body` | The primary path cannot import the embedding libraries. A CI import scan fails the build on a deny-list match. |
| `items[2].title` | Refusal trigger |
| `items[2].body` | If no kept evidence supports a sub-question, the answer refuses and returns an empty citation list. |
| `items[3].title` | Advice blocklist |
| `items[3].body` | Generated output is rejected when it contains advice phrasing, in the prompt and again after generation. |
| `items[4].title` | Persistent disclaimer |
| `items[4].body` | The disclaimer below stays visible wherever an answer is shown, not only on first use. |
| `disclaimer` | Tuklas is a research discovery tool, not a lawyer. Verify provisions before relying on them. |

The disclaimer is byte-for-byte the PRD section 7 US-2 string.

---

## Run locally

**Layout intent:** command block with a copy button, repo tree beneath it, and a plain build-state line above.

Export: `runLocally`

| Field | String |
|---|---|
| `headline` | Run it from a clone |
| `lead` | The intended local setup for a builder. Nothing here is a download or a release. |
| `status` | Build state: the specification, the API service, the retrieval engine, the agent loop, the evaluation harness and the controlled comparison are implemented and tested. The comparison's outcome is undecided, so no metric on this page reports a measured value. |
| `commands[0].label` / `.command` | Clone / `git clone https://github.com/donyelqt/Tuklas-Agent.git` |
| `commands[1].label` / `.command` | Install / `poetry install` |
| `commands[2].label` / `.command` | API / `poetry run uvicorn apps.api.api.app:app --reload` |
| `commands[3].label` / `.command` | Web / `npm --prefix apps/web run dev` |
| `caption` | Intended local setup, not an installer. The API entrypoint is the app factory in apps/api/api/app.py; the published comparison reports live in data/eval-reports. |

 `repoTree`, one template literal, from `docs/architecture.md` section 1. (`src/lib/` holds `copy.ts` and `site.ts` - not `design.ts`; the marquee is `backend-marks.tsx`, mapping slugs from `copy.ts`.)

```text
apps/web/                      Next.js App Router, marketing at /, explorer at /app/*
  api/  ingestion/  retrieval/  agent/  generation/  evaluation/  models/
data/
  documents/  indexes/  datasets/  eval-reports/
experiments/
  vector/                      dense baseline, imported only by evaluation
docs/                          specification, contracts, decisions, run reports
scripts/
```

The web command is the only one that works against the current repository. The caption and the status line both say so, so the block cannot be read as a working installer.

---

## Footer

**Layout intent:** two short lines and a link list, no social proof, no newsletter.

Export: `footer`

| Field | String |
|---|---|
| `note` | Tuklas Agent is a local-first research artifact for private law document discovery. Built to be read, ablated, and reproduced. |
| `links[0].label` / `.href` | Specification / https://github.com/donyelqt/Tuklas-Agent/tree/main/docs |
| `links[1].label` / `.href` | Retrieval contract / https://github.com/donyelqt/Tuklas-Agent/blob/main/docs/retrieval.md |
| `links[2].label` / `.href` | Evaluation framework / https://github.com/donyelqt/Tuklas-Agent/blob/main/docs/evaluation.md |
| `links[3].label` / `.href` | Repository / https://github.com/donyelqt/Tuklas-Agent |
| `legal` | 2026 Tuklas Agent. Research use, not legal advice. |

Plain year only, no copyright theatre.

---

## Site metadata

Export: `site` in `apps/web/src/lib/site.ts`.

| Field | String |
|---|---|
| `name` | Tuklas Agent |
| `shortName` | Tuklas |
| `tagline` | Vectorless, structure-aware retrieval for private law documents. |
| `repository` | donyelqt/Tuklas-Agent |
| `repositoryUrl` | https://github.com/donyelqt/Tuklas-Agent |
| `docsUrl` | /tree/main/docs |
| `apiBaseUrl` | /tree/main/apps/api |

Tagline is 6 words and states a method, not a result.

---

## Copy self-audit

Strings flagged during drafting and rewritten, with the reason.

| Export and field | Before | After | Why |
|---|---|---|---|
| `hero.headline` | Citation-grounded answers for legal research | Retrieval starts at the provision, not the chunk | "Grounded answers" is a result claim with no measurement behind it. Replaced with the method claim. |
| `hero.subhead` | The vectorless RAG engine that beats dense retrieval on statutes | Tuklas navigates a document tree with lexical, structural, and reference signals, and cites an article and page on every claim. | "Beats" is a banned superiority claim and no run exists. |
| `hero.previewCaption` | Live document explorer, synced in real time | The explorer tree the agent navigates, as returned by the documents tree endpoint. | Nothing is live, released, or synced. Points at the real endpoint shape instead. |
| `problem.headline` | Why other RAG pipelines fail on legal documents | A statute is a tree, and a chunk is not | "Other RAG pipelines" named competitors the PRD does not name, and failed to state the issue. |
| `problem.lead` | Flat chunking destroys hierarchy, which measurably hurts legal question answering | Splitting a document into flat windows discards the relationships a legal answer has to rest on. | The PRD records hierarchy loss as an assumption, not a measurement. |
| `problem.structured.items[0]` | Book IV, Title II, Chapter 1, Articles 1318-1320 | Book IV, Title II, Chapter 1, Articles 1318, 1319, 1320 as parent and child | The original needed an en-dash, which is banned. Listing the articles also shows the sibling set explicitly. |
| `pipeline.lead` | A simple pipeline from upload to answer | Each step is a contract in the codebase, not a stage name on a slide. | "Simple" undersells the specs; the rewrite points at what the reader can check. |
| `pipeline.steps[2].body` | Build a vector index of every chunk | SQLite stores the node tree, with BM25 over leaf bodies and an identifier index built at ingest. | The vector index is the arm Tuklas does not have. This is the arm it does. |
| `tools.headline` | Powerful agentic search tools | Seven tools are the only way in | "Powerful" is a banned filler claim and states nothing. |
| `tools.lead` | A fully agentic retrieval stack | The agent has no direct database or file access to the document. Every node it sees arrives through one of these signatures. | "Fully agentic" is unfalsifiable. The replacement is the checkable architecture rule. |
| `comparison.headline` | How Tuklas outperforms vector RAG | The comparison is designed, not won | Direct violation of honesty rule 1. |
| `comparison.lead` | See how our vectorless engine outperforms dense retrieval | Two retrieval arms, one dataset, one generation model. Both arms run the same agent and the same judge; the arms' retrieval layers draw on different signals, and the comparison's outcome is undecided. | Same violation, and it implied a run had happened. The replacement is now corrected too: the arms do not differ only in retrieval (ADR-006 section 1), and the comparison has been run, so the string says the outcome is undecided rather than that nothing was measured. |
| `comparison.facts[6].value` | 20% of test answers re-scored, >15% disagreement invalidates the run | 20 percent of test answers re-scored; disagreement above 15 percent invalidates the run | Percentage signs are banned on this page. Spelled out; the numbers themselves are the real protocol values. |
| `comparison.facts[7].value` | Competitive if within 10% on Recall@5 and faithfulness | 0.10 on Recall@5 and faithfulness, 0.20 on any single category gap | A percentage sign implied a measured ratio. The margin is a config placeholder, so it is written as the raw config value. |
| `comparison.expectedPattern` | Tuklas wins on cross-reference and multi-hop questions | The expected pattern is competitive on cross-reference and multi-hop questions, weaker on paraphrase-heavy definitions, and a uniform sweep across every category is treated as a leakage smell rather than a win. | An expectation stated as a result, and it dropped the weaker-case half of the honest reading. |
| `comparison.status` | Results coming soon | The controlled comparison has been run and its reports are published in data/eval-reports. The outcome is undecided, so no metric on this page reports a measured value. | A launch teaser implies a schedule the repository does not support. The replacement is now corrected too: the run has happened, so the string says the outcome is undecided rather than that no run has happened. |
| `guardrails.headline` | Bank-grade security and enterprise-grade safety | Guardrails that are checked, not promised | Unverifiable marketing adjectives. |
| `runLocally.status` | Get started in under five minutes | Build state: the specification, the API service, the retrieval engine, the agent loop, the evaluation harness and the controlled comparison are implemented and tested. The comparison's outcome is undecided, so no metric on this page reports a measured value. | A timing promise about code that does not exist. Honesty rule 6. The replacement is now corrected too: the evaluation harness and the controlled comparison are built, so the build-state line lists what is implemented rather than what is not. |
| `footer.note` | Trusted by legal researchers worldwide | Tuklas Agent is a local-first research artifact for private law document discovery. Built to be read, ablated, and reproduced. | Fake social proof, banned by honesty rule 5. There are no users. |

Strings deliberately kept out: any latency or token figure, any Recall or MRR value, any "competitive" result statement, any user count, any download link, and the advice phrases "you should", "I advise", "your case", "you must".