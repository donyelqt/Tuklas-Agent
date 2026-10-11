# Tuklas Agent — Landing Page Design Contract

**Status:** Design contract. Load-bearing. Implementation agents MUST read this file and MUST NOT diverge from it.
**Reference design language:** [omp.sh](https://omp.sh) (oh-my-pi, `can1357/oh-my-pi`), fetched and analysed 2026-10-12. Raw artefacts in `.tmp_refs/omp.html`, `.tmp_refs/omp.css`.
**Governing skills:** `using-agent-skills`, `design-taste-frontend` (anti-slop), `ui-ux-pro-max`.
**Precedence:** `CONSTRAINTS.md` (engineering floor) > `docs/PRD.md` (honesty rules) > this file (visual rules).

---

## 1. Stack decision: Next.js, not Astro, not a bare React SPA

**Decision: one Next.js App Router app at `apps/web`.** The marketing page is `/`. The document explorer (architecture.md §7, F6) is `/app/*`.

### Why not Astro

Astro is the better *pure static marketing site* and it loses here on repo economics, not on capability:

| Factor | Next.js (chosen) | Astro | React + Vite SPA |
|---|---|---|---|
| Frameworks in repo | 1 | 2 | 2 |
| Tailwind configs / design token sources | 1 | 2 (drift risk) | 2 (drift risk) |
| `node_modules`, lockfile, CI jobs, deploy targets | 1 each | 2 each | 2 each |
| Marketing page gets design tokens and components from the app | yes, by construction | copy and paste | copy and paste |
| SSR/SSG and SEO for the marketing page | yes | yes | **no** |
| JS shipped on the marketing page | React runtime | near zero | full SPA runtime |

The decisive factor is the second row. With two Tailwind configs the ink ramp, accent, and radius drift, and drift is a correctness bug in a design system, not a style preference. `architecture.md §7` already binds `apps/web` to Next.js, so choosing Astro also requires an ADR reversing an accepted architecture decision.

Honest counter-argument, recorded so the trade-off is on file: Astro ships roughly 100 to 200 KB less JavaScript on the marketing route and scores slightly better on Core Web Vitals. Next.js closes most of that gap with static rendering and zero client components above the fold. For a solo-builder artifact, avoiding a permanent second framework and a permanent second token source beats a marginal Lighthouse delta.

### Repo layout this decision implies

```
apps/web/                      Next.js App Router, marketing at /, explorer at /app/*
apps/api/                      FastAPI (per architecture.md section 7)
docs/landing-page/             this file, COPY.md, STACK.md
```

### Stack detail

- Next.js 16, App Router, React 19, TypeScript strict.
- Tailwind CSS v4 via `@tailwindcss/postcss`. No `tailwind.config.js`. Theme declared with `@theme` in CSS.
- Fonts self-hosted through `next/font/google`: **Geist** and **JetBrains Mono**. No `<link>` to Google Fonts.
- Icons: `@phosphor-icons/react` only, one family, `weight="regular"` throughout, sized from one scale.
- No shadcn/ui on the marketing route. shadcn/ui is specified in `architecture.md` §7 for the explorer panes; putting it on the marketing route adds Radix and a second button system to a page that needs none.
- No GSAP. Motion is CSS transitions plus one IntersectionObserver reveal. The page has no scroll-hijack and no pinning, so ScrollTrigger earns nothing here.

---

## 2. Design read and dials

**Reading this as:** a developer-facing landing page for a local-first RAG research artifact, aimed at RAG engineers and legal researchers, in a dark terminal-native harness language borrowed from omp.sh, leaning toward Tailwind v4 tokens plus Geist and JetBrains Mono, hairline rules, sharp corners, and a single locked accent.

| Dial | Value | Reason |
|---|---|---|
| `DESIGN_VARIANCE` | **6** | omp.sh is asymmetric but restrained. The audience is engineers who will read the spec. An Awwwards-grade layout would cost credibility, not earn it. |
| `MOTION_INTENSITY` | **5** | One marquee, scroll reveals, copy-button feedback. Nothing pinned, nothing hijacked. |
| `VISUAL_DENSITY` | **5** | Developer tool. Informative, not airy, not a cockpit. |

### Inherited from omp.sh, measured from its compiled stylesheet

- The full `--color-ink-*` ramp, hue 280, near-neutral graphite.
- `border-radius: 0` everywhere. All-sharp is omp.sh's shape system and it is the cheapest possible shape consistency lock.
- `box-shadow: none` on surfaces. Elevation comes from 1px rules and ink-ramp steps only.
- Geist for sans and display, JetBrains Mono for anything technical.
- `--tracking-tight: -0.025em` on display type.
- `--prose-measure: 66ch` on body copy.
- `--rule: oklch(100% 0 0 / .08)` and `--rule-strong: oklch(100% 0 0 / .14)` as the only two divider weights.
- Base transition `150ms cubic-bezier(.4, 0, .2, 1)`.
- Structural rhythm: short kicker, one-sentence H1, two-sentence sub, install command with a copy button, a "compatible" logo strip, a numbered feature list, a one-line footer. The whole omp.sh page is 44 lines of text. Restraint is the signature.

### Rejected

- **`#000000` page background.** omp.sh uses it. `--color-ink-0: #000000` is banned by `design-taste-frontend` §9.A and it visibly smears on OLED panels during scroll. Shifted to `#050507`, one step off pure black, which preserves the exact relationship to `--color-ink-1: #0a0a0c`.
- **omp.sh's em-dash habit.** `design-taste-frontend` §9.G bans the character outright, page-wide, with no allowance. Zero `—` and zero `–` in any rendered string.
- **omp.sh's version kicker** (`omp², soon!`). Version and launch-status labels in the hero are banned by default, and this project is pre-implementation, so a hero kicker would advertise a build that does not exist.

---

## 3. Color tokens

Values verified by reading `.tmp_refs/omp.css` and by computing WCAG contrast against the ink ramp. Hex values are computed fallbacks for the `oklch()` primaries; both are declared so the page renders correctly in every engine.

```css
@theme {
  /* ink ramp, cool graphite, hue 280 */
  --color-ink-0: #050507;                        /* page background */
  --color-ink-1: #0a0a0c;                        /* panel, elevated surface */
  --color-ink-2: #101014;                        /* code block, input, inset surface */
  --color-ink-3: #17171c;                        /* hover wash */
  --color-ink-4: #2a2a31;                        /* strong border on dark surfaces */
  --color-ink-5: #626368;                        /* faint text and icons, NON-TEXT ONLY */
  --color-ink-7: #c3c4c7;                        /* secondary body copy */
  --color-ink-9: #f5f5f5;                        /* primary text */

  /* accents */
  --color-accent:   #f84fcc;                     /* oklch(70% .24 340): Tuklas arm and CTA */
  --color-accent-2: #00dbe4;                     /* oklch(81% .14 200): vector baseline ONLY */

  /* semantic */
  --color-rule:        oklch(100% 0 0 / .08);
  --color-rule-strong: oklch(100% 0 0 / .14);
  --color-selection:   oklch(70% .24 340 / .32);
}
```

### Accent lock, with the one documented exception

Magenta `--color-accent` is the single accent for the whole page: primary CTA, focus rings, links, active states.

Cyan `--color-accent-2` is permitted **only** in the comparison section (section 6) and only to mark the quarantined dense-vector baseline arm. That distinction is the product's core claim (`ADR-003`), and encoding it in color makes the evaluation section readable without a paragraph of explanation. It must not appear on a CTA, a link, a focus ring, or anywhere outside that section. This is the documented rule that satisfies the color consistency lock.

### Verified contrast, and what each token may carry

| Foreground | Background | Ratio | Verdict | Permitted use |
|---|---|---|---|---|
| `ink-9` `#f5f5f5` | `ink-0` `#050507` | **19.2:1** | AAA | headings, primary copy, code |
| `ink-7` `#c3c4c7` | `ink-0` `#050507` | **11.7:1** | AAA | secondary copy, captions |
| `accent` `#f84fcc` | `ink-0` `#050507` | **6.9:1** | AA body, AAA large | accent text, icon, rule highlight |
| `accent-2` `#00dbe4` | `ink-0` `#050507` | **12.1:1** | AAA | baseline-arm markers only |
| `ink-0` `#050507` | `accent` `#f84fcc` | **6.9:1** | AA body | label inside a magenta CTA |
| `ink-5` `#626368` | `ink-0` `#050507` | **3.5:1** | **fails AA for text** | NON-TEXT ONLY |

`ink-5` is the one trap in this palette. At 3.5:1 it clears the 3:1 non-text threshold (WCAG 1.4.11) and fails the 4.5:1 text threshold. It is legal for rules, dividers, disabled-state icons, and decorative marks. It is **banned** for body copy, labels, and any string a user must read. Every readable string uses `ink-7` or `ink-9`. Focus rings use `accent`, which clears 3:1 against `ink-0` at 6.9:1.

### Theme lock

The page is dark, single theme, for its full length. No section inverts to a light background. Section-level background variation is allowed only as adjacent steps of the same ink ramp (`ink-0` next to `ink-1`). `prefers-color-scheme: light` does not flip this page. A dark-only landing page is an intentional brand decision matching the harness reference, not an oversight, and it is recorded here so a future maintainer does not "fix" it.

---

## 4. Typography

| Role | Family | Weight | Tracking | Notes |
|---|---|---|---|---|
| H1 hero | Geist | 600 | `-0.025em` | 2 lines max on desktop, `clamp()` sized |
| Section H2 | Geist | 600 | `-0.025em` | max 8 words |
| Body | Geist | 400 | normal | `66ch` measure, `1.625` leading |
| Lead paragraph | Geist | 400 | normal | `ink-7`, larger than body |
| Kicker, labels, code, metrics | JetBrains Mono | 400, 500 | `+0.02em` on uppercase labels | uppercase labels are `11px` with `0.18em` tracking |
| Buttons | Geist | 500 | `-0.01em` | labels are 1 or 2 words |

No serif anywhere. No italic emphasis words injected into sans headlines. Mono is the only family used for anything that is literally code, a tool signature, a metric, or an identifier.

Type scale, mobile first: `36px / 44px / 56px` for H1 at `sm / md / lg`, capped so the hero still fits `100dvh` with the CTA visible without scrolling.

---

## 5. Shape, spacing, surface

- **Radius: 0.** No `rounded-*` utility anywhere except `rounded-full` on the marquee and on genuine pills (a live-status dot, the copy-confirmation chip). This is the omp.sh shape system and it is a hard lock.
- **Shadows: none.** Surfaces separate with `--color-rule` borders and ink-ramp steps.
- **Spacing base:** `0.25rem` (Tailwind default). Section padding `py-20` mobile, `py-28` desktop. Content width `max-w-6xl` for text sections, `max-w-7xl` for the hero and comparison section.
- **Grain or noise:** if any, `position: fixed; inset: 0; pointer-events: none`. Never on a scrolling container.
- **Z-index scale:** declared once as CSS variables. `base 0`, `nav 40`, `overlay 60`, `grain 70`. No ad-hoc `z-50`.

---

## 6. Motion

| Element | Spec |
|---|---|
| Base transition | `150ms cubic-bezier(.4, 0, .2, 1)` |
| Hover states | opacity, border-color, background-color only. No layout-shifting transform. |
| Pressed state | `translateY(1px)`, no scale |
| Reveal on scroll | IntersectionObserver adds a class: `opacity 0 to 1`, `translateY(12px) to 0`, `420ms`, `cubic-bezier(.16,1,.3,1)`, fires once |
| Marquee | one, `36s` linear infinite, CSS keyframes, duplicated track |
| Copy button | label swaps to "copied" for `1600ms` |
| Backdrop blur | `12px` where glass is used (nav on scroll only) |

Rules:

- **Only `transform` and `opacity` are animated.** Never `top`, `left`, `width`, `height`.
- **`window.addEventListener('scroll')` is banned.** Reveals use IntersectionObserver, the marquee is pure CSS, the nav is `position: sticky`.
- **Reduced motion:** one global `@media (prefers-reduced-motion: reduce)` block sets animation and transition durations to `0.01ms` and forces every `.reveal` element to its final state with no transform. The marquee stops. Nothing becomes unusable.

---

## 7. Section inventory

Eight content sections, eight distinct layout families, zero eyebrows.

| # | Section | Layout family | Eyebrow |
|---|---|---|---|
| 1 | Hero | Asymmetric split: copy left, real component preview right | none |
| 2 | Backends strip | Single infinite marquee of logos | none |
| 3 | The hierarchy problem | Two-column comparison, flat chunk list versus kept tree | none |
| 4 | Pipeline | Numbered divide-list, 7 steps, bold lead-in plus one sentence | none |
| 5 | Retrieval tools | Single code block of the 7 tool signatures | none |
| 6 | The comparison | Two-arm diagram, magenta versus cyan, plus the real dataset facts | none |
| 7 | Guardrails | Two-column divide-list, with the disclaimer on its own full-width band | none |
| 8 | Run it locally | Install command block plus repo tree | none |

Constraints on the set:

- **Exactly one marquee on the page** (section 2). No second kinetic strip.
- **No section uses three equal cards.** Section 3 is a two-column comparison, section 4 is a numbered divide-list, section 7 is a divide-list. None is a three-across card row.
- **No section repeats another section's layout family.** All eight differ.
- **The logo strip is logos only.** No category label printed under any logo.
- The hero carries at most three text elements: headline, subhead, CTAs. No kicker, no trust strip, no tagline under the buttons.

### Hero geometry, non-negotiable

- `min-h-[100dvh]`, never `h-screen` (iOS address bar).
- Top padding capped at `pt-24` desktop.
- Headline max 2 lines at desktop. Subhead max 20 words.
- Both CTAs visible without scrolling at `1440x900` and at `390x844`.
- One primary and one secondary CTA. CTA intent lock: **"Read the spec"** is the only read-docs intent anywhere on the page; **"View on GitHub"** is the only repository intent. Reused verbatim in nav, hero, and footer. No "Get started", no "Learn more", no "Contact".

### Hero visual

The right-hand visual is a **real component preview**: a miniature of the 3-pane explorer rendering actual `GET /v1/documents/{id}/tree` response shapes from `docs/api.md` §2. It is built from real components with real data shapes, not a `<div>` mock of a screenshot. It shows a Book/Title/Chapter/Article path with page numbers, because the structural tree is the product. No fake version footer, no fake sync status, no fake "last updated".

---

## 8. Honesty rules, binding on all copy

From `docs/PRD.md` §2, §4, §6 and `docs/evaluation.md` §7. These are product requirements, not style preferences.

1. **No superiority claim.** No "outperforms", "beats", "better than", "superior", "most accurate", "best". The PRD calls the outcome of the comparison unknown, so the page must read that way.
2. **No fabricated metrics.** No invented Recall@5, MRR, latency, or token numbers. Nothing that looks like a benchmark result may appear unless it is a real measurement, and no real measurement exists yet (`CONSTRAINTS.md` "Measured, not yet enforced" is entirely `unmeasured`).
3. **No unverified performance claim.** `architecture.md` §6 states latency budgets are not set until the first benchmark run. The page must not promise speed.
4. **No legal-advice framing.** `CONSTRAINTS.md` bans the advice-phrase set in prompts and UI: "you should", "I advise", "your case", "you must". The persistent product disclaimer must appear on the page: *Tuklas is a research discovery tool, not a lawyer. Verify provisions before relying on them.*
5. **No fake social proof.** No testimonials, no customer logos, no user counts, no star ratings. There are no users.
6. **No fake install command.** Nothing is released. The run-locally section shows the repository and the documented local commands, and states the current build state plainly rather than implying a download exists.
7. **No invented names.** No "Acme", no "Jane Doe", no placeholder personas presented as real users.
8. **No version label in the hero.**

---

## 9. Banned patterns

From `design-taste-frontend` §9, §9.F, §9.G, plus §4.7. Mechanical pre-flight.

- Zero em-dashes `—` and zero en-dashes `–` in any rendered string. Only the ASCII hyphen `-`.
- No AI-purple or neon gradient. No `text-shadow` glow on text. No outer glow on cards. One tinted inner highlight is permitted on glass panels only.
- No pure `#000000`. No pure `#FFFFFF` for text surfaces (`ink-9` is `#f5f5f5`).
- No scroll cue. No `Scroll`, no arrow, no animated wheel.
- No decoration text strip at the hero bottom. No `TYPE / FORM / MOTION`.
- No section-number eyebrows (`00 / INDEX`, `01 / CAPABILITIES`). The pipeline section's `01` through `07` are step numbers attached to step content, which is permitted. They are not eyebrow labels above headlines.
- No generic step labels (`Stage 1`, `Phase 01`, `Step 1`). Steps are named with the verb: *Ingest, Parse, Index, Plan, Retrieve, Cite, Abstain*.
- No decorative status dots. The single permitted dot is a real semantic state indicator in the hero preview when it genuinely represents a streaming state.
- No pills or labels overlaid on images.
- No photo-credit captions as decoration.
- No locale, time, or weather strips.
- No testimonials at all: there are none to show.
- No fabricated precision (`99.99%`, `4.1x`).
- No filler verbs: elevate, seamless, unleash, next-gen, revolutionize.
- No "Acme", "Nexus", "SmartFlow".
- No Inter as the body font.
- No hand-rolled SVG icon paths. Phosphor only, one family, one stroke weight.
- No div-based fake screenshots.
- No `border-t` plus `border-b` on every row of a list. Pick one divider style per list.

---

## 10. Accessibility gate

- WCAG 2.2 AA minimum. AAA for body copy where achievable.
- `ink-5` is banned for readable strings. It is the only token in the palette that cannot carry text.
- Visible focus on every interactive control: `outline: 2px solid var(--color-accent); outline-offset: 2px`. Never removed.
- The sticky nav must not obscure keyboard focus. `scroll-margin-top` on every section anchor equals nav height plus 16px.
- The marquee pauses on hover and on focus-within.
- Every SVG icon is either `aria-hidden="true"` when decorative beside visible text, or carries an accessible name when it is the only content of a control.
- The copy button announces state through a polite live region.
- Semantic landmarks: one `h1`, plus `header`, `nav`, `main`, `footer`. Heading order never skips a level.
- Light mode is not offered (see §3). `color-scheme: dark` is declared so form controls and scrollbars render correctly.
- Target size: interactive controls at least 44px on the smallest breakpoint.
- No content hidden behind a fixed element at any scroll position.

---

## 11. Core Web Vitals

| Metric | Target | How |
|---|---|---|
| LCP | under 2.0s | Hero renders as static HTML. Fonts via `next/font` with `display: swap`. No hero bitmap to download: the hero visual is DOM, not an image. |
| INP | under 200ms | Zero JavaScript above the fold except the copy button and the reveal observer. No GSAP, no scroll handlers, no third-party scripts. |
| CLS | 0 | Fonts loaded via `next/font` with size-adjust metrics. Marquee and code block have explicit reserved dimensions. Reveal animates opacity and transform only. |

---

## 12. Implementation file layout

Everything below lives under `apps/web`. Paths are exact.

```
apps/web/
  src/app/
    layout.tsx                 fonts, metadata, theme lock, skip link
    page.tsx                   marketing route, composes the 8 sections
    globals.css                @theme tokens, base layer, reduced-motion block
  src/components/marketing/
    site-nav.tsx
    site-footer.tsx
    hero.tsx
    tree-preview.tsx           real component preview of the explorer
    backend-marquee.tsx
    hierarchy-problem.tsx
    pipeline.tsx
    tool-signatures.tsx
    comparison-arms.tsx
    guardrails.tsx
    run-locally.tsx
    copy-command.tsx           shared command block with copy button
    reveal.tsx                 IntersectionObserver wrapper
  src/lib/
    copy.ts                    every visible string lives here, no inline strings in JSX
    site.ts                    nav items, links, repo metadata
```

`src/lib/copy.ts` is mandatory. Every user-visible string in the marketing route comes from that one file. This makes the copy self-audit mechanical and keeps the em-dash ban enforceable by a single grep over one file.

---

## 13. Pre-flight gate, mechanical

The landing page is done when every line below is verifiable, not asserted.

```
[ ] Zero em-dash and zero en-dash characters in apps/web/src (grep the whole tree)
[ ] Zero rounded-* except rounded-full on the marquee and true pills
[ ] Zero shadow-lg, shadow-xl, shadow-2xl, or drop-shadow in marketing components
[ ] Zero #000 and zero #fff hex literals; all colour via theme tokens
[ ] ink-5 appears nowhere in a readable-string context
[ ] Every interactive element has a visible focus ring
[ ] Reduced-motion block present and proven by toggling the media query
[ ] Every image and SVG has alt text or an explicit aria-hidden
[ ] No string in copy.ts asserts superiority or reports an unmeasured metric
[ ] Disclaimer string present in copy.ts and rendered on the page
[ ] tsc --noEmit clean, next lint clean, next build clean
[ ] Lighthouse on the built route meets or beats section 11
[ ] Verified in a real browser at 390px, 768px, 1440px, and 1920px
[ ] The marquee is the only marquee
[ ] No three-equal-card row anywhere
[ ] All 8 sections use 8 different layout families
```