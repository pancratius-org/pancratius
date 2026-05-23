No code changed.

**1. Executive Diagnosis**

The core problem is not Astro, not Markdown, and not static publishing. The core problem is boundary drift: import, canonical source authoring, release rendering, and site build are documented as separate activities, but some scripts and schemas still treat them as one pipeline. The worst example is `projects`: they look like authored domain pages, but `docx_to_md.py` can still generate and overwrite them.

The project is fundamentally sound, but structurally drifting. The good parts are real: static Astro build, content collections, co-located work bundles, static download endpoints, one public Markdown renderer, and documented i18n rules. The bad parts are also real: project EN pages contain Russian body text, the batch converter still owns current source files, UI strings are partly centralized and partly scattered, and kind/route mappings are repeated across TS/Python/Astro.

**2. Source-Of-Truth Map**

| Artifact | Location | Owner | Writer | Reader | Sergey edit? | CI generate? |
|---|---|---|---|---|---:|---:|
| Canonical work Markdown | `src/content/{books,poetry}/<work>/<lang>.md` | editorial | `import_docx.py` initially, then human | Astro, downloads, graph | yes | no |
| Project Markdown | `src/content/projects/<slug>/<lang>.md` | should be editorial | currently `docx_to_md.py` can write it | Astro, downloads, graph | yes | no |
| Static page Markdown | `src/content/pages/<slug>/<lang>.md` | editorial | human | `StaticPage` or dedicated route | yes | no |
| Work source DOCX | `src/content/**/<lang>.docx`, part DOCX | library manager | importer/optimizer/local | download routes, local render scripts | replace/review | no |
| PDF/EPUB release artifacts | `src/content/**/<lang>.pdf/.epub` | library manager | `render_downloads.py` | download routes | no | no |
| Body images/covers | `src/content/**/images/**`, `cover.<lang>.*` | editorial/import | importer or human | Astro, `/assets`, exports | yes | emitted only |
| Graph data | `data/pancratius-*-graph.json` | local data pipeline | graph scripts | graph page, copy script | no | copy only |
| Public graph copy | `public/data/*.json` | build artifact | `build_copy_graph_payloads.py` | browser | no | yes |
| Bulk archive | `.cache/bulk-archives/all-md.zip`, `data/bulk-archives.json` | derived | `build_bulk_archives.ts` | `/downloads/[file].ts` | no | yes |
| Build output | `dist/`, `.astro/`, Pagefind | build | Astro/Pagefind | host | no | yes |

The docs say the same high-level thing: source lives in `src/content`, generated public output in `dist`, and local library tools are not deploy-path tools ([docs/architecture.md](/Users/lr/projects/misc/pancratius/docs/architecture.md:18), [docs/architecture.md](/Users/lr/projects/misc/pancratius/docs/architecture.md:27), [docs/downloads.md](/Users/lr/projects/misc/pancratius/docs/downloads.md:5)).

**3. Import / Render / Build Model**

Direct answer: yes, steady-state DOCX conversion should be one DOCX on demand, but no, that should not mean keeping `docx_to_md.py` as the normal tool. The durable tool should be `scripts/import_docx.py`; `docx_to_md.py` is a legacy batch migration engine.

Evidence: `docx_to_md.py` is explicitly legacy batch conversion, still advertises `--kind project`, and can emit project Markdown ([scripts/docx_to_md.py](/Users/lr/projects/misc/pancratius/scripts/docx_to_md.py:8), [scripts/docx_to_md.py](/Users/lr/projects/misc/pancratius/scripts/docx_to_md.py:32), [scripts/docx_to_md.py](/Users/lr/projects/misc/pancratius/scripts/docx_to_md.py:2803)). `import_docx.py` is already one-DOCX import and preserves existing frontmatter where possible ([scripts/import_docx.py](/Users/lr/projects/misc/pancratius/scripts/import_docx.py:328), [scripts/import_docx.py](/Users/lr/projects/misc/pancratius/scripts/import_docx.py:380), [scripts/import_docx.py](/Users/lr/projects/misc/pancratius/scripts/import_docx.py:395)).

“Local library management” should mean: import DOCX, optimize DOCX, render PDF/EPUB/DOCX release artifacts, regenerate graphs, and review editorial metadata before committing. CI should build the static site, emit cheap `.md`/`.txt`/bulk Markdown derivatives, copy committed artifacts, run checks, and publish `dist`.

Current CI is not Node-only. The build scripts run `uv` for slug-map and graph-payload copy, then Node for bulk archives ([package.json](/Users/lr/projects/misc/pancratius/package.json:20)). Workflows install `uv` ([.github/workflows/build.yml](/Users/lr/projects/misc/pancratius/.github/workflows/build.yml:37), [.github/workflows/deploy.yml](/Users/lr/projects/misc/pancratius/.github/workflows/deploy.yml:27)). That is not fatal, but the contract should say “Node + uv lightweight static build” or the Python prebuilds should be moved to Node.

Import scripts should never run in CI. `--clean` should only delete scratch output or manifest-owned generated files. Current `docx_to_md.py --clean` deletes selected whole work bundles with `shutil.rmtree`, including projects ([scripts/docx_to_md.py](/Users/lr/projects/misc/pancratius/scripts/docx_to_md.py:3092)). That contradicts the stricter decision doc, which says destructive clean-room rebuilds should write to scratch or delete only manifest-owned files ([docs/decisions.md](/Users/lr/projects/misc/pancratius/docs/decisions.md:43)).

If Sergey gives a new project DOCX, it should first land in scratch or a draft import, then be curated into an author-owned project page. It should not be batch-converted into `src/content/projects` by default.

**4. Content / Page Model**

`src/content` should contain editorial source and committed release artifacts. `src/pages` should contain route orchestration and custom experiences. `data/` should contain corpus-wide generated data that is not author-facing page copy.

Astro’s own model supports this split: content collections are for related entries with shared structure, while a few distinct pages may be better as individual page components; endpoints in static sites are build-time generated files; Markdown images in `src` are processed/optimized; MDX can use components but requires MDX syntax and imports. Sources: [Astro content collections](https://docs.astro.build/en/guides/content-collections/), [Astro images](https://docs.astro.build/en/guides/images/), [Astro endpoints](https://docs.astro.build/en/guides/endpoints/), [Astro i18n](https://docs.astro.build/en/guides/internationalization/), [Astro MDX](https://docs.astro.build/en/guides/integrations-guide/mdx/).

`about`, `mission`, `svetozar`, and `license` are prose pages. `downloads` is prose plus a generated archive table. `support` is prose plus a structured donation model. `home`, `search`, and `conceptosphere` are application/structural pages, not the same kind of content page.

`StaticPage` is still recoverable, but it is drifting: it checks `mission` and `svetozar` by slug, renders about-specific portrait/facts, and support-specific channels ([src/components/StaticPage.astro](/Users/lr/projects/misc/pancratius/src/components/StaticPage.astro:31), [src/components/StaticPage.astro](/Users/lr/projects/misc/pancratius/src/components/StaticPage.astro:69), [src/components/StaticPage.astro](/Users/lr/projects/misc/pancratius/src/components/StaticPage.astro:102)). The generic `pages` schema has page-specific fields `portrait`, `facts`, and `channels` ([src/content.config.ts](/Users/lr/projects/misc/pancratius/src/content.config.ts:130)). That is a page-builder smell.

Recommendation for static pages:
| Page | Target model |
|---|---|
| `about` | Markdown body plus about-specific wrapper/data for portrait/facts |
| `mission` | Markdown + typed `prose: manifesto`, no slug check |
| `svetozar` | Markdown body; dedicated wrapper if terminal/dialog remains |
| `support` | Dedicated route/component plus support-specific data |
| `downloads` | Keep dedicated route rendering Markdown body plus archive table |
| `license` | Plain Markdown + `StaticPage` |

Options comparison:
| Option | Ergonomics | Drift risk | Type safety | Complexity | Agent maintainability | Escape hatch |
|---|---|---|---|---|---|---|
| Markdown + `StaticPage` | best for Sergey | low until special cases | low/medium | low | good | limited |
| Markdown + typed knobs | still good | low | medium | low | good | good |
| Dedicated Astro route + Markdown body | medium | low | high | medium | good | strong |
| MDX | worse for Sergey | medium | medium | medium/high | mixed | very strong |
| `components: Component[]` | poor without CMS | high | theoretical | high | poor | bad page-builder |

Do not adopt MDX repo-wide. MDX would mean adding `@astrojs/mdx`, changing loaders to `**/*.{md,mdx}`, writing localized `.mdx` entries, importing components in content, and passing component maps through `render()`. That helps engineers avoid raw HTML, but makes Sergey edit JSX-like syntax. Use it only for engineer-owned special pages if truly needed.

Future `/videos/` should be a new typed collection, not a generic page-builder: `src/content/videos/<key>/<lang>.md` with fields like video URL/embed ID, thumbnail, date, duration, transcript/body, and dedicated `/videos/` routes.

**5. Localization Model**

UI strings should live in one typed i18n/copy layer. Page-specific editorial copy should live in Markdown/frontmatter. Route metadata should be generated consistently: `seoForWork` from work frontmatter, `seoForPage` from page frontmatter, structural SEO from the same i18n dictionaries as UI copy.

Current localization is only partly coherent. There is a good central `copy.ts` ([src/lib/copy.ts](/Users/lr/projects/misc/pancratius/src/lib/copy.ts:12)), but nav/footer strings live in `i18n.ts` ([src/lib/i18n.ts](/Users/lr/projects/misc/pancratius/src/lib/i18n.ts:125)), structural SEO strings live in `seo.ts` ([src/lib/seo.ts](/Users/lr/projects/misc/pancratius/src/lib/seo.ts:88)), poetry routes hardcode page copy ([src/pages/poetry/index.astro](/Users/lr/projects/misc/pancratius/src/pages/poetry/index.astro:57), [src/pages/poetry/[slug]/index.astro](/Users/lr/projects/misc/pancratius/src/pages/poetry/[slug]/index.astro:43)), 404 is Russian-only ([src/pages/404.astro](/Users/lr/projects/misc/pancratius/src/pages/404.astro:5)), and `SvetozarTerminal` carries its own local copy dictionary ([src/components/SvetozarTerminal.astro](/Users/lr/projects/misc/pancratius/src/components/SvetozarTerminal.astro:25)).

Adding a third language today is not config-only. It touches `Locale`, content schemas, Astro config, slug maps, routing folders, sitemap regexes, string dictionaries, and scripts ([src/lib/i18n.ts](/Users/lr/projects/misc/pancratius/src/lib/i18n.ts:8), [src/content.config.ts](/Users/lr/projects/misc/pancratius/src/content.config.ts:5), [astro.config.ts](/Users/lr/projects/misc/pancratius/astro.config.ts:171)). The architecture should introduce a single locale registry with default locale, path prefix, labels, OG locale, and copy coverage checks.

**6. Assets / Downloads Model**

Canonical work images belong in the work bundle. Covers are editorial source assets. Body images are source assets under `images/**`. Public `/assets/...` URLs are derivative stable URLs for downloads, not author-maintained `public/` files. This matches the docs ([docs/architecture.md](/Users/lr/projects/misc/pancratius/docs/architecture.md:31), [docs/decisions.md](/Users/lr/projects/misc/pancratius/docs/decisions.md:36)).

HTML body images are handled by Astro’s Markdown image path and become optimized `_astro` assets. Public Markdown image URLs go through the static `/assets/<kind>/<work>/images/<file>` route ([src/lib/body-images.ts](/Users/lr/projects/misc/pancratius/src/lib/body-images.ts:41)). That route emits source bytes, not optimized derivatives ([src/lib/body-images.ts](/Users/lr/projects/misc/pancratius/src/lib/body-images.ts:82)). That is acceptable for stable corpus URLs, but it must be named as a tradeoff.

There is one canonical TS public-Markdown renderer for per-work `.md`, `.txt`, and bulk archive ([src/lib/downloads.ts](/Users/lr/projects/misc/pancratius/src/lib/downloads.ts:106), [scripts/build_bulk_archives.ts](/Users/lr/projects/misc/pancratius/scripts/build_bulk_archives.ts:146)). It strips frontmatter ([src/lib/public-markdown.ts](/Users/lr/projects/misc/pancratius/src/lib/public-markdown.ts:27)). Python `render_downloads.py` has a separate local Pandoc scratch cleaner for PDF/EPUB/DOCX ([scripts/render_downloads.py](/Users/lr/projects/misc/pancratius/scripts/render_downloads.py:109), [scripts/render_downloads.py](/Users/lr/projects/misc/pancratius/scripts/render_downloads.py:270)). That duplication is tolerable only if documented/tested as separate sinks.

The public Markdown renderer is not good enough for projects. Project source uses structured HTML sections and definition lists ([src/content/projects/enlightened-ai/en.md](/Users/lr/projects/misc/pancratius/src/content/projects/enlightened-ai/en.md:27), [src/content/projects/enlightened-ai/en.md](/Users/lr/projects/misc/pancratius/src/content/projects/enlightened-ai/en.md:64)); the renderer strips unknown HTML tags generically ([src/lib/public-markdown.ts](/Users/lr/projects/misc/pancratius/src/lib/public-markdown.ts:134)). The current bulk archive confirms the EN project files contain Russian text and flattened Q&A structure.

PDF/EPUB/DOCX rendering is correctly local/admin, not CI ([scripts/render_downloads.py](/Users/lr/projects/misc/pancratius/scripts/render_downloads.py:7)). The script stages body images and preserves poem lineation via `+hard_line_breaks` ([scripts/render_downloads.py](/Users/lr/projects/misc/pancratius/scripts/render_downloads.py:238), [scripts/render_downloads.py](/Users/lr/projects/misc/pancratius/scripts/render_downloads.py:276)). EPUB stylesheet is optional, but no `epub.css` currently exists, so EPUB styling is minimal by absence ([scripts/render_downloads.py](/Users/lr/projects/misc/pancratius/scripts/render_downloads.py:319)).

`/downloads/` should offer one production archive: `all-md.zip`, containing both RU and EN public Markdown in one archive. That is what the docs and script implement ([docs/downloads.md](/Users/lr/projects/misc/pancratius/docs/downloads.md:112), [scripts/build_bulk_archives.ts](/Users/lr/projects/misc/pancratius/scripts/build_bulk_archives.ts:141)). Bulk PDF/EPUB should stay off-host.

**7. Brittleness Findings**

| Severity | Class | Finding |
|---:|---|---|
| 1 | production bug + source-of-truth violation | `/en/projects/*` claim English but contain Russian descriptions/body; `docx_to_md.py` generates EN project pages from one RU DOCX ([src/content/projects/enlightened-ai/en.md](/Users/lr/projects/misc/pancratius/src/content/projects/enlightened-ai/en.md:7), [scripts/docx_to_md.py](/Users/lr/projects/misc/pancratius/scripts/docx_to_md.py:2859)). |
| 2 | source-of-truth violation | `docx_to_md.py --clean` deletes whole selected work bundles, including author-added files, not just manifest-owned files ([scripts/docx_to_md.py](/Users/lr/projects/misc/pancratius/scripts/docx_to_md.py:3097)). |
| 3 | architecture smell | Projects are modeled as full `WorkKind` with downloads, crossrefs, conversion, graph participation, and batch ownership. The schema enforces this ([src/content.config.ts](/Users/lr/projects/misc/pancratius/src/content.config.ts:92)). |
| 4 | production bug | EN project pages violate the documented rule “Never render Russian body content under an `/en/...` work URL” ([docs/i18n-routing.md](/Users/lr/projects/misc/pancratius/docs/i18n-routing.md:64)). |
| 5 | architecture smell | Locale support is hardcoded to RU/EN in TypeScript, Astro config, and scripts ([src/lib/i18n.ts](/Users/lr/projects/misc/pancratius/src/lib/i18n.ts:8), [scripts/build_slug_map.py](/Users/lr/projects/misc/pancratius/scripts/build_slug_map.py:60)). |
| 6 | architecture smell | Kind-to-segment mapping is repeated across app and scripts; the audit encodes the duplication instead of removing it ([scripts/audit/kind_segments.py](/Users/lr/projects/misc/pancratius/scripts/audit/kind_segments.py:17)). |
| 7 | source-of-truth violation | `editorial.yaml` is explicitly temporary but still load-bearing; it even references a nonexistent `docx_to_md.py --all` command ([editorial.yaml](/Users/lr/projects/misc/pancratius/editorial.yaml:1), [editorial.yaml](/Users/lr/projects/misc/pancratius/editorial.yaml:15)). |
| 8 | mirror/base bug risk | Public Markdown image URLs use `https://pancratius.ru/assets/...` and ignore Astro `base`; HTML uses `sameSitePath`, but corpus downloads do not ([src/lib/public-markdown.ts](/Users/lr/projects/misc/pancratius/src/lib/public-markdown.ts:100), [src/lib/paths.ts](/Users/lr/projects/misc/pancratius/src/lib/paths.ts:8)). |
| 9 | page-model smell | Generic `pages` schema contains `portrait`, `facts`, and `channels`, while `StaticPage` makes slug-based styling decisions ([src/content.config.ts](/Users/lr/projects/misc/pancratius/src/content.config.ts:130), [src/components/StaticPage.astro](/Users/lr/projects/misc/pancratius/src/components/StaticPage.astro:31)). |
| 10 | acceptable-but-watch | Graph payloads include poem/project refs inside a books graph, but adapters filter to books where required ([data/pancratius-books-graph.json](/Users/lr/projects/misc/pancratius/data/pancratius-books-graph.json:428), [src/lib/conceptosphere.ts](/Users/lr/projects/misc/pancratius/src/lib/conceptosphere.ts:235)). |
| 11 | harmless/local detail | Bulk archive script has unused `--publish` path while the static endpoint serves from `.cache` ([scripts/build_bulk_archives.ts](/Users/lr/projects/misc/pancratius/scripts/build_bulk_archives.ts:187), [src/pages/downloads/[file].ts](/Users/lr/projects/misc/pancratius/src/pages/downloads/[file].ts:31)). |
| 12 | scattered-copy smell | `copy.ts` is good, but SEO, poetry, 404, and component-local strings still bypass it. |

Graph payloads do not appear to contain stale `legacy/` cover paths; the graph code resolves localized titles/hrefs through work pairs ([src/components/conceptosphere/page-data.ts](/Users/lr/projects/misc/pancratius/src/components/conceptosphere/page-data.ts:91)). This area is mostly production-safe.

**8. Recommended Target Architecture**

Keep three layers strict:

1. Import layer: one DOCX in, draft/canonical Markdown and source assets out. Local only. No CI. No project overwrites.
2. Release layer: PDF/EPUB/DOCX artifacts rendered locally from canonical content, committed beside the work.
3. Site layer: Astro static build, cheap `.md`/`.txt`/bulk archive derivation, graph payload copy, Pagefind, sitemap, publish `dist`.

Make `book` and `poem` the converted corpus model. Keep `project` only if it truly remains a work with downloadable alternate representations; otherwise split it into an authored project/page model. Future `video` should be its own collection.

Keep `StaticPage`, but reduce it to generic prose. Add a small typed presentation field if needed: `prose: default | manifesto | bio | svet`, `toc: true | false`. Move support channels to a support model. Do not introduce `components: Component[]`.

Centralize locale configuration and UI/SEO copy. Add third-language support by adding a locale entry, not by touching every route and regex.

Centralize kind/segment metadata in one generated/shared manifest or one TS module plus small generated Python JSON. Do not keep auditing duplicated constants as the long-term solution.

**9. Migration Plan**

1. First: hard-disable `docx_to_md.py` writing `src/content/projects`. This prevents data loss before any refactor.
2. Fix the EN project pages: either remove them until translated, or make them real English content. This is production-facing.
3. Replace slug checks in `StaticPage` with typed frontmatter knobs; split `support` into a dedicated route/data model.
4. Move scattered UI/SEO strings into one i18n copy layer. Start with poetry, 404, `HeadMeta`, and `SvetozarTerminal`.
5. Decide whether projects are works or authored pages. Then update downloads/archive/public Markdown behavior accordingly.
6. Replace duplicated kind/segment constants with one shared route metadata source.
7. Make public Markdown URL policy explicit: canonical primary-domain URLs vs mirror-relative URLs. Then encode that policy.
8. Bake `editorial.yaml` into content frontmatter and delete the temporary file.
9. Retire `docx_to_md.py` after migration, or leave it fenced as `legacy_batch_import.py` that only writes scratch output.

Do not touch yet: graph algorithms, visual graph runtime, PDF/EPUB renderer internals, Pagefind, or the basic static endpoint dispatch. Those are not the cause of the drift.

Changes requiring Sergey/editorial approval: project EN content, project taxonomy, support/payment data, EN title finalization, any decision to expose or remove project downloads, future video content fields.

**10. Open Questions**

Sergey/editorial:
- Are projects meant to be downloadable corpus works, or authored site/domain pages?
- Should current EN project pages be removed until translated, or translated now?
- Are `editorial.yaml` English titles final enough to bake into `en.md`?
- Should public Markdown downloads use canonical `pancratius.ru` image URLs, even on mirrors?

Engineering:
- Is “Node-only CI” a hard requirement, or is “Node + uv, no pandoc/typst/ML” acceptable?
- Should body-image `/assets/...` emit every file in `images/**`, or only images referenced by Markdown?
- Should future videos have per-video downloadable text/transcript artifacts, or just page content plus embedded media?
