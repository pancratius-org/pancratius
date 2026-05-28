// Download / corpus-export contract (docs/audit-harness.md → "PAN008: Download
// And Corpus Export Contract", docs/downloads.md). A `post-build`-tier rule:
// it needs an emitted `dist/`, so it runs only on `npm run audit:post-build`,
// never on the fast PR gate.
//
// PAN008 wraps the existing, production-proven post-build check
// (python/download_asset_urls.py): it scans the public Markdown exports in
// `dist/` and the bundled `dist/downloads/all-md.zip` for local image URLs that
// are NOT work-scoped `/assets/…` URLs. The TS side owns severity and the
// contract/why/repair prose; the script owns the detection and prints the
// offending URLs. Its non-zero exit becomes the finding via runPythonCheck.

import type { Rule, RuleContext } from "../lib/rule.ts";
import type { Finding } from "../lib/finding.ts";
import { runPythonCheck } from "../lib/python.ts";

/** PAN008: public Markdown exports must reference work-scoped /assets/… URLs. */
export const pan008PublicMarkdownAssets: Rule = {
  id: "PAN008-public-md-asset-urls",
  title:
    "PAN008: public Markdown exports (and all-md.zip) must reference work-scoped /assets/… image URLs, never local/legacy/relative paths",
  tier: "post-build",
  run(ctx: RuleContext): Finding[] {
    return runPythonCheck(ctx, {
      id: "PAN008-public-md-asset-urls",
      category: "download-export",
      severity: "fatal",
      script: "python/download_asset_urls.py",
      contract:
        "Public Markdown exports must reference work-scoped /assets/… URLs for local images, never machine-local, legacy, or relative image paths. This holds for both the per-file `.md` emitted into dist/ and every `.md` inside dist/downloads/all-md.zip.",
      why: "The published .md (and the all-md.zip corpus archive) is consumed off-site — LLM training sets, mirrors, third-party readers — where the repo's directory layout and dev server do not exist, so a non-/assets/ image URL is dead on arrival: it resolves to nothing for every off-site consumer.",
      repair:
        "Regenerate the public Markdown through the canonical renderer so every local image URL is rewritten to its work-scoped /assets/… form. The check prints each offending file:url pair.",
      doNotFixBy:
        "Hand-editing the emitted file in dist/ — the next build overwrites it; fix the renderer so the export is correct at the source.",
    });
  },
};
