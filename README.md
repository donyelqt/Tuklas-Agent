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

- **Ingestion.** PyMuPDF parses a PDF into a structural tree (Book, Title,
  Chapter, Section, Article). Every node keeps its page span and its
  hierarchy path, so a citation resolves to a provision and a page. A
  structurally ambiguous document gets a flagged flat fallback with a
  confidence value, never a confidently wrong deep tree.
- **Retrieval.** A five-component vectorless scorer over that tree: `lex`
  (per-document BM25), `ident` (identifier precedence), `title` (Jaccard
  overlap with the node's heading), `ref` (cross-reference) and `hier`
  (shared hierarchy). The lexical component is per-document on purpose, so
  its scores cannot drift when another document is ingested.
- **Agent.** A LangGraph loop (plan, search, inspect, expand, sufficiency
  gate) over exactly seven tools - `search_nodes`, `inspect_node`,
  `list_children`, `get_parent`, `expand_context`, `follow_reference`,
  `get_source` - capped at twelve iterations, able to abandon a dead end and
  say so in the trace.
- **Generation.** A local model through an interchangeable interface
  (llama.cpp, Ollama or vLLM, per ADR-004), grounded strictly on the
  retrieved evidence, with a refusal path when the sufficiency gate fails and
  an advice-phrase blocklist on the way out.
- **Baseline.** A dense-vector arm lives in `experiments/vector/` and is
  imported only by the evaluation package, so the comparison is real and the
  primary path stays vectorless. CI enforces this with
  `scripts/check_no_vectors.py`.
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
| test-48 | dense baseline | 0.707 | - | - | 41 |
| dev-24 | Tuklas (vectorless) | 0.674 | 0.500 | 0.711 | 23 |
| dev-24 | dense baseline | 0.761 | - | - | 23 |

Two caveats travel with those numbers, and the reports carry both:

1. **The two published reports are not comparable with each other.** The
   retrieval metric changed instrument between them (it is now computed over
   the node ids the answer was produced from, where the earlier report used a
   separate one-off search over the whole question, a ranking the model never
   saw), and the run row records neither the instrument nor the lex path.
   Neither report is a ratchet over the other.
2. **The citation-failure numbers are a lower bound**, dominated by the
   scorer's strictness: no string rule can match a proposition *about* a
   provision to the provision's own words. The two arms' lower bounds are not
   comparable with each other either, and the report says so.

The comparison's outcome is undecided. Read the reports, not a summary of
them.

## Requirements

- Python 3.11, with the dependencies pinned in `pyproject.toml` (fastapi,
  uvicorn, langgraph, pymupdf, rank-bm25, fastembed, pydantic).
- Node 22, for `apps/web`.
- A local LLM backend for generation. The measured runs used Ollama with
  `qwen2.5:3b`; the interface is interchangeable per ADR-004.
- No cloud service, no API key, and no document bytes leaving the machine.

## Quick start

Windows is the environment these commands were run in; the `.venv` lives at
`.venv/Scripts/python.exe`.

```bash
# 1. the corpus: two public statutes, sha256-verified against docs/CORPUS.md.
#    The PDFs are gitignored (ADR-002) - they are third-party artefacts.
python -m scripts.fetch_corpus

# 2. the API. It creates its own store and indexes on first use.
PYTHONPATH=apps/api .venv/Scripts/python.exe -m uvicorn apps.api.api.app:create_app --factory --port 8000

# 3. ingest a document over HTTP (multipart upload):
#    curl -X POST http://127.0.0.1:8000/v1/documents -F "file=@<your.pdf>"
#    The API answers GET /v1/documents with the documents it holds.

# 4. the web app (the document explorer and the landing page)
npm install --prefix apps/web
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

Three rules make this repository unusual. All three are CI-enforced, and all
three are in `CONSTRAINTS.md` in full.

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
   `# noqa` or `eslint-disable`, and no unimplemented placeholders.

## Status

Everything described above is implemented and gated: the Python suite, the
ruff and mypy gates, the web type-check, lint and build, the quarantine
scan, the eval-config parity check, the reproducibility check, and coverage
on changed lines. CI runs on every pull request.

What is **not** here: no Docker image or compose file, no container or
deploy configuration, no licence file, no published package, no release, and
no install command that would imply one. The local store and the corpus are
gitignored by design; `docs/CORPUS.md` records the corpus provenance and the
checksums.

`docs/TODO.md` is the working journal: what was decided, what was measured,
and what is still open.
