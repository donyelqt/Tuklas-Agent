# Tuklas Agent - Landing Page Design Contract

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

### Stack detail

- Next.js 16, App Router, React 19, TypeScript strict.
- Tailwind CSS v4 via `@tailwindcss/postcss`. No `tailwind.config.js`. Theme declared with `@theme` in CSS.
- Fonts self-hosted through `next/font/google`: **Geist** and **JetBrains Mono**. No `<link>` to Google Fonts.
- Icons: `@phosphor-icons/react` only, one family, `weight="regular"` throughout, sized from one scale. No shadcn/ui on the marketing route: it is specified in `architecture.md` §7 for the explorer panes, and adding it here would add Radix and a second button system to a page that needs none.
- **Animation: `motion` `^14.1.0`, imported as `motion/react`.** It drives the hero backdrop only. Everything else on the page stays plain CSS transitions and the one IntersectionObserver reveal. Framer Motion is the same library under its legacy name; the modern package is the one on npm and in `package.json`, so the legacy alias is not installed and must not be imported. No GSAP: there is no scroll-hijack and no pinning, so ScrollTrigger earns nothing here.

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
- Base transition `150ms cubic-bezier(.4, 0, .2, 1)`. Structural rhythm: a short kicker, one-sentence H1, two-sentence sub, an install command with a copy button, a "compatible" logo strip, a numbered feature list, a one-line footer, and 44 lines of text in total. Restraint is the signature.

### Rejected

- **`#000000` page background.** omp.sh uses it. `--color-ink-0: #000000` is banned by `design-taste-frontend` §9.A and it visibly smears on OLED panels during scroll. Shifted to `#050507`, one step off pure black, which preserves the exact relationship to `--color-ink-1: #0a0a0c`.
- **omp.sh's em-dash habit.** `design-taste-frontend` §9.G bans the character outright, page-wide, with no allowance. Zero em-dash and zero en-dash characters in any rendered string. Only the ASCII hyphen.

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

### Accent lock, with two documented exceptions

Magenta `--color-accent` is the single accent for the whole page: primary CTA, focus rings, links, active states.

Cyan `--color-accent-2` is permitted in exactly two components, and nowhere else:

- `comparison-arms.tsx`, on the quarantined dense-vector baseline arm, as a semantic marker inside a labelled two-arm diagram. That distinction is the product's core claim (`ADR-003`), and encoding it in color makes the evaluation section readable without a paragraph of explanation.
- `dot-wave.tsx`, on the trailing band of the hero dot field, behind the magenta crest.

The lock survives a second exception because neither use carries meaning by colour alone. The comparison arm sits in a diagram whose heading names it "Dense vector baseline", so the meaning is carried by the diagram and its text; the wave's cyan is ambient background on a canvas that is `aria-hidden` and conveys nothing. Strip the arm's label and the cyan loses its referent; strip the wave and nothing is lost. Cyan still must not appear on a CTA, a link, a focus ring, or any text a user must read. `hero-backdrop.tsx` inherits the same treatment in its crest layer: same token, same fallback literal, same unreadable ambient background, so it counts as `dot-wave.tsx`'s exception and not as a third one. **The brand mark is not a third accent either:** `brand-mark.tsx` fills its bars with one gradient running across this page's own `accent` to `accent-2` pair, so both endpoints are already locked tokens, and the violet between them exists nowhere else on the page and is not a token.

The backends strip (section 2) carries seven brand marks, all rendered monochrome in the ink ramp via `currentColor`. Ollama's official mark is black and would be invisible on `ink-0`. Brand colour is not used: a brand-coloured mark sitting beside monochrome ones reads as an accident, and vLLM's brand blue would introduce a second accent, which this section locks out.

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
| H1 hero | Geist | 600 | `tracking-tighter` for the hero headline only | 2 lines at desktop, step-sized. The tighter step applies to the hero alone: at the hero's `text-6xl` step the inherited `-0.025em` reads loose. |
| Section H2 | Geist | 600 | `-0.025em` | max 8 words |
| Body | Geist | 400 | normal | `66ch` measure, `1.625` leading |
| Lead paragraph | Geist | 400 | normal | `ink-7`, larger than body |
| Kicker, labels, code, metrics | JetBrains Mono | 400, 500 | `+0.02em` on uppercase labels | uppercase labels are `11px` with `0.18em` tracking |
| Buttons | Geist | 500 | `-0.01em` | labels are 1 or 2 words |

No serif anywhere. No italic emphasis words injected into sans headlines. Mono is the only family used for anything that is literally code, a tool signature, a metric, or an identifier.

Type scale, mobile first: `text-5xl / md:text-6xl` for the H1, discrete steps rather than a `clamp()`. Below `lg` the headline is held to `max-w-[15ch]`; at `lg` it drops the cap (`lg:max-w-none`) and takes the full width of its grid column. There is no third, larger desktop step: once the panel holds `21rem` of the grid, the headline column is too narrow to carry one, and `text-6xl` is the largest step that still sets the headline as two lines. The headline stays left-aligned inside the content column and is never stretched across the section.

The headline is split into two runs inside one `h1`: `hero.headlineLead` in `ink-9` and `hero.headlineAccent` in `accent`. The accessible name is therefore still one sentence. The emphasis rule is satisfied because both runs are the same family at the same weight and differ by colour alone, and no second font family is injected into a sans headline, which is exactly the failure the taste skill warns about.

---

## 5. Shape, spacing, surface

- **Radius: 0.** No `rounded-*` utility anywhere except `rounded-full` on the marquee and on genuine pills (a live-status dot, the copy-confirmation chip). This is the omp.sh shape system and it is a hard lock.
- **Shadows: none.** Surfaces separate with `--color-rule` borders and ink-ramp steps. **Z-index scale:** declared once as CSS variables, `base 0`, `rail 30`, `nav 40`, `overlay 60`, `grain 70`. No ad-hoc `z-50`. The `rail` slot exists so the section rail can never reach the header's layer, and the `grain` slot is reserved and unused: the page has no noise layer, so nothing sits at that layer and any grain added later must be `position: fixed; inset: 0; pointer-events: none`, never on a scrolling container.
- **Spacing base:** `0.25rem` (Tailwind default). Section padding `py-20` mobile, `py-28` desktop. Content width `max-w-6xl` for text sections, `max-w-7xl` for the hero and comparison section.

### Brand mark

`brand-mark.tsx` is the project's own mark, used at exactly two call sites: the nav wordmark and the hero lockup.

- **Construction.** A 64-unit grid (`viewBox="0 0 64 64"`) drawn as four `rect` primitives, not one path, so each bar stays individually reviewable: a root bar at `x=6 y=13` sized `52x11`, a stem at `x=28 y=24` sized `8x10`, and two `17x17` leaves at `x=11` and `x=36`, both at `y=34`. It reads as a document tree reduced to its minimum: a root level, a descent, a branch, and a cited leaf, the right leaf being the provision the product returns. A first pass used five bars including a horizontal spread; at 20px that read as a coloured smudge rather than as a shape, because a 64-unit grid leaves each bar about two pixels wide. Four primitives leave three distinguishable masses at nav size: a bar, a stem, and two blocks.
- **The name is doing the work, and it is not a llama.** `tuklas` is Tagalog for discovery, and the tool discovers provisions inside hierarchical legal documents, so "root to cited leaf" is the argument rather than decoration. A llama silhouette was rejected: the mark file records that two llama marks already appear on this page in the backends strip, and a third would read as a third vendor rather than as the product.
- **Gradient:** three stops, `oklch(0.7 0.24 340)` at offset 0, `oklch(0.62 0.21 295)` at 0.5, `oklch(0.81 0.14 200)` at 1, in `userSpaceOnUse` from `(8,12)` to `(56,52)`. The endpoints are this page's `accent` and `accent-2` values verbatim.
- **Favicon asset set, and the duplication rule.** The favicon is this mark. Four assets sit in `src/app` next to `layout.tsx`: `icon.svg`, the scalable one browsers prefer; `favicon.ico`, a real multi-size icon at 16, 32 and 48 rendered from the same geometry, replacing the framework's default icon, which was the wrong brand entirely; `icon.png` at 512, transparent; and `apple-icon.png` at 180, opaque. The apple icon is opaque on purpose: iOS composites the icon onto the platform's own background, so an opaque field that matches the page background beats depending on the platform. **The geometry is defined once in `brand-mark.tsx` and mirrored in `icon.svg`, and that duplication is a constraint of how favicons load, not an oversight, so nobody "fixes" it by importing from the component:** a favicon is fetched outside the document and cannot reach the page's module graph, its CSS custom properties, or its font stack. For the same reason `icon.svg` uses hex gradient stops, `#f84fcc`, `#9362f4` and `#00dbe4`, instead of the page's oklch values: a favicon is rasterised by the browser's SVG engine on an unknown baseline where oklch support is not guaranteed. The hex values are the computed equivalents of the three oklch stops, and the file's own comment records that mapping.
- **Accessibility and sizing:** `aria-hidden="true"` and `focusable="false"`, so the mark contributes nothing to any accessible name and the wordmark text carries the name at both call sites. Both call sites pass `size={20}`, so 20px rendered against the component's 22px default. `site.shortName` in the nav is the single permitted exception to the `copy.ts` rule; the hero lockup reads `hero.wordmark` from `copy.ts`, and puts the byline in `ink-7` behind it. The gradient id is per-instance via `useId`, so the two copies cannot collide. The hero lockup's relation to the reference design language's tab row is recorded in §7.

---

## 6. Motion

| Element | Spec |
|---|---|
| Base transition | `150ms cubic-bezier(.4, 0, .2, 1)` |
| Hover states | opacity, border-color, background-color only. No layout-shifting transform. |
| Pressed state | `translateY(1px)`, no scale |
| Reveal on scroll | IntersectionObserver adds a class: `opacity 0 to 1`, `translateY(12px) to 0`, `420ms`, `cubic-bezier(.16,1,.3,1)`, fires once |
| Marquee | one, `48s` linear infinite, CSS keyframes, seven duplicated groups, plus a real pause button |
| Hero backdrop drift | Motion on the static 2000-dot field's wrapper: `x -1.6%`, `y -1.2%`, `scale 1.04`, `duration 22s`, `repeat: Infinity`, `repeatType: "mirror"`, `ease: "easeInOut"`. The field itself draws once on mount and once per resize, debounced into a single `requestAnimationFrame`, and is never redrawn per frame. Transform only, so the drift runs on the compositor. |
| Hero backdrop crest | A second canvas of roughly 140 dots over the crest band. Redrawn on a wall-clock throttle of `1000 / 24` ms, so at most 24 redraws a second regardless of refresh rate. Shape comes from elapsed time: a `9s` breathe cycle and a `5s` travelling brightness pass, never from a frame counter, so a 120Hz and a 60Hz display show the same wave. |
| Copy button | label swaps to "copied" for `1600ms` |
| Backdrop blur | `12px` where glass is used (nav on scroll only) |

Rules:
- **Only `transform` and `opacity` are animated.** Never `top`, `left`, `width`, `height`. **`window.addEventListener('scroll')` is banned:** reveals use IntersectionObserver, the marquee is pure CSS, the nav is `position: sticky`.
- **Reduced motion:** one global `@media (prefers-reduced-motion: reduce)` block sets animation and transition durations to `0.01ms` and forces every `.reveal` element to its final state with no transform. The marquee stops. Nothing becomes unusable.

---

## 7. Section inventory

Eight content sections, eight distinct layout families, zero eyebrows.

| # | Section | Layout family | Eyebrow |
|---|---|---|---|
| 1 | Hero | Two-column grid from `lg` up, one column below: brand lockup label, headline with an accent run, subhead, CTA row and command artifact in the left cell, the real tree preview in the right cell, over the animated backdrop | none |
| - | Hero internals (not a section) | Backdrop, then one grid container whose two cells are the content column and the tree preview panel; the panel cell is `hidden` below `lg`. The numbered rail is no longer here: it is fixed to the viewport and owned by `page.tsx` | none |
| 2 | Backends strip | Single infinite marquee of logos | none |
| 3 | The hierarchy problem | Two-column comparison, a flat chunk list against the structure the tree keeps, plus the closing note | none |
| 4 | Pipeline | Numbered divide-list, 7 steps, bold lead-in plus one sentence | none |
| 5 | Retrieval tools | Single code block of the 7 tool signatures | none |
| 6 | The comparison | Two-arm diagram, magenta versus cyan, plus the real dataset facts | none |
| 7 | Guardrails | Two-column divide-list, with the disclaimer on its own full-width band | none |
| 8 | Run it locally | Install command block plus repo tree | none |

Constraints on the set:

- **Exactly one marquee on the page** (section 2), and no second kinetic strip. **No section uses three equal cards:** section 3 is a two-column comparison, section 4 is a numbered divide-list, section 7 is a divide-list. **No section repeats another section's layout family.** All eight differ.
- **The logo strip is logos only.** No category label printed under any individual logo. The strip carries one section-level label naming both kinds of thing, as recorded in the backends strip content model below.
- **Navigation surfaces.** The header carried Problem, Pipeline, Tools, Comparison and Guardrails as links, which duplicated the rail exactly. That list is deleted from the header, in the desktop row and in the mobile panel both. The header keeps the brand lockup and the two CTAs, and the mobile `<details>` disclosure survives because it carries those CTAs to narrow screens. The rail is therefore the page's single section-navigation surface. Consequence, recorded so it is not rediscovered as a bug: below `1024` the rail is `hidden` and no section navigation is rendered at all, so no claim of section navigation at every width may appear in this file.

### Hero geometry, non-negotiable

- The section is `relative`, `overflow-hidden`, `flex flex-col`, `min-h-[100dvh]`, and carries no padding of its own: `pt-24 pb-16 lg:pb-24` sits on the grid container. That container is `mx-auto grid w-full max-w-7xl flex-1 grid-cols-1 items-center gap-10 px-4 sm:px-6 lg:grid-cols-[minmax(0,1fr)_21rem] lg:gap-10 lg:px-8 lg:pb-24`: one column below `lg`, two at `lg` and up, the panel held at `21rem` and the leftover width handed to the content through `minmax(0,1fr)`.
- The hero renders six things across two layers. The backdrop is one layer; everything else is one grid. The content cell is `relative z-10 flex flex-col gap-8 lg:pl-16` and holds five blocks in order: the brand lockup, the `h1`, the subhead, the CTA row, and the command block. No kicker, no trust strip, no tagline under the buttons. The sixth thing, the tree preview panel, occupies its own grid cell.
- The brand lockup is a label, not a widget: a `span` with `flex min-h-11 items-center gap-3 font-mono text-[11px] uppercase tracking-[0.18em]`, holding `brand-mark.tsx` at `size={20}`, the wordmark in `text-ink-9`, and the byline in `text-ink-7` a step back in the ramp. It replaced a row of buttons labelled "Clone" that did nothing when pressed, and it replaced the reference design language's tab row, which switches between real installers this project ships none of: one place fewer mark than the reference, because a tab row here would be a control that does nothing.
- The headline is `max-w-[15ch] text-5xl font-semibold leading-[0.95] tracking-tighter md:text-6xl lg:max-w-none`, 2 lines at desktop. The subhead is capped at `max-w-[52ch]`.
- **Why a grid, and the panel rule.** The panel was an absolutely positioned layer anchored to the viewport edge; measured, it overlapped the headline's box at 1280 and 1440, so a grid makes the collision impossible instead of tuning it away with breakpoints. The panel cell is `relative z-10 hidden lg:block`: it is the second column of `lg:grid-cols-[minmax(0,1fr)_21rem]`, so it can never collide with the headline and never clips its own caption. Below `lg` the hero already carries the headline, both CTAs and the command block, so the panel hides. The panel carries its own `@container` scope and reveals the node id column with `@md:inline`, so the id drops out when the panel itself is too narrow to carry it: the panel is `21rem` wide on a 1440px viewport, where a viewport breakpoint still showed the id and squeezed the node title into an ellipsis.
- The rail is `fixed top-1/2 left-0 z-30 hidden -translate-y-1/2 lg:block` and is rendered by `page.tsx`, so it persists for the whole page instead of scrolling away with the hero. It carries one anchor per section, with the visible two-digit number `aria-hidden` and the label as the accessible name. **Two facts make a viewport-fixed rail safe:** it sits one slot below the sticky header's `z-40`, so it can never paint over the wordmark or the CTAs, and the page content wrapper's single `lg:pl-16` clears its `w-16` for every section at once. That one padding is load-bearing and must not be split into per-section offsets, which are exactly what a future section forgets.
- **The gutter rule.** The gutter is the same 64: the hero's content cell keeps `lg:pl-16`, so the hero's grid still works with a rail that no longer lives inside it. It replaced a `lg:pl-72` wrapper gutter plus an inner `max-w-3xl`, because the grid already reserves the panel's width and reserving another 288px of gutter beside it starved the headline column.
- The command block is the hero's artifact: the slot the reference design language gives to its install command. It is a `git clone` command, **not** `curl | sh`, because nothing is released and no installer exists. An installer command would be a fake install command under §8 rule 6, and that honesty constraint is load-bearing here, not a stylistic preference. The platform note the reference prints beside its install command is deliberately absent: it labels installer choices this project does not ship. The CTA row beside it carries one primary and one secondary CTA under a CTA intent lock: **"Read the spec"** is the only read-docs intent anywhere on the page and **"View on GitHub"** is the only repository intent, reused verbatim in nav, hero, and footer. No "Get started", no "Learn more", no "Contact".

### Backends strip content model

The strip is two kinds of thing, and the distinction is a product claim, not a design detail.

- **Seven marks in all: three runtimes and four models.** Counted from `backends.backends` and `backends.models` in `copy.ts` (slugs `ollama`, `llamacpp`, `vllm`, `googlegemini`, `qwen`, `mistralai`, `deepseek`) and mapped one to one through `backend-marks.tsx`.
- **ADR-004 fixes the generation interface to `ollama`, `llamacpp` and `vLLM`.** Those three are the runtimes the project targets, and they are the only runtimes the strip may list as supported.
- **Gemma, Qwen, Mistral and DeepSeek appear as examples of open-weight models** those runtimes can serve on the builder's own machine. They are not providers the project claims to support.
- **Everything else is deliberately absent.** OpenAI, Anthropic, Gemini and xAI because their weights are not available to run locally and ADR-004 rejects cloud backends; Groq because it is an inference service, not a model; Llama because Simple Icons has no mark for it and the llama silhouette already appears as the llama.cpp mark.
- **The label admits both kinds of thing.** It reads "Local runtimes, and the open-weight models they serve" rather than pretending the strip is one list.
- **A hairline rule separates the runtimes from the models** so the two kinds do not blur together.

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

- Zero em-dash and zero en-dash characters in any rendered string. Only the ASCII hyphen `-`.
- No AI-purple or neon gradient. No `text-shadow` glow on text. No outer glow on cards. One tinted inner highlight is permitted on glass panels only.
- No pure `#000000`. No pure `#FFFFFF` for text surfaces (`ink-9` is `#f5f5f5`).
- No scroll cue. No `Scroll`, no arrow, no animated wheel.
- No decoration text strip at the hero bottom. No `TYPE / FORM / MOTION`.
- No section-number eyebrows (`00 / INDEX`, `01 / CAPABILITIES`) and no generic step labels (`Stage 1`, `Phase 01`, `Step 1`). The pipeline section's `01` through `07` are step numbers attached to step content, which is permitted; its steps are named with the verb: *Ingest, Parse, Index, Plan, Retrieve, Cite, Abstain*.
- No decorative status dots: the single permitted dot is a real semantic state indicator in the tree preview when it genuinely represents a streaming state. No pills or labels overlaid on images, no photo-credit captions as decoration, and no locale, time, or weather strips.
- No testimonials at all, there are none to show, and no fabricated precision (`99.99%`, `4.1x`).
- No filler verbs: elevate, seamless, unleash, next-gen, revolutionize. No "Acme", "Nexus", "SmartFlow". No Inter as the body font.
- No hand-rolled SVG icon paths for UI icons. Phosphor only, one family, one stroke weight. The one exception is the project mark and the favicon set that mirrors it, which is four hand-drawn rects in section 5.
- No div-based fake screenshots. The hero tree preview renders actual `GET /v1/documents/{id}/tree` response shapes from `docs/api.md` §2, as a Book/Title/Chapter/Article path with page numbers, because the structural tree is the product.
- No `border-t` plus `border-b` on every row of a list. Pick one divider style per list.

---

## 10. Accessibility gate

- WCAG 2.2 AA minimum. AAA for body copy where achievable. `ink-5` is banned for readable strings; it is the only token in the palette that cannot carry text.
- Visible focus on every interactive control: `outline: 2px solid var(--color-accent); outline-offset: 2px`. Never removed.
- The marquee pauses on hover, on focus-within, and through a real pause button, because its track holds no focusable element.
- Every SVG icon is either `aria-hidden="true"` when decorative beside visible text, or carries an accessible name when it is the only content of a control. Both hero canvases are `aria-hidden`, and the rail's visible number is `aria-hidden` with the label as the anchor's accessible name.
- Semantic landmarks: one `h1`, plus `header`, `nav`, `main`, `footer`. Heading order never skips a level. Light mode is not offered (see §3); `color-scheme: dark` is declared so form controls and scrollbars render correctly.
- Target size: interactive controls at least 44px on the smallest breakpoint; the rail's anchors are `h-16 w-16`. No content is hidden behind the rail at any scroll position, because the page content wrapper's `lg:pl-16` clears its `w-16` for every section, and the rail's `z-30` keeps it below the sticky header, which in turn must not obscure keyboard focus: `scroll-margin-top` on every section anchor equals nav height plus 16px.

---

## 11. Core Web Vitals

| Metric | Target | How |
|---|---|---|
| LCP | under 2.0s | Hero renders as static HTML. Fonts via `next/font` with `display: swap`. No hero bitmap to download: the hero is text, a command block and the tree preview panel, all DOM, not image. |
| INP | under 200ms | Zero JavaScript above the fold except the copy button, the reveal observer, and the hero backdrop. The 2000-dot field draws once on mount and once per resize and never per frame; the backdrop's drift is transform-only and compositor-side; the only per-frame work is the crest layer, about 140 dots, capped at one redraw per `1000/24` ms. That cap is the budget the layer must stay inside: it may not move to a per-frame redraw or grow past a fraction of the 200ms interaction budget. No GSAP, no scroll handlers, no third-party scripts. |
| CLS | 0 | Fonts loaded via `next/font` with size-adjust metrics. Marquee and code block have explicit reserved dimensions. Reveal animates opacity and transform only. |

---

## 12. Implementation file layout

Everything below lives under `apps/web`. Paths are exact.

```
apps/web/
  src/app/
    layout.tsx                 fonts, metadata, theme lock, skip link
    page.tsx                   marketing route, composes the 8 sections, owns the fixed rail and the `lg:pl-16` content wrapper
    globals.css                @theme tokens, base layer, reduced-motion block
    icon.svg                    favicon, scalable, mirrors brand-mark.tsx's four rects and three gradient stops in hex, not imported (see section 5)
    favicon.ico                 favicon at 16, 32 and 48, same source geometry
    icon.png                    favicon at 512, transparent
    apple-icon.png              apple touch icon at 180, opaque
  src/components/marketing/
    site-nav.tsx               brand lockup and the two CTAs only, no section links
    site-footer.tsx
    brand-mark.tsx            the project mark, four rects on a 64-unit grid, per-instance gradient id
    hero.tsx                   hero section, two-column grid from lg up, one column below
    hero-rail.tsx             numbered section rail, owns no positioning; page.tsx places it
    hero-backdrop.tsx         hero background: drift wrapper plus crest canvas, consumes dot-wave.tsx
    dot-wave.tsx              hero dot field, canvas, drawn once, rendered by hero-backdrop.tsx
    tree-preview.tsx           explorer preview in its own grid cell, @container scope
    backend-marquee.tsx
    backend-marks.tsx         official brand marks, monochrome via currentColor
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
    site.ts                    links, repo metadata
```

`src/lib/copy.ts` is mandatory. Every user-visible string in the marketing route comes from that one file, with the wordmark as the single documented exception (see §5). This makes the copy self-audit mechanical and keeps the em-dash ban enforceable by a single grep over one file.

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
[ ] Reduced-motion block present and proven by toggling the media query, and hero-backdrop.tsx gates both layers through useReducedMotion
[ ] Every image and SVG has alt text or an explicit aria-hidden
[ ] Both hero canvases are pointer-events-none, aria-hidden, absolutely positioned; dot-wave.tsx has no animation loop; hero-backdrop.tsx redraws only the crest layer and only every CREST_FRAME_MS = 1000/24 ms of wall clock; accent-2 appears as a class or token reference only in comparison-arms.tsx and dot-wave.tsx (hero-backdrop.tsx reads the CSS variable and counts as dot-wave's exception per section 3)
[ ] hero-backdrop.tsx animates only opacity and transform, and the crest redraw cost stays inside one CREST_FRAME_MS = 1000/24 ms window measured in a real browser with zero long tasks
[ ] brand-mark.tsx renders one aria-hidden, focusable="false" svg holding exactly four rects on viewBox "0 0 64 64" and no raw hex outside its three gradient stops; BrandMark is imported by exactly two files, site-nav.tsx and hero.tsx, both at size 20; the wordmark text sits beside the mark at both call sites, since the mark is aria-hidden and carries no name itself
[ ] The four favicon assets exist in apps/web/src/app (icon.svg, favicon.ico, icon.png, apple-icon.png); favicon.ico carries real 16, 32 and 48 entries, icon.png is 512 with transparency, apple-icon.png is 180 and fully opaque; icon.svg holds the same four rects and the same three gradient stops as brand-mark.tsx, in hex rather than oklch, with the mapping recorded in the file's own comment
[ ] The hero h1 is one element, so its accessible name is one sentence
[ ] No string in copy.ts asserts superiority or reports an unmeasured metric
[ ] Disclaimer string present in copy.ts and rendered on the page
[ ] tsc --noEmit clean, next lint clean, next build clean
[ ] Lighthouse on the built route meets or beats section 11
[ ] Verified in a real browser at 390px, 768px, 1440px, and 1920px
[ ] site-nav.tsx renders no section links in either the desktop row or the mobile panel; the rail is the only section-navigation surface, and below 1024 no section navigation is rendered at all
[ ] hero-rail.tsx is placed by page.tsx in a `fixed` wrapper at `z-30`, one slot below the sticky header's `z-40`, and the page content wrapper's `lg:pl-16` clears the rail's `w-16` for every section at 1024, 1440 and 1920 with no per-section offset doing the work
[ ] The marquee is the only marquee
[ ] No three-equal-card row anywhere
[ ] All 8 sections use 8 different layout families
```