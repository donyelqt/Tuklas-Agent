# Versions

This file records which measured run is which. It is the answer to "what am I
looking at, and is it the same as the thing that produced the published numbers?"

## The two runs that exist

Both are real. Both were measured. Neither is a baseline for the other.

| | old v1 (superseded) | v1 (current) |
| Test split | `data/eval-reports/v1-test-OLD-INSTRUMENT-AND-STORE.md` | `data/eval-reports/v1-test.md` |
| Dev split | `data/eval-reports/v1-dev-OLD-INSTRUMENT-AND-STORE.md` | `data/eval-reports/v1-dev.md` |
| Run id | `eval_a80167ab` / `eval_083a4c15` | `eval_447127eb` / `eval_ff373231` |
| Config id | `cfg_e86e8bfbc803c860` / `cfg_5a3d6973caf7bd43` | `cfg_e86e8bfbc803c860` / `cfg_0f73ff26c0d38d54` |
| Instrument | `whole-question-search-v0` | `answer-node-union-v1` |
| Lex path | per-document `bm25.json` present | FTS5 fallback, artifact absent |
| Root tree | `04f6dcae` | `4a3b6f4d` |
| `apps/api/retrieval` tree | `d19c4365` | `240f229c` |

The suffixes on the two instrument names belong to the instruments themselves, not to
the run labels used in this file.

The two test runs share a config id (`cfg_e86e8bfbc803c860`) even though they were
measured under different instruments and different lex layers. That gap is now
CLOSED for future runs: `code_version` is validated against the code's own
instrument constant (`answer-node-union-v1`, the harness the code implements), so
a config whose `code_version` does not match the harness the code implements is
refused, and two runs with different instruments can no longer share a config id.
The config ids in the PUBLISHED reports (`cfg_e86e8bfbc803c860` /
`cfg_0f73ff26c0d38d54`) are the LEGACY ids of the runs that produced them; they
remain correct as historical identifiers in the published reports and the store
rows, which are not regenerated. The dev runs did not collide
(`cfg_5a3d6973caf7bd43` vs `cfg_0f73ff26c0d38d54`) because their configs differ
in other fields.

old v1 is preserved because it is a measurement that was actually taken and because
its run's per-question rows live in a gitignored store that will not survive. It is
NOT A BASELINE: the two runs do not measure the same thing.

v1 is the run whose numbers are published in `data/eval-reports/`. It is the current
measured state of the system. It is NOT a verdict on vectorless versus dense.

## Why the numbers differ

The difference is not one thing. It is three recorded, measured causes, and none of
them is sampling noise on the ranking:

1. **The instrument changed.** The old v1 run computed the retrieval metric over a
   separate one-off search over the whole question. The current v1 run computes it
   over the union of the agent's per-sub-question `search_nodes` results. Measured,
   vectorless arm on test-48, lex layer held fixed: recall@5 −0.061 (0.7927 →
   0.7317), mrr@10 −0.1028. The instrument is the largest single effect in both
   arms.
2. **The lex layer changed.** The old v1 run was measured with the per-document
   `bm25.json` artifact present; the current v1 run without it, so the vectorless arm
   fell back to FTS5, whose N and average document length are table-global. Measured:
   recall@5 −0.0122, mrr@10 +0.0187; interaction with the instrument +0.0122.
3. **The dense arm's corpus changed.** It was cut to the vectorless arm's lexical
   node set, its title de-duplicated and its query's identifier tokens stripped.
   Net −0.0244 recall@5, −0.0262 mrr@10.

The ranking code itself did not change. Re-scoring the split under each of the three
code trees (`9401dc6`, `a8e7eea`, `47cb80e`) gives **identical top-20 rankings on all
48 questions, on both lex paths**. The ranking is a pure function of (corpus, query,
weights) and it is deterministic: three repeated runs produced one sha256.

## The nondeterminism that does exist

The ranking function is deterministic. What is not deterministic is the node set of
the current v1 run's instrument. `arms.py:_retrieved_node_ids` reads the node ids out
of the agent's own tool trace, and the agent decomposes a question into sub-questions
with an LLM. So a re-run of the current v1 configuration can retrieve a different node
set than the published run did, and its retrieval numbers can move with it. That is
real, and it is the reason the generation half carries an **unchecked ±5pp** tolerance
rather than an exact reproduction guarantee. It is not the cause of the old v1 to v1
difference: that was measured per-cause with the lex layer held fixed, as above.

## What a reader may not conclude

- No verdict. The vectorless arm did not get worse and did not get better.
- old v1 is not a baseline and the two are not comparable.
- The v1 run is not a published package. There is no `npm`/`npx`/`pip` artifact and
  no install command.

## Known defects in this record

- `apps/api/evaluation/report.py`'s `STORE_STATE_NOTE` used to name `4d8ad7e6` as
  the tree of the earlier run and `04f6dcae` as the tree of this one, and to claim
  the two runs' retrieval trees were byte-identical. Both are false. `4d8ad7e6` is
  the root tree of commit `25272c1`, which predates the evaluation package
  entirely, and `04f6dcae` is the tree of `9401dc6`, the commit that first wrote
  the *earlier* run's id. Corrected in the renderer and re-spliced into the two
  published reports; the two `-OLD-` files were left untouched because they are
  byte-identity-pinned measurement artifacts.
- The run row records neither the retrieval instrument nor the lex path that
  produced its scores. Tracked in `docs/TODO.md`.

## Why there is no npm / npx / pip artefact

Verified, not assumed. `apps/web/package.json` is `name: "web"`, `version:
"0.1.0"`, `private: true`, and has **no** `bin`, `files`, `publishConfig` or
`license` key:

```
name: web     version: 0.1.0     private: true     bin: null
files: null   publishConfig: null  license: null
```

- **`web` is taken on npm.** `npm view web` returns `web@0.0.2 | MIT | "A small
  and fast web/http library for nodejs"`. Publishing this package would collide
  with a third-party package of the same name.
- **`private: true` blocks publishing.** npm refuses at the upload layer
  (`EPRIVATE`); `--dry-run` does not show it because the upload is skipped.
- **There is no `bin`, so `npx` cannot run anything.** Without a `bin` entry
  `npx` exits `could not determine executable to run`. This is a Next.js site,
  not a CLI.

Licensing is not one of the blockers. The repository is MIT-licensed: the LICENSE file
at the repo root, and the MIT licence recorded in pyproject.toml. The package
manifest named above declares no licence key, which is a fact about the web package,
not about the repository's licensing. npm and PyPI packaging is blocked by the
mechanical packaging reasons above, not by licensing.

A tag + GitHub Release is the only channel with no mechanical blocker, and the
licence it needs is already in place.

## Why the reports keep the name `v1-test.md`

Asked whether the current reports should be renamed with a decimal version suffix
because "v1" is already taken. It is not. The `v1` prefix is the **dataset version**
(`data/datasets/v1/`), and there is no v2 dataset. `data/datasets/v1/README.md:44`
names `data/eval-reports/v1-test.md` as the reported test-48 report. Both the
current and the superseded reports carry `v1` because both report on the v1
dataset. Renaming would fabricate a dataset version that does not exist, break the
link to that README, and throw away the existing disambiguation: the superseded
files carry the `-OLD-INSTRUMENT-AND-STORE.md` suffix, which says *why* they are
old, and `data/eval-reports/README.md` separates CURRENT from PRESERVED. The two
runs are disambiguated here, by run id and by what changed between them.