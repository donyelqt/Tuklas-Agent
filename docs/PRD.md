# Tuklas Agent — PRD (MVP v1.0)

**Status:** Draft for review — do not implement beyond Day 1 slice until approved.
**Source:** Consolidated final spec (2026-10-07). Research question is the product.
**Type:** Local-first AI research application. Not a legal-advice system.

## 1. Executive Summary

We're building **Tuklas Agent** — a local-first agentic RAG system for **private law document discovery** — for law/legal researchers and RAG engineers, to answer the question: *can agentic, structure-aware vectorless retrieval achieve competitive retrieval accuracy and grounding vs. conventional dense-vector RAG on long hierarchical legal documents?* MVP ships PDF→tree→agent→grounded answer with citations plus a controlled Tuklas-vs-vector evaluation on 50–100 questions. Success is a working artifact **and** a completed, honestly reported measurement — not a win over the baseline.

## 2. Problem Statement

### Who has this problem?

- Law students, legal researchers, academic/government researchers working inside long hierarchical statutes (books → titles → chapters → articles).
- RAG researchers/engineers evaluating retrieval architectures for structured documents.

### What is the problem?

Conventional chunk→embed→vector-DB RAG flattens hierarchy at chunking time. Parent/child, sibling-provision, and cross-reference relationships (e.g. Book IV → Title II → Chapter 1 → Articles 1318/1319/1320) **can be lost or degraded** unless explicitly preserved — most flat pipelines do not preserve them, and what is lost at chunk time is unavailable at query time.

### Why is it painful?

- Researchers must manually chase context (neighboring articles, exceptions, cross-references) that a flat top-K misses.
- Citations degrade to chunk offsets instead of stable legal locations (article/section/page).
- Architecture comparisons are anecdotal without a controlled vectorless-vs-vector harness on the same dataset.

### Evidence

- 🔶 **Assumption:** hierarchy loss materially hurts legal QA. No Tuklas-local measurement exists yet — validating this is the job of `docs/evaluation.md`. Do not assert it as fact in UI/docs.

## 3. Target Users & Personas

### Primary — Legal Researcher Rosa

- Role: law student / legal researcher. Reads 100+ page statutes.
- Goals: find the exact provision(s) answering a question; cite article + page.
- Pain: Ctrl-F across PDFs; flat chatbot answers without verifiable citations.
- JTBD: "When I ask a doctrinal question, show me the provisions and their neighbours so I can verify the answer myself."

### Secondary — RAG Engineer Eli

- Role: AI engineer evaluating retrieval architectures.
- Goals: reproduce the tree index, swap scoring weights, run the vector baseline, read Recall@K/MRR/faithfulness.
- Pain: demos without measurements; tangled agent+retrieval code where the contribution is unclear.
- JTBD: "Show me exactly what LangGraph does vs. what your retrieval engine does, with code I can ablate."

### Explicitly not a user

- Anyone seeking legal advice or autonomous legal decisions. Tuklas is a **research/discovery artifact**, not an AI lawyer. UI must carry the disclaimer in §7 (US-2).

## 4. Strategic Context

- **Goal:** one focused research-engineering artifact (7-day MVP), not a SaaS. No billing, accounts, RBAC, collaboration, case management, cloud storage, mobile.
- **Working hypotheses (to be validated, not asserted):** current local LLMs (Ollama/llama.cpp/vLLM) are sufficient for grounded QA on this task — validated by generation metrics in `evaluation.md`; lexical+structural retrieval is a reasonable alternative to dense RAG for hierarchical law — validated by the controlled comparison.
- **Hypothesized contribution:** the **custom hierarchical/vectorless retrieval engine**; LangGraph is plumbing (see ADR-001). Whether the hypothesis holds is decided by the benchmark, not by this PRD.
- 🔵 **Open Question:** which public/legal-safe document(s) anchor the benchmark? Real private uploads must never enter the benchmark set.

## 5. Solution Overview

Seven features, one happy path:

```text
Upload PDF → Parse + Structure → Document Tree → Ask →
Search / Inspect / Expand (agent loop) → Evidence →
Local LLM → Grounded answer + page/section citations
```

| ID | Feature | MVP scope |
|----|---------|-----------|
| F1 | Local PDF ingestion (PyMuPDF → pages → text+metadata) | PDF only. No DOCX/OCR. Limits: ≤500 pages, ≤100 MB, text layer required. |
| F2 | Structural document index (tree: Book→Title→Chapter→Section→Article) | Regex/structure parser; node = id, document_id, parent_id, type, title, text, page_start/end, depth, position, references. Ambiguous layouts get a flagged flat fallback, never a fake deep tree. |
| F3 | Tuklas Agent (LangGraph loop: plan → search → inspect → expand → sufficiency check) | 7 tools only (see retrieval.md). Backtrack + terminate required; max 12 iterations. |
| F4 | Evidence/provenance (every node keeps page + hierarchy path) | Citation click → source node/page. |
| F5 | Grounded generation (question + evidence + hierarchy + citation metadata → local LLM) | Refusal trigger in §7 US-2; advice-phrase blocklist; no unsupported claims. |
| F6 | Document explorer (3-pane: tree / source / answer) | Next.js + SSE progress. Citation click opens source. Disclaimer banner persistent. |
| F7 | Evaluation framework (same dataset → Tuklas arm + vector arm → metrics) | Retrieval + generation + system metrics. Decision rule in §6. |

Out-of-band: `experiments/vector/` holds the **only** dense-embedding code (baseline). Primary path must not import it (enforced by CONSTRAINTS.md).

## 6. Success Metrics

Primary readout: **Recall@5** (retrieval) + **faithfulness** (generation), Tuklas vs. vector on the frozen test split. MRR@10 secondary. Full metric definitions, judge blinding, and split methodology live in `evaluation.md`; this section states the decision rule so "competitive" is testable:

- **MVP success (shippable):** full test-set run completes for both arms; per-category numbers published; system stats reported; no superiority language beyond what the numbers support.
- **"Competitive" (interpretation rule, provisional):** Tuklas Recall@5 ≥ vector − 0.10 **and** faithfulness ≥ vector − 0.10, with no single category gap worse than −0.20. Margins are placeholders recorded in the eval config; tighten only with a new run id.
- **Correctness scorer:** case-folded, punctuation-stripped exact substring **or** RapidFuzz `token_set_ratio` ≥ 85.
- **Judge protocol:** fixed versioned prompt, arm-blinded, sentence-level factual claims, 20% spot-check (min 10 answers) by the builder; disagreement > 15% invalidates the judge run.
- K values reported: 1/3/5/10. Categories (defined in `evaluation.md`): direct-lookup, definition, multi-section, cross-reference, exception, multi-hop, comparison.

### Guardrails / system (report, don't optimize into dishonesty)

- Retrieval latency, generation latency, total latency (p50/p95), tokens in/out, tool iterations per ask.
- 🔶 **Assumption:** no latency budget is promised until the first benchmark run sets the ratchet in CONSTRAINTS.md.

## 7. User Stories & Requirements

### Epic hypothesis

We believe a structure-navigating agent over a document tree will give researchers verifiable, citable answers on hierarchical law with competitive grounding vs. flat vector RAG — **because** hierarchy and references may carry signal flat chunks discard. The "because" is the hypothesis under test; the benchmark confirms or rejects it.

### Stories (MVP)

**US-1 — Upload a PDF and get a navigable tree**
As Rosa, I want to upload a PDF and browse its Book/Title/Chapter/Article tree so I can orient myself.
- [ ] PDF ≤ 500 pages / ≤ 100 MB ingests; pages + text + page numbers retained. Larger → 422, corrupt/non-PDF → 422, identical hash → 409.
- [ ] Tree renders with depth/position; every leaf carries page_start/end; flat fallback carries `parse_confidence < 0.5` + UI badge.
- [ ] Legal-safety: scanned/empty-text PDFs rejected with `OCR_REQUIRED`, never an empty tree.
- Verify: `pytest -k ingest` fixture on 2 public statutes asserts page_count, root type, child_count > 0, 100% leaves with page spans, fallback flag behavior on a flat fixture.

**US-2 — Ask and get a cited answer (or an explicit refusal)**
As Rosa, I want to ask a question and get an answer where every legal claim has a citation so I can verify it.
- [ ] Answer renders `[1]`-style citations; click opens source node/page. `insufficient: false` on success.
- [ ] Refusal trigger: zero kept evidence nodes **or** best kept score < 0.15 (gate threshold, `architecture.md §4`) → canned "retrieved evidence is insufficient…" + `citations: []` + `insufficient: true`. Never a hallucinated answer.
- [ ] Disclaimer (persistent answer-pane banner): "Tuklas is a research discovery tool, not a lawyer. Verify provisions before relying on them." Prompt + post-generation blocklist rejects "you should" / "I advise" / "your case" / "you must" phrasing (single oracle, shared with `architecture.md §6`).
- Verify: 5 gold questions; LLM-judge (blinded) confirms 100% of factual claims cited; builder spot-checks all 5.

**US-3 — Agent visibly searches, inspects, expands, and abandons dead ends**
As Eli, I want to see the agent's search→inspect→expand steps streamed so I can audit retrieval.
- [ ] `POST /ask/stream` (SSE) emits plan, tool calls, evidence set, then tokens (order pinned in `api.md`).
- [ ] Backtrack = a visited branch whose tool_result is marked `relevant: false` and contributes zero evidence nodes; the trace exposes the abandoned branch id.
- Verify: multi-hop fixture trace shows ≥1 abandoned branch **and** ≥2 inspect/expand calls before the answer.

**US-4 — Run the controlled comparison**
As Eli, I want `POST /evaluations` to run both arms on the same dataset and return Recall@K/Precision@K/MRR@10 + faithfulness + latency/tokens.
- [ ] Same question set feeds both arms; gold_nodes fixed per question; identical generation model (ADR-004); seeds/temperatures/versions pinned in run config.
- [ ] Results persist to `evaluation_runs` + per-question `retrieval_runs`; re-run reproduces retrieval metrics exactly and generation metrics within ±5pp.
- Verify: 10-question smoke subset (validity gate: completes, all rows have node ids, judge scores present) then full dev/test run locally.

**US-5 — Keep the vector baseline quarantined**
As Eli, I want a guarantee the primary path is vectorless so the research claim is credible.
- [ ] `apps/api/{ingestion,retrieval,agent,generation}/` import no entry on the deny list in `architecture.md §2` (exact dotted paths, CI-enforced).
- [ ] Dense-embedding code lives only under `experiments/vector/`, imported only by `evaluation/`.
- Verify: quarantine scan green in CI (see CONSTRAINTS.md).

### Edge cases & constraints

- Scanned PDFs: a page with < 50 extractable chars counts as empty; a doc with > 30% empty pages → 422 `OCR_REQUIRED` with a clear message. No silent empty tree.
- Structurally ambiguous docs: parser emits flat fallback (document → page nodes) with `parse_confidence < 0.5`, surfaced in `GET /documents/{id}` and as a UI badge — never a confidently wrong deep tree.
- Identifier variants normalized before matching: Art./Arts./Article/§/Sec./Section/Ch./Chapter/Title/Book + ranges ("1318–1320"). Full rules in `retrieval.md §4`.
- Also handled: encrypted PDFs (422), >500-page/>100MB uploads (422), non-hierarchical docs (flat fallback), tables/footnotes (kept as node text, no special parsing in v1), cross-document questions (out of scope — one document per ask in v1).
- Local-first: no document bytes leave the machine; benchmark uses public/legal-safe docs only.

## 8. Out of Scope

Billing, SaaS accounts, enterprise RBAC, collaborative editing, case management, cloud document storage, mobile app, legal advice, autonomous legal decisions, DOCX/OCR ingestion, Postgres/vector-DB in v1, multi-document questions — each adds surface without answering the research question. Cut explicitly; revisit only with a new ADR.

## 9. Dependencies & Risks

| Risk | Impact | Mitigation |
|------|--------|------------|
| Structure parser brittle on real PDFs | High — wrong tree poisons everything | Page-fallback + confidence flag; benchmark on 2–3 known docs first; regex + RapidFuzz, no ML layout in v1 |
| Local LLM quality/latency varies by hardware | Med — answers slow or weak | Interchangeable LLM interface (ADR-004); default small model; tokens/latency per run; identical model across arms |
| Benchmark too easy (direct lookups only) | High — vacuous "competitive" claim | All 7 categories non-empty (≥3 dev Qs each) before any reported comparison |
| Agent loops / blows token budget | Med | 12-iteration cap + sufficiency gate + per-tool output caps (architecture.md §4); retry consumes budget |
| Legal-safety / advice framing | High | Disclaimer + refusal trigger + output blocklist + advice-leakage planted Qs in eval |

## 10. Open Questions

1. 🔵 Benchmark corpus: which public statute(s) + who writes the 50–100 gold questions?
2. 🔵 Scoring weights start uniform — who owns the dev-split tuning run before test numbers count?
3. 🔵 Default local model + min hardware for MVP demo?
4. 🔵 SSE v1 schema frozen in `api.md` — web client builds against it now, or one iteration allowed?

**Next step:** review this PRD + architecture.md + ADRs. Only then plan Day 1–7 slices.
