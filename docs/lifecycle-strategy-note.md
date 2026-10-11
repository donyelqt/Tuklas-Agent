# Tuklas Agent — Lifecycle & Strategy Note

**Why this file exists:** `product-lifecycle-plays` and `product-strategy-session` were
invoked on this project. Both skills target products that already exist. Tuklas is
pre-launch (Design stage), so this note records the honest diagnosis instead of
forcing a play or a 4-week strategy process the project doesn't need.

## Lifecycle diagnosis (product-lifecycle-plays, Worksheet 1)

**Trigger:** skill invoked mid-spec, not by a business signal.
**Revenue trend:** none — no product, no users, no revenue.

| # | Lever | Ask | Answer | Evidence |
|---|-------|-----|--------|----------|
| 1 | Marketing objective | Is defending share still profitable? | N/A | Nothing to defend |
| 2 | Competition | Are loyalty efforts failing? | N/A | No users |
| 3 | Product | Are legacy support costs unsustainable? | No | No legacy |
| 4 | Promotion | Migration over loyalty? | N/A | Nobody to migrate |
| 5 | Place | Losing money on channels? | No | No distribution |
| 6 | Price | Bundles losing effectiveness? | N/A | No pricing |
| 7 | Data strategy | Predictive → transitional? | No | No data yet |

**Yes count: 0/7. Stage: Design** (pre-Introduce). Per the skill's own scoring,
0–1 yes = "mature and healthy... the question isn't a play" — and here there isn't
even a mature product. No extend / replace / retire play applies. The risk register
(Worksheet 3) is empty by construction: there is no old product to retire and no
successor to launch.

**What survives from the skill:** the hazard list, as pre-mortem prompts for the
future — GTM failure (nobody adopts the benchmark), internal misalignment (agent vs.
retrieval ownership blurring), regulatory framing (legal-advice perception). Each is
already mitigated in `PRD.md §9` and the ADRs. Revisit this diagnosis only when the
MVP has users and a trend to read.

## Strategy session (product-strategy-session, compressed)

A 2–4 week cross-functional strategy process is disproportionate for a solo-builder
research artifact with the strategy already decided (vectorless hierarchical
retrieval, local-first, LangGraph orchestration). Ran compressed, using the final
spec as the input artifact:

- **Positioning:** local-first vectorless RAG for private law discovery; users Rosa
  (legal researcher) + Eli (RAG engineer); differentiated on structure-aware
  retrieval + honest evaluation, not on chatbot breadth. Not a legal-advice system.
- **Problem:** flat chunk→embed RAG discards hierarchy researchers rely on.
- **Opportunities considered:** (a) vectorless agent (chosen — the research
  question), (b) hybrid dense+structure (rejected for v1 — confounds the
  comparison), (c) SaaS/legal-advice product (rejected — out of scope, §14).
- **Prioritization:** vertical slices in dependency order (ingest → structure →
  retrieval → agent → generation → UI → evaluation). No framework needed at this
  size; the dependency graph is the priority order.
- **Roadmap:** Day 1–7 plan in the final spec; execution planning (`tasks/`) starts
  after doc approval.
- **Success metrics:** Recall@5 / MRR / faithfulness / citation accuracy,
  Tuklas-vs-vector on the same dataset (`evaluation.md`).

**Next step (both skills converge here):** review and approve the doc suite
(`PRD.md`, `architecture.md`, `retrieval.md`, `evaluation.md`, `api.md`, ADRs,
`CONSTRAINTS.md`), then execution planning. The full strategy-session template and
lifecycle worksheets remain available when Tuklas has a shipped MVP worth
positioning or a line worth diagnosing.
