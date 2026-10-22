# Tuklas Agent

A local-first, vectorless agentic retrieval system for private law document
discovery. It exists to answer one research question:

> Can agentic, structure-aware vectorless retrieval achieve competitive
> retrieval accuracy and grounding against conventional dense-vector RAG on
> long hierarchical legal documents?

**The answer is not known.** The benchmark decides it, not this document. Both
arms' numbers are published, side by side, and neither arm is called better
here. What the system does is measured and reproducible; what it means is a
reading.

Tuklas is a research and discovery tool. It is not a lawyer, it does not give
legal advice, and it has no users, no release, and no support.

---

## What the system is

- **Ingestion.** PyMuPDF parses a PDF into a structural tree (book, part,
  division, title, chapter, subchapter, section, article - the families the
  parser recognises, of which this corpus produces book, title, chapter,
  section and article). Every node keeps its page span; its hierarchy path
  is derived from the tree when a node is read. A structurally ambiguous
  document gets a flagged flat fallback with a confidence value, never a
  confidently wrong deep tree.
- **Retrieval.** A five-component vectorless scorer over that tree: `lex`
  (per-document BM25), `ident` (identifier precedence), `title` (Jaccard
  overlap with the node's heading), `ref` (cross-reference) and `hier`
  (shared hierarchy). The lexical component is per-document on purpose, so
  its scores cannot drift when another document is ingested. On this corpus
  the `ref` channel is 0 for every node, because no reference parsing is
  implemented; the reports say so.
- **Agent.** A LangGraph loop (plan, search, inspect, expand, sufficiency
  gate) over exactly seven tools - `search_nodes`, `inspect_node`,
  `list_children`, `get_parent`, `expand_context`, `follow_reference`,
  `get_source` - capped at twelve iterations, able to abandon a dead end and
  say so in the trace.
- **Generation.** A local model through an interface designed to be
  interchangeable (llama.cpp, Ollama or vLLM, per ADR-004). Ollama is the
  only backend implemented so far, and the constructor rejects an
  unimplemented one by name rather than accepting it and failing at the
  first request. The answer is grounded strictly on the retrieved evidence,
  with a refusal path when the sufficiency gate fails and an advice-phrase
  blocklist on the way out.
- **Baseline.** A dense-vector arm lives in `experiments/vector/` and is
  imported only by the evaluation package, so the comparison is real and the
  primary path stays vectorless. CI enforces this with
  `scripts/check_no_vectors.py`. It is not a second system: the dense arm
  runs the **same agent graph, the same planner, the same generation model,
  the same judge and the same metric**, with one substitution - its
  `search_nodes` returns a dense ranking instead of the vectorless one. A
  second agent would differ in more than retrieval, which is the confound
  the experiment forbids.

  What the substitution does **not** do is make this a one-variable swap.
  The two ranking functions do not draw on the same signals: the vectorless
  arm scores five components and orders identified provisions first, over
  the whole 2,579-node tree; the dense arm has **one** component - a
  min-max-normalised cosine with no BM25, no sparse term and no hybrid
  fusion, `ident`/`title`/`ref`/`hier` hard-coded to 0 - over the 2,392
  lexical nodes, with no identifier precedence. So the dense arm cannot name
  a provision and cannot reach the container nodes the vectorless arm's
  channels score. The published report says so at length ("THE ARMS ARE NOT
  LIKE-FOR-LIKE"), and per-category differences are differences of surface
  and channel as well as of mechanism. ADR-003 rejected a hybrid arm
  precisely because it would confound the comparison.

  The two arms side by side, dimension by dimension:

  | Dimension | Vectorless arm (Tuklas) | Dense baseline arm |
  |---|---|---|
  | Ranking signal | Five components: `lex`, `ident`, `title`, `ref`, `hier`. `ref` is 0 for every node on this corpus, because no reference parsing is implemented. | One min-max-normalised cosine. `ident`, `title` and `hier` are structurally absent; `ref` is 0 on this corpus too. |
  | Identifier precedence | Yes: a query that names a provision puts that provision first (ADR-005). | None. |
  | Dense-only or hybrid | Not applicable: this arm has no vector in it. | Dense only, no sparse term. |
  | Candidate set | The whole 2,579-node tree. | The 2,392 lexical nodes. |
  | Lexical score | Per-document BM25. | None: the cosine occupies the lex slot. |
  | Agent | The same LangGraph graph and the same seven tools. | The same LangGraph graph and the same seven tools, with `search_nodes` substituted. |
  | Generation and judge | The same model, backend, temperature 0, seed 42, and the same arm-blinded judge. | The same model, backend, temperature 0, seed 42, and the same arm-blinded judge. |
  | Metric | The same instrument (`answer-node-union-v1`), over the node ids the answer was produced from. | The same instrument (`answer-node-union-v1`), over the node ids the answer was produced from. |

  The node counts are the per-arm scope lines published in
  `data/eval-reports/v1-test.md`, and the channel analysis is in
  `docs/decisions/ADR-006-evaluation-framework-arms-and-dataset.md`.
- **Evaluation.** A frozen 72-question dataset (24 dev, 48 test, seven
  categories) with both arms run over the same questions, metrics published
  per category, and no verdict.

## What the measured runs show

The published reports are `data/eval-reports/v1-test.md` (48 questions) and
`data/eval-reports/v1-dev.md` (24 questions). Each publishes both arms'
numbers and a store-state note; `data/eval-reports/README.md` is the index
and says what each file is.

| Run | Arm | recall@5 | recall@1 | mrr@10 | Answerable questions |
|---|---|---|---|---|---|
| test-48 | Tuklas (vectorless) | 0.732 | 0.427 | 0.686 | 41 |
| test-48 | dense baseline | 0.707 | 0.415 | 0.657 | 41 |
| dev-24 | Tuklas (vectorless) | 0.674 | 0.500 | 0.711 | 23 |
| dev-24 | dense baseline | 0.761 | 0.457 | 0.661 | 23 |

Two caveats travel with those numbers, and the reports carry both:

1. **A run is only comparable with another run when the instrument and the
   store state are the same.** The two reports above are the CURRENT pair
   and they share an instrument, but they are different splits (48 test
   questions and 24 dev questions), so their numbers are not a ratchet over
   each other in any case. What is NOT comparable is the current report
   against the preserved `-OLD-INSTRUMENT-AND-STORE` pair: the retrieval
   metric there was computed over a separate one-off search over the whole
   question, a ranking the model never saw, where the current one is
   computed over the node ids the answer was produced from. Those older
   files are kept as a record of a measurement that was actually taken, not
   as a baseline the current system fell from. The run row does record the
   instrument and the lex path now, but the two published runs predate it,
   so their rows carry neither.
2. **The citation-failure numbers are a lower bound**, dominated by the
   scorer's strictness: no string rule can match a proposition *about* a
   provision to the provision's own words. The two arms' lower bounds are not
   comparable with each other either, and the report says so.

The comparison's outcome is undecided. Read the reports, not a summary of
them.

## Requirements

- Python 3.11, with the dependencies pinned in `pyproject.toml` (fastapi,
  uvicorn, langgraph, pymupdf, rapidfuzz, python-multipart, httpx,
  rank-bm25, fastembed, numpy, pydantic).
- Node 22, for `apps/web`.
- A local LLM backend for generation. The measured runs used Ollama with
  `qwen2.5:3b`; the interface is designed to be interchangeable per ADR-004,
  but Ollama is the only backend implemented.
- No API key, and no document bytes leaving the machine. The one thing that
  does come over the network is the baseline's embedding model, which
  fastembed downloads from Hugging Face on a cold cache; the document
  itself never does.

## Quick start

Windows is the environment these commands were run in; the `.venv` lives at
`.venv/Scripts/python.exe`.

```bash
# 1. the corpus: two public statutes, sha256-verified against docs/CORPUS.md.
#    The PDFs are gitignored (ADR-002) - they are third-party artefacts.
.venv/Scripts/python.exe -m scripts.fetch_corpus

# 2. the API. It creates its own store and indexes on first use.
.venv/Scripts/python.exe -m uvicorn apps.api.api.app:create_app --factory --port 8000

# 3. ingest a document over HTTP (multipart upload):
#    curl -X POST http://127.0.0.1:8000/v1/documents -F "file=@<your.pdf>"
#    The API answers GET /v1/documents with the documents it holds.

# 4. the web app (the document explorer and the landing page)
npm ci --prefix apps/web
npm run dev --prefix apps/web

# 5. the test suite
.venv/Scripts/python.exe -m pytest -q
```

The API serves `http://127.0.0.1:8000/v1/documents`, the document tree, and
`POST /v1/ask/stream` (an SSE stream whose event order is frozen in
`docs/api.md`). The web app runs on `http://localhost:3000` and reaches the
API at `http://127.0.0.1:8000`; override that with
`NEXT_PUBLIC_API_BASE_URL`.

Some tests need the corpus. With it present they run; with it absent they
skip themselves and say why, and the CI step reports how many skipped, so a
run without the corpus is visibly narrower rather than silently green.

## Repository map

| Path | What it is |
|---|---|
| `docs/` | The specification suite: PRD, SPEC, PLAN, TODO, architecture, api, retrieval, evaluation, CORPUS, and seven ADRs in `docs/decisions/` |
| `apps/api/` | The FastAPI service: `ingestion`, `retrieval`, `agent`, `generation`, `evaluation`, `api`, `models`, plus `tests/` |
| `apps/web/` | The Next.js document explorer and the landing page |
| `experiments/vector/` | The dense-vector baseline, quarantined to the evaluation arm |
| `data/` | The frozen dataset (`datasets/v1/`), the published reports (`eval-reports/`), and the local store plus corpus, which are gitignored |
| `scripts/` | The CI gates and the corpus fetcher |
| `CONSTRAINTS.md` | The binding quality floor |
| `AGENTS.md` | The working rules, including the honesty rules this README follows |

## The engineering floor

Three rules make this repository unusual. The first two are CI-enforced; the
third is a rule for whoever changes the code, and the diff is where it is
caught.

1. **Vectorless quarantine.** `apps/api/{ingestion,retrieval,agent,generation,api}/`
   must not import `sentence_transformers`, `faiss`, `chromadb`,
   `qdrant_client` or `pgvector`, and `cosine_similarity` must not appear in
   `apps/api/retrieval/`. Only `apps/api/evaluation/` may import
   `experiments/vector/`. A vector implementation inside the primary arm
   would void the comparison, so the check is a script, not a convention.
2. **Agent thinness.** `apps/api/agent/` reaches the tree only through the
   seven tools and must not import `sqlite3`, `sqlalchemy` or the models
   package, so the agent cannot become a second, unchecked retrieval path.
3. **No suppression comments and no stubs.** No `# type: ignore`,
   `# noqa` or `eslint-disable`, and no unimplemented placeholders. Nothing
   in CI scans for these, so they are caught in review.

## Status

Everything described above is implemented. CI runs on every pull request and
gates the Python suite, ruff, mypy, the web type-check and lint, the
quarantine scan, the eval-config parity check, the two scripts that prove
those checks fail on a violation, the reproducibility check, and coverage on
changed lines. The web production build is not among the CI steps, so run
`npm run build --prefix apps/web` before you trust a change there.

What is **not** here: no Docker image or compose file, no container or
deploy configuration, no published package, no release, and no install
command that would imply one. The local store and the corpus are gitignored
by design; `docs/CORPUS.md` records the corpus provenance and the checksums.

`docs/TODO.md` is the working journal: what was decided, what was measured,
and what is still open.

