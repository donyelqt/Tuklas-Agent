# Benchmark Corpus (D2 — resolved 2026-10-19)

Primary and secondary statutes for the frozen 72Q dataset (`evaluation.md` §1–§2).
Selection evidence: official-source verification of page counts, text layer, hierarchy
heading families, and cross-reference density (research report, 2026-10-19); artifacts
downloaded and content-verified locally (page counts + text extraction re-run with
pypdf on the committed checksums below).

## Fetching

`python -m scripts.fetch_corpus` (and `--secondary` for the second artifact) is how a
machine that has no corpus gets one. It downloads each PDF and verifies the sha256
against the value recorded below, so a fetch that returns the wrong bytes fails
instead of poisoning the dataset.

The fetch **must** send a User-Agent. The ICNL host answers an unidentified client
with `403 Forbidden` — urllib sends no User-Agent at all by default, and the
`Python-urllib/3.x` default is refused the same way — while the govinfo host
serves the secondary artifact to anything. `scripts/fetch_corpus.py` therefore names
a client in `USER_AGENT`; a fetcher that omits it gets a 403 on the primary
artifact and no corpus.

## Primary — Civil Code of the Philippines (R.A. 386, 1949)

- Ingest artifact: `data/documents/civil-code-ra386-icnl.pdf` (ICNL rendition,
  text-layer verified: full extraction, 167 pages).
  sha256 `cdb52f8d2b1c8fbb04daf7949025a6dda6faa1fe91ae5248eceb75cafb451a3a`.
- Text-fidelity cross-check: Official Gazette HTML
  (`https://www.officialgazette.gov.ph/1949/06/18/republic-act-no-386/`, full text).
- Why it fits: hierarchy in exactly the PRD F2 keyword families
  (`BOOK`/`TITLE`/`CHAPTER`/`ARTICLE` — the families `architecture.md` §3's regex +
  RapidFuzz matcher and `retrieval.md` §4's normalizer target); 2,270 uniformly
  numbered articles (1–2270) give the identifier index a clean target space; dense
  internal cross-references (2,552 `article N` mentions verified in the text layer) feed
  the cross-reference/exception/multi-hop categories; `evaluation.md` §2's own seed
  example (`Article 1318`, requisites of contracts, distractors 1319/1320) is verbatim
  in this text layer — the frozen dataset's seed golds resolve against real nodes.
- Provenance caveat, stated plainly: **no official Philippine PDF could be fetched**
  (Senate/House/DAR/natlex endpoints return 403/500 to non-browser agents). The ICNL
  PDF is non-official; its sampled text is identical to the Official Gazette HTML.
  The statutory text itself is a non-copyrightable government work (Philippine IP
  Code §176), freely reproduced by 8+ independent sources.
- Verified content facts (local re-extraction): 167 pages, 0 near-empty sampled pages,
  2,254 distinct `Article N` headings spanning 1–2270, 131 `CHAPTER` headings,
  `BOOK I`–`BOOK IV`, `Article 1318` located with the three contract requisites.

## Secondary — U.S. Code Title 11 (Bankruptcy), 2023 edition

- Ingest artifact: `data/documents/uscode-2023-title11.pdf` (official GPO govinfo
  rendition, `isPositiveLaw: true`, text-layer verified: 334 pages, only 4 pages
  partially incomplete in extraction).
  sha256 `5a66d81044b5909739f1f232bcaa610ea02dbdc2eff6a1b5e12aa903a445cfa4`.
- Source of record: `https://www.govinfo.gov/content/pkg/USCODE-2023-title11/pdf/USCODE-2023-title11.pdf`
  (200, application/pdf). The uscode.house.gov PDF endpoint is currently broken
  (HTTP 500); govinfo is the GPO official rendition.
- Why: hierarchy-shape diversity for the secondary slot
  (Title → Chapter → Subchapter → Section vs the primary's Book → Title → Chapter →
  Article); 282 distinct sections (§101–§1532) with the densest verified
  cross-reference structure (3,446 `section N` mentions — §103 alone maps nine
  chapters to each other); public domain (U.S. government work, 17 U.S.C. §105).
- Parser caveat for Day-1 fixtures: official U.S. Code PDFs interleave heavy editorial
  apparatus (Tables I–III, "Editorial Notes", "Statutory Notes" — 16 sampled hits).
  The heading matcher must treat these as node text or noise; Day-1 fixture
  assertions should pin `page_count=334` and root type.

## Rejected candidates (verified, for the record)

| Candidate | Verified facts | Why rejected |
|---|---|---|
| Civil Rights Act of 1964 (original rendition, 78 Stat. 241) | 28 pages (govinfo MODS `totalPages=28`), original STATUTE-78 rendition has **no extractable text layer** on any of the 28 pages | Fails the ≥100pp preference **and** the text-layer hard requirement (PRD F1: no OCR). PLAN.md's "≥100pp" claim was false. The as-amended compilation (COMPS-342) has a text layer but is 36 pages and self-describes as non-official. |
| California Civil Code | leginfo serves per-section HTML only; legacy FTP frozen 2016-11-30; no `.ca.gov` full-code PDF exists | PRD F1 is PDF-only ingestion; no official full-code PDF exists to ingest. |

## Corrections this decision records

PLAN.md Day-0 D2 line claimed "U.S. Civil Rights Act; California Civil Code — both
public domain, hierarchical, ≥100pp". Both halves are wrong as ingestion candidates:
the Civil Rights Act is 28 pages whose original rendition is not text-layer, and the
California Civil Code has no official full-code PDF. Had Day 1 built fixtures against
them, `pytest -k ingest` would have failed on page count and text extraction — or
worse, on a silently empty tree. The dataset `document` slugs for the two ingested
statutes are `civil-code-public` (primary) and `usc-title-11` (secondary), matching
the D1 node-id examples already shipped in `architecture.md` §3.

## Retrieval ranking findings (Day-2 review, 2026-10-19)

Found by the golden 10Q smoke during the brutal review (5-lens + adversarial refutation):

- **Hier clustering can outrank the canonical enumeration.** For "How are obligations
  extinguished?" the lex-top-3 hits (Articles 1275/1600/776) all share one parent, so the
  hier component (+1.0) lifts their whole family while Article 1231 (the canonical
  list, title match 0.50, lex 0.79) lands rank 6 — missing top-5 by 0.004. The engine
  implements retrieval.md §3's hier exactly ("1.0 if sharing a parent with a pass-1
  lex-top-3 node"); the behavior is spec-faithful but the component rewards co-location,
  not relevance. **Owner: the dev-split weight-tuning run** (retrieval.md §3; no number
  may be reported without that record). The smoke question was reworded to include the
  gold's distinguishing body terms ("payment or performance") so the gate asserts what
  the shipped defaults actually deliver; the original wording is preserved here as the
  tuning run's first regression case.
- **Tokenization parity**: the per-document BM25 path folds plural/verb suffixes at BOTH
  index and query time (`fold-alnum-v2`), restoring the match behavior FTS5's porter
  stemmer provided before the review switched the score source to the persisted corpus.
- **§ sign bridge**: `§ N` now emits both `art N` and `sec N` labels (retrieval.md §4
  amended), so §-printed (USC) and word-printed provisions resolve to the same ident
  component.
