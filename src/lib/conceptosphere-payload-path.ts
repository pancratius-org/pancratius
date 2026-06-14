// Where a conceptosphere graph payload lives on disk, per locale.
//
// The default (RU) locale reads the un-suffixed SOURCE graph under `data/`.
// A non-default locale reads the per-locale payload the build join already
// emitted under `public/data/` — `build/copy-graph-payloads.ts` writes
// `pancratius-*-graph.<locale>.json` = RU topology ⋈ the authored overlay.
//
// That build-time join is the ONLY bridge (conceptosphere-bilingual-design.md
// §2): a consumer reads its output, it never re-joins. The desktop graph fetches
// the same `.<locale>.json`, so the server-rendered mobile list and the client
// graph render identical labels. A missing localized payload throws fail-loud —
// no silent RU fallback under an English URL; `npm run generate` emits it before
// any render.
//
// Pure (node:fs/node:path + the locale list) so it carries no `astro:content`
// dependency and is directly unit-testable apart from the heavy graph adapter.

import { existsSync } from "node:fs";
import { resolve as resolvePath } from "node:path";

import { DEFAULT_LOCALE, type Locale } from "./locales.ts";

export function graphPayloadPath(name: string, locale: Locale, root: string): string {
  if (locale === DEFAULT_LOCALE) return resolvePath(root, "data", `${name}.json`);
  const localized = resolvePath(root, "public", "data", `${name}.${locale}.json`);
  if (!existsSync(localized)) {
    throw new Error(
      `conceptosphere ${locale} payload ${name}.${locale}.json is missing — run \`npm run generate\` ` +
        "to emit the build-time RU⋈overlay join before rendering (a missing localized payload is a build " +
        "failure, not a silent RU fallback under an English URL)",
    );
  }
  return localized;
}
