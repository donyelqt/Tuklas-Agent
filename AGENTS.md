# AGENTS.md

Read this file before touching anything in the repo. It is short on purpose.

## Project

Tuklas Agent is a local-first, vectorless retrieval system for private law
document discovery. It is a pre-implementation research artifact: the
specification suite under `docs/` is complete and the code has not been written.
The research question is the product, and the honest comparison against a dense
vector baseline is the deliverable. Do not write copy, code, or comments that
assume the result.

## Repo layout

```
apps/web/       marketing site at /, Next.js App Router   (exists, only implemented app)
apps/api/       FastAPI service                            (specified in docs/architecture.md, not built)
docs/           PRD, architecture, api, retrieval, evaluation, decisions, landing-page
experiments/    evaluation harness and the dense vector baseline under vector/  (specified, not built)
data/           corpora, splits, run artefacts             (specified, not built)
scripts/        check_no_vectors.py, check_eval_config.py  (specified, not built)
```

## The engineering floor

`CONSTRAINTS.md` at the repo root is binding. Read it before writing code. Do not
weaken it to make a change pass. Tightening the floor is silent; loosening it
requires human approval stated in the commit message.

What surprises people:

- No new suppression comments: `# type: ignore`, `# noqa`, `eslint-disable`, `@ts-ignore`.
- No unimplemented stubs: `raise NotImplementedError`, `...`, `pass`-only handlers, empty `catch {}`.
- No secrets in source. No absolute private paths.
- No skipped or deleted tests without a reason in the commit message.
- Vectorless quarantine: `apps/api/{ingestion,retrieval,agent,generation}/` must not import
  `sentence_transformers`, `faiss`, `chromadb`, `qdrant_client`, `pgvector`, and
  `cosine_similarity` must not appear in `apps/api/retrieval/`. Only
  `apps/api/evaluation/` may import `experiments/vector/`.
- Agent thinness: `apps/api/agent/` accesses nodes only through the seven tools and must
  not import `sqlite3`, `sqlalchemy`, or `apps.api.models`.

## Honesty rules

These stop the worst mistakes. They are product requirements, not style
preferences.

- No superiority claims. Never write outperforms, beats, better than, superior, most
  accurate, best. The outcome of the vectorless versus vector comparison is unknown
  and `docs/PRD.md` says so.
- No fabricated metrics. Every retrieval and latency metric in `CONSTRAINTS.md` is
  currently `unmeasured`. No invented Recall@5, MRR, latency, token counts, and no
  fabricated precision such as `99.99%` or `4.1x`.
- No legal-advice phrasing in copy, prompts, or UI. The banned phrases are `you should`,
  `I advise`, `your case`, `you must`. Research tool framing only. The persistent
  disclaimer must render on the page.
- No testimonials, customer logos, user counts, or star ratings. There are no users.
- No invented names. No Acme, no Jane Doe, no placeholder personas presented as real.
- No version labels and no install command implying a build exists. Nothing is released.
- All marketing copy goes in `apps/web/src/lib/copy.ts` and nowhere else. The source of
  truth for that copy is `docs/landing-page/COPY.md`.

## Stack and commands

Next.js 16 App Router, React 19.3, TypeScript strict, Tailwind CSS v4, Geist and
JetBrains Mono via `next/font`. Python for the API side. Run all four of these from
`apps/web`:

```bash
npx tsc --noEmit -p tsconfig.json   # types
npm run lint                        # eslint
npm run build                       # production build
npm run start                       # serve the production build for browser verification
```

## Conventions that are not obvious from the code

- Tailwind v4 takes its theme from CSS. There is no `tailwind.config.js` and there
  must not be one. Tokens live in an `@theme` block in `apps/web/src/app/globals.css`.
  Renaming one emits no error, it silently produces no CSS and breaks every consumer.
- Import icons from `@phosphor-icons/react/ssr`, never the package root. The root barrel
  calls `createContext` at module scope and the build fails during page-data
  collection, because Server Components resolve React to the server build where
  `createContext` does not exist.
- Most components are Server Components. Add `"use client"` only when the component
  genuinely needs an observer, a timer, or clipboard access. Never to work around a
  styling problem.
- All user-visible marketing copy lives in `apps/web/src/lib/copy.ts`. No inline strings
  in JSX. This keeps the copy audit a single-file grep.
- Zero em-dash and zero en-dash characters in any rendered string, project-wide on the
  marketing route. Use the ASCII hyphen.
- Radius 0 and no shadows is the shape system, not an oversight.
- `text-ink-5` is for non-text decoration only. It fails the text contrast threshold and
  is banned on any string a user must read.
- Accent cyan (`accent-2`) is permitted in exactly one component,
  `comparison-arms.tsx`. Never anywhere else.
- Motion animates only `opacity` and `transform`. `window.addEventListener('scroll')` is
  banned; use IntersectionObserver.

## Brand assets

`apps/web/src/components/marketing/backend-marks.tsx` inlines official third-party
logos. They are upstream artwork, never hand-drawn, and each carries a provenance
comment naming its source. If a mark is added or changed, keep that comment accurate.
ADR-004 restricts which local LLM providers may appear on the strip.

## Before you finish

- `npx tsc --noEmit -p tsconfig.json` clean.
- `npm run lint` clean, zero warnings.
- `npm run build` clean.
- The change verified in a real browser, not asserted.
- If the change touches the marketing route, run the mechanical pre-flight in
  `docs/landing-page/DESIGN.md` section 13.