# apps/web

Next.js App Router application for the Tuklas Agent marketing site and the
document explorer. Two routes: `/` is the landing page, and `/app/*` is the
document explorer described in `docs/architecture.md` §7 (PRD feature F6),
whose components live in `src/components/explorer/`.

## Design contract

Every visual decision on this route traces to `docs/landing-page/DESIGN.md`. Read
it before changing anything here. The short version:

- **Theme** is declared in `src/app/globals.css` inside an `@theme` block. There
  is no `tailwind.config.js`; Tailwind v4 reads its theme from CSS. Token names
  are load-bearing. Renaming `--color-ink-4` silently breaks every consumer.
- **Shape** is radius 0 and no shadows, matching the omp.sh reference. Surfaces
  separate with 1px rules and ink-ramp steps.
- **Accent** is `--color-accent` (magenta) page-wide. `--color-accent-2` (cyan)
  is permitted in exactly two places: the dense-vector baseline arm in
  `comparison-arms.tsx`, and the hero dot field in `dot-wave.tsx`
  (`hero-backdrop.tsx` reads the same token and counts as `dot-wave.tsx`'s
  exception). That is a documented exception, not an oversight.
- **Copy** all lives in `src/lib/copy.ts`. No inline user-facing strings in JSX.
  This keeps the copy audit a single-file grep, including the project-wide ban on
  em-dashes and on unmeasured performance claims.
- **Honesty** rules in `DESIGN.md` §8 are product requirements, not style
  preferences. No superiority claim, no fabricated metric, no legal-advice
  phrasing, no fake social proof. The benchmark has been run and its reports
  are published in `data/eval-reports/`; the comparison's outcome is
  undecided, and the reports publish both arms' numbers with no verdict.

## Commands

```bash
npm run dev     # development server
npm run build   # production build
npm run start   # serve the production build
npm run lint    # eslint
npx tsc --noEmit
```

`npm run start` runs `scripts/serve-export.mjs`, not `next start`. The build
uses `output: "export"`, so `next start` refuses to run and says so; the export
is a directory of files, and `serve-export.mjs` serves it without a framework.
It also maps the extensionless `/app` route onto the exported `app.html`, which
is what a browser asks for.

## Server Components

Almost every file here is a Server Component. Only `reveal.tsx` and
`copy-command.tsx` carry `'use client'`, because only they need an observer, a
timer, or clipboard access. Do not add `'use client'` to a section component to
work around a styling problem.

## Icons

`@phosphor-icons/react/ssr`, never the package root. The root barrel re-exports
the CSR components, which build an `IconContext` with `createContext` at module
scope; that throws during page-data collection because React Server Components
resolve `react` to the server build, where `createContext` does not exist.