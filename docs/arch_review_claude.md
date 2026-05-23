# Pancratius Architecture Review (Claude Code)

## 1. Executive diagnosis

**The project is fundamentally sound, unusually well-documented, and drifting in two specific, traceable ways — not structurally rotten.** The `docs/` set is a real architecture contract (it states source-of-truth rules, the import/render/build split, the CC0/static posture), and most of the code honors it: `dist/` is transient, `data/slug-map.json` / `data/bulk-archives.json` / `public/data/*` are gitignored and regenerated, the work bundle is genuinely co-located, CI is verifiably Node+uv-only and never renders documents (`.github/workflows/build.yml:7-8`, `deploy.yml`), and the bulk archive reuses the *same* public-Markdown renderer as the per-work route (`scripts/build_bulk_archives.ts:17,146`). Several of the brief's fears are already false: graph payloads contain **zero** `legacy/` paths (`data/pancratius-books-graph.json`, `public/data/*` verified clean); the only `legacy/` references in committed data are provenance in `data/conversion-manifest.json` (150, expected).

So this is not a rescue. But there are **two root causes** under almost every symptom you listed:

**Root cause A — a category error: `projects` (and tomorrow `videos`) are forced into the "Work" abstraction.** The Work model — identity `(kind, number)`, `translation.source`, a full `md/txt/docx/pdf/epub` download matrix, converter ownership, graph participation — was designed for *homogeneous converted DOCX corpus documents* (books, poems). Projects are **authored, web-native pages**: hand-written `<aside class="project-negation">`, `<section class="project-qa">`, `<dl>`, `<blockquote class="project-pull">`, taglines, curated editorial `cross_refs` (`src/content/projects/enlightened-ai/ru.md:9,27,40,64-66`). Yet they are a content collection sharing the work schema, **owned by `scripts/docx_to_md.py` reading `legacy/data/projects-data.js`** (`load_projects` at 253-254; `convert_project` at 2803-2806; `--kind project` at 3083-3088), pushed through the public-Markdown/downloads/bulk machinery, and the same DOCX-tuned cleaner. This single error generates: the "converter can overwrite ground truth" risk, the "pages schema is a dumping ground" smell, the "duplicated markdown rendering degrades" symptom, and the unanswered "where do videos go?" question.

**Root cause B — the binary-locale assumption is baked into selection and fallback logic.** Strings *are* centralized (`src/lib/copy.ts`, `conceptosphere/strings.ts` — genuinely good). What is **not** centralized is the ~30 occurrences of `locale === "en" ? pair.en : pair.ru` selection and the RU-fallback chains, plus a handful of true string ternaries (`HeadMeta.astro:26-27`, `seo.ts:89-92`). Your stated "100% another language will land" collides head-on with this: the *string layer* extends cleanly to a third language; the *selection/fallback layer* does not.

Everything else — kind→segment duplicated ~10×, the two Markdown cleaners, `editorial.yaml` scaffolding still load-bearing, the `StaticPage` god-component trajectory, dual image emission — is either a symptom of A/B or independent low-grade debt. None of it is incoherent. The codebase's problem is **accidental absorption** (one abstraction swallowing content types it shouldn't) and **a brittle binary baked where a list belongs.**

---

## 2. Source-of-truth map

| Artifact                                                 | Location                             | Category                                    | Writer                                           | Reader                              | Sergey edits?                 | CI generates?                      |
| -------------------------------------------------------- | ------------------------------------ | ------------------------------------------- | ------------------------------------------------ | ----------------------------------- | ----------------------------- | ---------------------------------- |
| Work Markdown `ru.md`/`en.md`                            | `src/content/{books,poetry}/<key>/`  | **Authored source** (seeded by import)      | `import_docx.py` once, then human                | Astro, downloads, bulk, audits      | **Yes** (prose + frontmatter) | No                                 |
| Project `ru.md`/`en.md`                                  | `src/content/projects/<slug>/`       | **Authored source** (currently mis-owned)   | *Today:* `docx_to_md.py` ⚠️ / *Should be:* human | Astro, downloads, bulk              | **Yes**                       | No                                 |
| Static page Markdown                                     | `src/content/pages/<slug>/<lang>.md` | **Authored source**                         | human                                            | StaticPage / dedicated routes       | **Yes**                       | No                                 |
| Covers `cover.<lang>.<ext>`                              | work bundle                          | **Authored asset**                          | human / importer                                 | Astro `covers.ts`                   | Yes (replace file)            | No                                 |
| Body images `images/**`                                  | work bundle                          | **Imported/authored asset**                 | converter (hashed) / human (named)               | Astro `_astro/*`, `/assets/*` route | Yes (add named)               | No                                 |
| `<lang>.docx`                                            | work bundle                          | **Source + release artifact**               | `import_docx.py` / `render_downloads.py`         | downloads copy                      | replace                       | No                                 |
| `<lang>.pdf` / `.epub`                                   | work bundle                          | **Release artifact**                        | `render_downloads.py` (local)                    | downloads copy                      | no (generated)                | **No** (copied only)               |
| `.md` / `.txt` downloads                                 | emitted to `dist/`                   | **Build output**                            | `downloads.ts` via `public-markdown.ts`          | browser                             | no                            | **Yes** (cheap derivation)         |
| `bibliography.yaml`                                      | work bundle                          | **Authored/imported sidecar**               | converter / human                                | export only                         | yes                           | No                                 |
| `editorial.yaml`                                         | repo root                            | **Temporary scaffolding** ⚠️                | human                                            | `docx_to_md.py`                     | yes                           | No                                 |
| `data/conversion-manifest.json`                          | `data/`                              | **Committed provenance**                    | `docx_to_md.py`                                  | audits                              | no                            | No                                 |
| `data/pancratius-*-graph.json`                           | `data/`                              | **Committed generated**                     | `conceptosphere.py` (local, MLX)                 | `conceptosphere.ts`, copy step      | no                            | **No** (committed, manual refresh) |
| `data/conceptosphere-embed.json`                         | `data/`                              | **Committed build-input** (never published) | `conceptosphere_embed.py`                        | graph generators                    | no                            | No                                 |
| `public/data/*-graph.json`                               | `public/data/`                       | **Build output** (gitignored)               | `build_copy_graph_payloads.py`                   | client graph                        | no                            | **Yes** (prebuild copy)            |
| `data/slug-map.json`                                     | `data/`                              | **Build output** (gitignored)               | `build_slug_map.py`                              | `astro.config.ts` sitemap           | no                            | **Yes** (prebuild)                 |
| `data/bulk-archives.json` + `.cache/bulk-archives/*.zip` | `data/` + `.cache/`                  | **Build output** (gitignored)               | `build_bulk_archives.ts`                         | `/downloads/[file].ts`              | no                            | **Yes** (prebuild)                 |
| `dist/`, `.astro/`                                       | root                                 | **Transient**                               | Astro                                            | host                                | no                            | Yes                                |

The boundaries are **clean except two rows**: projects (writer should be human, not `docx_to_md.py`) and `editorial.yaml` (a "temporary" file that is now an architectural dependency of the converter, `docx_to_md.py:155-162`).

---

## 3. Import / render / build model

**Direct answer to the `docx_to_md.py` question: yes — and the steady-state single-DOCX tool already exists, it is `scripts/import_docx.py`, not `docx_to_md.py`.**

`scripts/import_docx.py` (438 lines) is the correct durable tool: one DOCX → one work folder, additive, no `legacy/` dependency, seeds a `TODO:` description so the file validates (`content-model.md:279-305` documents this flow). `scripts/docx_to_md.py` (3231 lines) is the **one-time mass-import engine**: it reads from `legacy/` (`LEGACY = ROOT/"legacy"`), is gated by `editorial.yaml`, regenerates whole kinds, and reads projects from `legacy/data/projects-data.js`. Keeping it as the steady-state converter is itself drift, because:

- It depends on `legacy/`, which is **gitignored and slated for deletion** (your own memory + `.gitignore`). On a fresh clone it cannot run.
- It is gated by `editorial.yaml`, declared temporary in two places (`editorial-notes.md:5-7`, `docx_to_md.py:155-162`).
- It can **overwrite authored projects** (see §4).

So the end state is: **`import_docx.py` is the only converter that survives; `docx_to_md.py` is deleted once the corpus import is finalized.** Until then, the immediate safe move is to **stop `docx_to_md.py` from ever writing `src/content/projects`** (remove or hard-guard the `--kind project` path). The brief's instinct ("flags to specify which WorkKinds to touch") is already half-implemented (`--kind book/poem/project`), but the right answer for projects is not "make it opt-in" — it is "remove projects from the converter's vocabulary entirely."

**What `--clean` should be allowed to delete:** today it `shutil.rmtree`s only the *selected* work folders before regeneration (scoped, matches `content-model.md:61-66`). That is acceptable *for books/poems* and *only* under explicit `--clean`. It must never be the default, and never operate on whole kind directories. It must never touch `projects` or author-added neighbors. This matches the documented contract; the only fix is the projects exclusion.

**"Local library management" means:** the three local/admin activities that need heavy tools (pandoc, typst, MLX/Qwen) and human judgment — (1) import/clean DOCX, (2) render PDF/EPUB/merged DOCX release artifacts (`render_downloads.py`), (3) regenerate graph/embedding data (`conceptosphere*.py`). These run on a maintainer's machine, produce committed artifacts, and are reviewed before commit.

**CI should and does:** `npm ci` → `astro check` → `astro build` (which runs prebuild: slug-map, graph-payload copy, bulk-md archive) → pagefind → smoke → publish `dist/` over FTP (and a parallel GH Pages mirror). **CI should not and does not:** install pandoc/typst, render documents, run embeddings, or regenerate graphs. This is correctly enforced and even commented as a guardrail (`build.yml:7-8`). **Import scripts must never run in CI** — they don't, and `legacy/` isn't even checked out. This part of the architecture is healthy; protect it.

---

## 4. Content / page model

### Projects
**Projects must be author-owned, decisively.** The evidence is in the files: bespoke semantic HTML, taglines, position-paper register, editorial-only `cross_refs`. A converter that rebuilds them from `legacy/data/projects-data.js` is a **source-of-truth violation waiting for someone to type `--kind project`**. Recommendations:

- Remove projects from `docx_to_md.py`.
- Give projects their **own collection schema** that reflects what they are: `title`, `slug`, `number` (keep for `(kind,number)` identity if graph/cross-ref needs it), `description`, `tagline`, `cover`, `cross_refs`. Drop `translation: {source: original|literary|ai}` as a forced field — a project isn't a "translation of an original."
- A new project DOCX (if Sergey supplies one) lands via a **one-time seed conversion** into the folder, then becomes authored. Projects do not need a durable `.docx` source artifact the way books do; the current `ru.docx` in project folders is vestigial.
- Decide projects' **download surface deliberately** (see §6) — a position paper as EPUB is questionable.

### `pages` collection and `StaticPage`
The shared `pages` schema is becoming a **dumping ground** (`content.config.ts:131-160`): `portrait` is used by exactly one page (about), `facts` by one (about), `channels` by one (support). These are *page-specific data wearing a generic-schema costume*. `StaticPage.astro` is on the **threshold of a god component**: four layout variants chosen by a mix of slug-checks (`isMission`, `isSvetozar` at lines 31-32) and field-presence (`hasPortrait`→bio grid, `channels`→support grid, lines 39-52). It works for six pages; it will accrete a branch per new page.

**It is wrong that the generic page schema carries `portrait`/`facts`/`channels`.** Those belong to about and support specifically. And `support` deserves its own model — donation channels are arguably *config/data* (the values are currently placeholders, `support/ru.md:7-32`), not page prose.

**Are these pages "the same kind of thing"?** No. There are three registers:
1. **Plain prose** — `license`, `mission`, `svetozar` body: same kind, keep on a lean `StaticPage` (prose + optional ToC).
2. **Prose + bespoke structured block** — `about` (portrait + facts), `support` (channels widget): dedicated components, page-scoped data.
3. **Structural index** — `downloads`: already a dedicated route (correct). Its intro copy is inconsistent: index-page copy for books/poetry lives in `copy.ts`, but downloads' copy lives in the pages collection. Pick one home for index-page copy (recommend `copy.ts`).

### Future `/videos/`
Do **not** route videos through `StaticPage` or the Work model. Videos-with-blog-posts are authored, have video-specific fields (provider/id, poster, duration, transcript), and a blog body. Give them their **own collection + own schema + own routes**, exactly the lesson projects teach. This is the structural test of the whole review: if the answer to "where do videos go?" is "add fields to an existing generic schema," the architecture has lost.

---

## 2b. Static-page / MDX / page-builder boundary

| Option                                                             | Authoring ergonomics                                                  | Localization drift risk                                         | Type safety                 | Impl. complexity                        | AI-agent maintainability   | Long-term escape hatch                            |
| ------------------------------------------------------------------ | --------------------------------------------------------------------- | --------------------------------------------------------------- | --------------------------- | --------------------------------------- | -------------------------- | ------------------------------------------------- |
| **1. Plain MD + `StaticPage`**                                     | Excellent for prose; nil for structure                                | Low (each lang = one MD file)                                   | Schema-typed frontmatter    | Low                                     | High (one component)       | Poor — structure forces god-component branches    |
| **2. MD + typed frontmatter knobs** (`register: prose\|manifesto`) | Excellent; one obvious dial                                           | Low                                                             | Strong (enum)               | Low                                     | High                       | Good — knobs stay finite if disciplined           |
| **3. Dedicated Astro route per bespoke page**                      | Excellent (author edits prose only)                                   | Low                                                             | Strong (page-scoped schema) | Medium (one file per bespoke page)      | High (explicit, greppable) | **Best** — bespoke logic stays out of shared code |
| **4. MDX**                                                         | **Poor for non-tech author** (imports, JSX, a stray `<` breaks build) | **High** (localized `.mdx` duplicate component wiring per lang) | Mixed                       | Medium-high                             | Medium                     | Good technically, wrong for this author           |
| **5. `components: Component[]`**                                   | Poor (hand-writing JSON trees)                                        | High                                                            | Weak (stringly-typed)       | High (must build + maintain a renderer) | Low                        | Bad — a worse MDX you now own                     |

**MDX, concretely in this repo:** you'd add `@astrojs/mdx`, change the `pages` loader to `**/*.{md,mdx}`, and authors would write `import SupportChannels from "@/components/SupportChannels.astro"` then `<SupportChannels channels={...} />` inside `support/ru.mdx`. Localization means `ru.mdx` **and** `en.mdx` each repeat that import/usage, so a component-signature change touches every localized file. For an author whose contract is "edit Markdown prose, never touch components," MDX **moves the wrong way** — it puts component wiring into the file Sergey edits and makes the build fail on prose-author mistakes. **Recommendation: no MDX.**

**The `components: Component[]` model is a hand-rolled page builder — reject it.** It's bad MDX with weaker types and a renderer you maintain forever.

**Recommendation for the six pages (named tradeoff):**
- `license`, `mission`, `svetozar` → **plain MD + a lean `StaticPage`**, with **one** frontmatter knob `register: prose | manifesto | verse` replacing the `isMission`/`isSvetozar`/`prose--svet` slug-checks. (Trade: a tiny typed enum vs. slug `if`-ladder; the enum wins and stays finite.)
- `about`, `support` → **dedicated Astro components** (option 3), with `portrait`/`facts`/`channels` moved into **page-scoped schemas** and out of the shared `pages` schema. Support's channel data could even be a small data file since it's config, not prose.
- `downloads` → keep its dedicated route; move its intro copy to `copy.ts` for consistency with the other index pages.

Net: `StaticPage` shrinks to "prose + optional ToC + register knob"; the two genuinely-bespoke pages get explicit homes; the shared schema stops accumulating one-page fields.

---

## 5. Localization model

- **UI/chrome strings → stay in `copy.ts` + `conceptosphere/strings.ts`.** This is already the model and it's good; do not scatter.
- **Page editorial copy → the page's own Markdown** (frontmatter + body), already correct.
- **Route/page metadata (SEO) → entirely through `src/lib/seo.ts`.** Today index/work/search/page SEO go through it (good), but two leaks remain: `seoForHome`/`seoForKindIndex` embed literal strings via binary ternary (`seo.ts:89-92`), and `HeadMeta.astro:26-27` hardcodes `og:site_name`/`og:locale` inline. Fix: drive og from `Record<Locale, …>` (a `SITE_LABEL` already exists in `seo.ts:21`), so `HeadMeta` reads `seo.locale` against a record, never a ternary.

**The real scaling blocker is selection, not strings.** Adding a third language is mechanical in the dictionaries (every `Record<Locale, …>` extends with one key) but breaks at ~30 `locale === "en" ? pair.en : pair.ru` sites and the implicit "else → RU" fallbacks (`works.ts:109,178-186,221`; `downloads.ts:54`; `download-routes.ts:21`; `conceptosphere/page-data.ts:92`; etc.). **Introduce two helpers** — `entryForLocale(pair, locale)` and a single `localeFallback` policy — and replace the ternaries. Then a third locale is: widen the `Locale` union (one edit), add dictionary keys, add `src/pages/<lang>/` route folders, and redesign the 2-button switcher (the docs already flag `RU|EN` as needing replacement at 3+, `i18n-routing.md:55`). The URL layer (`localizePath`, `prefixDefaultLocale: false`) already supports `/de/`, `/fr/` — that part scales.

**Verdict: the localization architecture is coherent at the string layer and incoherent at the selection layer.** "Only a few labels" is not the issue; the issue is a binary `? :` baked where a keyed lookup belongs. As written it does **not** scale to a third language without touching ~30 sites — but the fix is a bounded, mechanical refactor, not a redesign.

---

## 6. Assets / downloads model

**Images.** Canonical source is the work bundle (`cover.<lang>.<ext>`, `images/**`) — correct and well-defended (`docs/decisions.md`). There are effectively **two emission paths, by design**: HTML pages use Astro's optimized `/_astro/*.webp`; downloads/exports reference a verbatim `/assets/<segment>/<work>/images/*` static route (`body-images.ts`). This is documented (`decisions.md:36-41`) and gives downloads a stable, work-scoped, locale-independent image URL shared by RU and EN — the right call. Two real costs to name, not bugs:
- The `/assets/` route emits **every file in each `images/` dir** regardless of whether any Markdown references it (`workAssetImageStaticPaths`), duplicating bytes already shipped as `_astro`. Against your 1 GB host ceiling and ~128 MB of `assets/books`, consider emitting only referenced images.
- Public-Markdown image URLs are absolute `https://pancratius.ru/...` (`public-markdown.ts:15,100-108`) and ignore `base`. For the GH Pages mirror (where `PUBLIC_SITE_URL` is unset, `mirror-github-pages.yml:36`) downloaded `.md` images point at the primary host. For a corpus download that is arguably *correct* (canonical URLs), but it's an implicit decision worth making explicit.

`public/` vs `/assets/`: `public/` is verbatim site-root static (favicon, portrait, llms.txt, `public/data/` graph copies); `/assets/` is the work-image static route. The distinction is clean. Cover images are editorial (human-named, `cover.<lang>.<ext>`, validated by `ALLOWED_COVER_RE` in `works.ts:140`); body images may be hashed (imported) or named (authored); bibliography "thumbnails" are correctly **not** images at all — they're lifted into `bibliography.yaml` (`content-model.md:217-249`). No brittle post-build image-copy script exists (`build_copy_body_images.py` is gone — confirmed). Good.

**Public Markdown / TXT.** **One** canonical TS renderer (`public-markdown.ts`) serves both the per-work `.md` route and the bulk archive (`build_bulk_archives.ts:146`) — no TS duplication. Frontmatter is stripped; verse made portable; inline HTML and converter wrappers (`epigraph`, `verse-block`, `answer-block`, `signature`) are converted (`public-markdown.ts:58-92`). **But** the cleaner is tuned to converter-emitted book/poem HTML and brute-strips everything else (`<[^>]+>` at line 136). Projects' authored `<section class="project-qa">`, `<dl>`, `<h2 id>` therefore **degrade** in `.md`/`.txt`/bulk — headings collapse to plain lines, definition lists merge into prose. This is the projects-as-works error surfacing in the export layer.

**The second cleaner is the real duplication.** `render_downloads.py` reimplements frontmatter-strip + `<img>`→Markdown + image-path rewrite in Python (`_strip_frontmatter`, `_html_images_to_markdown`, `_rewrite_image_paths`) for the pandoc/typst scratch input. TS `public-markdown.ts` and Python `render_downloads.py` are **two implementations of "clean source Markdown into portable Markdown" that can drift.** They target different sinks (public `.md` vs pandoc scratch), so full unification is hard, but the contract should be shared/tested in one place.

**PDF / EPUB / DOCX.** Committed release artifacts, copied at build, never rendered in CI — correct and well-guarded (`downloads.ts:1-8`, `decisions.md:112-127`). EPUB styling is intentionally a local-script concern (`downloads.md:62-73`); if minimal styling is a complaint, that's a `render_downloads.py` template decision, not architecture.

**Bulk archives.** One `all-md.zip` containing **both RU and EN in one archive** under `kind/lang/slug.md` (`build_bulk_archives.ts:142`) — correct per `downloads.md:112-124`. PDF/EPUB bulk are explicitly off-host. The `/downloads/` page is localized only because the *page chrome* is localized; the archive is bilingual. Good.

**Workaround scripts that should disappear:**
- `build_bulk_archives.ts` `publishToDist()` / `--publish` (lines 187-212) is **dead code** — the `/downloads/[file].ts` route emits from `.cache` instead. Delete it.
- `sync_pagefind_dev.py` `shutil.rmtree(TARGET)` without checking `SOURCE.exists()` — guard it; it's dev-only but will happily delete the index if `dist/pagefind` is absent.

---

## 7. Brittleness findings (ordered by severity)

1. **Projects modeled as converter-owned Works — source-of-truth violation (latent data loss).** `docx_to_md.py` (`convert_project` 2803-2806, `--kind project` 3083-3088) rebuilds `src/content/projects/<slug>/` from `legacy/data/projects-data.js`, clobbering hand-authored HTML (`projects/enlightened-ai/ru.md:27,40,64-66`). *Source-of-truth violation.*
2. **`docx_to_md.py` as steady-state tool.** 3231 lines depending on gitignored `legacy/` and on temporary `editorial.yaml` (155-162); cannot run on a fresh clone; the project-clobber path lives here. *Architecture smell + latent failure.*
3. **Binary-locale selection/fallback in ~30 sites + og ternary.** `works.ts:109,178-186,221`, `downloads.ts:54`, `download-routes.ts:21`, `seo.ts:89-92`, `HeadMeta.astro:26-27`, `conceptosphere/page-data.ts:92`. Directly contradicts the "third language is certain" requirement. *Architecture smell.*
4. **kind→segment mapping defined ~10×.** `i18n.ts:14-18`, `works.ts:18-22`, `body-images.ts:12-16`, `public-markdown.ts:9-13`, `astro.config.ts:69-73`, `build_bulk_archives.ts:25-29`, `render_downloads.py`, `build_slug_map.py`, `lib/content_catalog.py`, guarded by `audit/kind_segments.py:11-15`. The *existence of the audit is the smell.* Adding `videos` means editing ~10 files. *Acceptable-duplication trending to smell.*
5. **Two Markdown cleaners (TS `public-markdown.ts` vs Python `render_downloads.py`).** Same intent, separate code, silent drift risk. *Duplication.*
6. **Public-Markdown cleaner brute-strips unknown HTML (line 136),** degrading authored project exports. *Symptom of #1; production-quality bug for project downloads.*
7. **`editorial.yaml` still load-bearing.** A file labeled temporary in two places is an input to the converter and to EN titles (`editorial-notes.md`). *Debt with a documented exit not yet taken.*
8. **`pages` schema kitchen-sink + `StaticPage` god-component trajectory.** `content.config.ts:131-160`, `StaticPage.astro:31-52`. *Early-stage smell.*
9. **Committed graph/embed JSON refreshed only manually; no CI coverage check.** `data/pancratius-*-graph.json`, `data/conceptosphere-embed.json`. Adding works without re-running generators silently ships stale recommendations. CI *can't* run MLX, so committing is the right call — but add an audit that fails when graph node count ≠ corpus work count. *Accepted tradeoff missing a guard.*
10. **`astro.config.ts` URL regexes** (`WORK_RE`/`PAGE_RE` 75-76) parse routes because the config can't import `astro:content`; `PAGE_RE` distinguishes pages from works by a negative `SEGMENT_TO_KIND` check. *Acceptable with the existing comment* — but it's a fourth place encoding the segment list.
11. **Dead/loose code.** `build_bulk_archives.ts:187-212` (`--publish` unused); `sync_pagefind_dev.py:25` unguarded rmtree; `PagefindSearch.astro:12-13` "temporary feature notice" comment now false. *Harmless local detail.*

Two fears the brief raised that I am **rejecting on evidence:** graph payloads do **not** contain `legacy/` paths (clean), and the data files are **not** all naively committed (`slug-map.json`, `bulk-archives.json`, `public/data/*` are gitignored and regenerated; only provenance/graph/embed are committed, deliberately, because CI must not regenerate them). The earlier exploration overstated both.

---

## 8. Recommended target architecture

Keep it to **two content families and one rule per concern.**

- **Family 1 — Works (`book`, `poem`).** Homogeneous converted corpus. Identity `(kind, number)`. Full download matrix. Seeded by `import_docx.py`, then authored. Owns the graph and the public-Markdown export pipeline.
- **Family 2 — Authored content (`project`, static `pages`, future `videos`).** Hand-authored, bespoke layout, **own schemas**, no DOCX round-trip, no forced `translation.source`, download surface chosen per type. Never touched by `docx_to_md.py`.

Supporting decisions:
- **One `src/lib/kinds.ts`** as the single TS source for kind↔segment, consumed by `i18n/works/body-images/public-markdown`. For `astro.config.ts` and Python (which can't import it), generate a tiny `kinds.json` and keep the cross-language audit. Be honest: full DRY across TS+Py+config isn't free; centralize within TS, generate for the rest.
- **Locale: dictionaries stay; add `entryForLocale` + one fallback policy; SEO entirely via `seo.ts`; og via records.** This is what makes "language N+1" a bounded change.
- **`StaticPage` = prose + ToC + one `register` enum.** Bespoke pages (`about`, `support`) become dedicated components with page-scoped schemas. No MDX. No `components[]` builder.
- **One public-Markdown contract.** Either teach the cleaner about authored wrappers (project-aware) or — better — keep the cleaner for Works and give authored content a different, simpler export path (or no `.md`/`.txt` export at all for projects).
- **Retire:** `docx_to_md.py` (post-import), `editorial.yaml` (apply once, delete), the `--publish` dead code.
- **Keep untouched (they're good):** CI pipeline, download dispatch, cover/i18n URL helpers, graph payloads, the work-bundle co-location.

Fewer moving parts, no generic page-builder, no schema added to satisfy an audit.

---

## 9. Migration plan (small, safe, ordered)

**Do first (pure safety, no content change):**
1. Remove/guard the `--kind project` path in `docx_to_md.py` so it can never write `src/content/projects`. Removes the foot-gun before anything else. *(Needs your nod; no behavior change to the site.)*
2. Delete `build_bulk_archives.ts` `--publish` dead code; guard `sync_pagefind_dev.py`'s rmtree; fix the stale `PagefindSearch` comment.

**Then (mechanical, type-checked refactors):**
3. Add `entryForLocale(pair, locale)` + `localeFallback`; replace the ~30 ternaries; move `og:site_name`/`og:locale` and `seoForHome`/`seoForKindIndex` literals into `Record<Locale,…>`. `astro check` is the safety net.
4. Extract `src/lib/kinds.ts`; point all TS consumers at it; keep the Python audit; optionally generate `kinds.json` for config/Python.

**Then (structural, needs light review):**
5. Pages: pull `portrait`/`facts`/`channels` into dedicated `about`/`support` components with page-scoped schemas; slim `StaticPage`; add the `register` enum; move downloads' intro copy to `copy.ts`. *(Editorial: confirm no visible copy changes.)*
6. Projects: introduce a project-specific schema; decide their download surface; either make the public-Markdown cleaner project-aware or exclude projects from `all-md.zip`. *(Editorial input on downloads + whether projects stay permanent authored pages.)*
7. Bake `editorial.yaml` EN titles into each `en.md`, then delete the file and the converter's dependency on it. *(Editorial: confirm the 19 awaiting-review titles.)*

**Last (once corpus import is declared done):**
8. Delete `docx_to_md.py` and the `legacy/` dependency; `import_docx.py` is the steady state.

**Do not touch yet:** the graph/embedding pipeline, download dispatch, CI workflows, cover/i18n URL helpers — they are correct and changing them adds risk without payoff.

---

## 10. Open questions for the human

**Engineering:**
- Is the bulk corpus import effectively *finished*, or do you still expect full clean reconversions? (Decides whether `docx_to_md.py` is deleted now or merely fenced off.)
- Is the GitHub Pages mirror a real production surface or a fallback? (Decides how hard to chase `base`/`site` correctness in download/image URLs.)
- Given the 1 GB host ceiling, keep the dual image emission (optimized `_astro` + verbatim `/assets`) or trim `/assets` to referenced images only?
- Confirm the third-language timeline so we right-size the locale refactor now vs. later.

**Sergey / editorial:**
- **Projects:** permanent hand-authored pages, yes? And should projects offer downloads at all (does a PDF/EPUB of a position paper make sense), or HTML-only?
- **Videos:** are these "works with their own page + an attached blog post," or blog posts that embed a video? (Determines the new collection's schema before any code.)
- **`editorial.yaml`:** are the 19 awaiting-review EN titles final enough to bake in and delete the scaffolding?
- **Support page:** are the donation channels real yet (current values are placeholders), and should channel data live as page content or as config?
