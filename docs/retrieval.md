# Tuklas Agent — Retrieval Engine Contract (Vectorless, rev 2)

**Status:** Draft for review (rev 2 — post adversarial review). Implements PRD F2/F3/F4. Read with `architecture.md §4–5`.
**Rule:** this path uses zero dense embeddings. Package deny list in `architecture.md §2` is binding; the word "embedding" appears in this file only in this rule.

## 1. What "vectorless" means here (testable)

1. No dense vector, no similarity-over-embeddings, no ANN index in the Tuklas arm.
2. Allowed signals: BM25, exact/fuzzy phrase, keyword, title/heading match, tree position (depth/position/parent-child/sibling), article-identifier match, explicit cross-reference walks.
3. Allowed libraries: `rank-bm25` (or SQLite FTS5 BM25), RapidFuzz (string similarity only), regex. Deny list: `sentence_transformers, faiss, chromadb, qdrant_client, pgvector` (+ `cosine_similarity` grep rule) — CI-scanned.
4. The dense baseline lives **only** in `experiments/vector/` and is callable only from `evaluation/`.

## 2. Index structures

- **Tree store (SQLite):** `nodes` + `references` per `architecture.md §8`. Indexes: `(document_id, parent_id)`, `(document_id, type)`, FTS5 on `(title, text)`.
- **BM25 index:** per-document, over leaf-node bodies + section-intro nodes. Rebuilt on ingest; persisted under `data/indexes/{document_id}/bm25.json`. The query-time lex path scores through this persisted per-document rank-bm25 model (`bm25.json`), so lex is a pure function of (this document's corpus, query); SQLite FTS5 serves only as the candidate MATCH prefilter (its bm25() stats are table-global and are never the score source).
- **Identifier index:** normalized article/section labels → node ids. Normalizer §4.
- **Reference graph:** `references` edges from regex + RapidFuzz title fallback; unresolved targets stored with `target_node_id NULL`.

## 3. Scoring — tunable defaults, disjoint components, two-pass order

Per candidate node `n` for query `q`:

```text
score(n, q) = w_lex*lex + w_ident*ident + w_title*title + w_ref*ref + w_hier*hier
```

**Disjoint inputs (no triple-counting):** `lex` sees body text only (titles stripped before BM25); `title` sees title/heading only; `ident` sees identifier tokens only (removed from both other inputs). A heading term fires in exactly one component.

- `lex`: BM25 over body, min-max normalized per query → [0,1].
- `ident`: 1.0 exact normalized article id, 0.5 chapter/title-level id, else 0. No depth prior, no classifier — v1 has no query classifier; lookup-vs-comparison handling is future work, not a silent keyword set.
- `title`: heading token overlap (Jaccard) → [0,1].
- `ref`: 1.0 if reachable within 2 hops from a pass-1 lex-top-5 node, else 0.
- `hier`: 1.0 if sharing a parent with a pass-1 lex-top-3 node, else 0 (captures Art. 1318/1319/1320 clustering).
- **Two-pass order:** pass 1 computes lex/ident/title and fixes lex-top-5/top-3 sets; pass 2 computes ref/hier against those frozen sets. With all-zero lex (below-threshold case), ref/hier = 0 and the broaden-retry fires.

**Identifier precedence (ORDER, not a weight — ADR-005).** The five components are
bounded and SUMMED, so no single one can outrank the sum of the others: `ident`
(capped at 1.0) lost to a competitor that accumulated `lex 1.0 + title 0.1 +
hier 1.0 = 2.1`, and a query naming a provision lost that provision from the
top-8 evidence window. The node that UNIQUELY OWNS an exact label the query names
therefore sorts FIRST; every other node keeps the §3 score order. The score
VALUE, the five components and the formula above are unchanged: no new weight
and no tuned constant. A container label (chapter/title/book) never identifies,
because container numbers repeat ("CHAPTER 2" occurs 34 times in the Civil Code),
and an exact label owned by more than one node identifies none of them.

`scoring.evidence_rank_key` is the single source of this order. The retrieval
ranking, the evidence-window cut, the context builder's prompt order and the
agent's merged evidence set all use it, so the provision the user named is the
first thing the model reads. The `ident` component's VALUE is set by §4's
own-identity rule and the ident index's `level` column.

**Defaults (starting points, NOT optima):** all weights 1.0; phrase/fuzzy bonuses off by default and enabled only via tuning. Tunable ranges: weights ∈ [0,2], fuzzy threshold ∈ [80,95]. Tuning procedure: grid/random search on the **dev split only**; winning config frozen into the eval run record; test split touched once. Any number reported without this record is invalid.

**Stoplist for broaden-retry:** SQLite FTS5 default English stopwords + `{said, provision, provides, article, section}` — versioned in code, not ad hoc.

## 4. Identifier normalization (required)

Lowercase → strip punctuation → canonical map: `article|art\.?|arts\.? → art`, `section|sec\.? → sec`, `chapter|ch\.? → ch`, `subchapters?|subch\.? → subch`, `title → title`, `book → book`; digits preserved; ranges expanded (`1318–1320` → 1318,1319,1320); plurals handled. `Art. 1318`, `Article 1318`, `§1318` → `art 1318` and `sec 1318` (a bare § sign bridges to both section label families, so §-printed and word-printed provisions resolve to the same ident component). `subch` is a 0.5 container-level label like `ch`. Query and node labels both normalized before matching.

**Own identity (ADR-005).** A node OWNS the label in its own heading and
INHERITS the labels of its ancestors. Ownership requires all three of:

1. the label word starts the title (so "the provisions of articles 226 to 228"
   owns nothing — it cites, it does not own);
2. the number is terminated by a period or the end of the string;
3. the node type matches the label family (an `article` node may own `art N`, a
   `chapter` node `ch N`).

The ident index records which is which: `level` 1.0 for an owner, 0.5 for an
inheritor. **The `ident` component's VALUE is the label's own level** - 1.0 for
any node carrying an exact label, 0.5 for a container label - exactly as the
§3 bullet above states; the value is a property of the LABEL, not of the node.
**The `level` column decides OWNERSHIP**: only a node at level 1.0 may be the
identified node. Changing the value for an inheritor would change every published
ident and every score, which §3 forbids without a recorded tuning run.
Measured on the Civil Code, ownership is the difference between `art 57` naming
404 nodes and naming 1.

**Coordinated lists (ADR-005).** A list of provisions joined by a comma, "and",
"or" or "to" yields a label for EACH of them: "Compare articles 1318 and 2124"
-> `art 1318`, `art 2124`. The list is bounded at eight numbers, and it must be
a real list - a separator word is never read as a number, and a word that is
not a number ends the list. Without this, only the first provision in the list
reached the identifier channel, and the planner's split left the rest as bare
numbers, which carry no label at all.

## 5. Tool contracts (agent's ONLY tree access — no direct reads)

```python
search_nodes(query: str, document_id: str, top_k: int = 10) -> list[NodeHit]
  # default 10, hard max 20 (binding; resolves the old 10-vs-20 conflict)
inspect_node(node_id: str) -> NodeFull            # single node, ≤8000 chars text
list_children(node_id: str, limit: int = 50) -> list[NodeSummary]
  # max 50 rows, 200-char summaries — flat-fallback safe
get_parent(node_id: str) -> NodeSummary | None    # single node
expand_context(node_id: str, radius: int = 1) -> NodeFull
  # radius ≤ 2, ≤5 nodes, ≤2000 chars/node
follow_reference(reference_id: str) -> FollowResult
  # FollowResult = { status: "resolved"|"unresolved"|"error", node: NodeFull|None }
  # unresolved (NULL target) → status unresolved; agent MUST NOT invent target
get_source(node_id: str) -> Source                # single node text + pages + path
select_evidence(hits: list[NodeHit], sub_questions: list[str]) -> EvidenceSet
  # retrieval-owned: dedup → drop <50 chars → drop page-span-less →
  # rank by score → keep top 8 → per-sub-question support check (threshold 0.15)
  # The <50-char floor and the page-span filter do NOT apply to the identified
  # node: it is the provision the user named, and a short provision is still
  # a provision (ADR-005).
```

`NodeHit { node_id, title, snippet ≤400 chars, score, components{lex,ident,title,ref,hier}, page_start, fallback }`. Every tool logs `(tool, args, latency_ms, result_ids)`.

## 6. Flat-fallback behavior (per-component, binding)

Fallback detected by `parse_confidence < 0.5` (stored on document). Then: `ident/title/hier = 0` (no headings, no parents); `lex` runs over page-node texts; `ref` runs only on resolved edges (usually none → 0). The `fallback` flag is computed in `score_nodes` from `parse_confidence` and carried on every `ScoredNode` (so `search_nodes` results surface it); the Day 3 trace reads it from retrieval rather than recomputing it.

## 7. Failure modes

| Case | Behavior |
|------|----------|
| Best kept score < 0.15 (gate threshold, `architecture.md §4`) | one broaden-retry (stopword-strip + identifier-only query; consumes iteration budget), then `insufficient_evidence` |
| Flat fallback tree | §6 behavior + trace flag; lex-only in effect |
| Unresolved reference | `status: "unresolved"`; agent must move on, not invent |
| Contradictory provisions | include both with paths; generation renders "sources differ" |
| Zero sub-questions from planner | invalid plan; gate fails closed → retry plan once, then insufficient |

## 8. Verification (before any claim counts)

- [x] Golden: 10-question smoke set — every gold_node in top-5 (the reported primary K; top-8 evidence window is a superset, so top-5 pass implies top-8 containment).
- [ ] Weights + normalization snapshot in every `retrieval_runs` row (schema has the columns).
