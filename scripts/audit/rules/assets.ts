// Assets and images (docs/audit-harness.md → PAN007). Authored assets must
// resolve where the content references them; a Markdown image that doesn't exist
// on disk renders broken in dev, preview, the static deploy, and public Markdown.
//
// PAN007 wraps the existing, production-proven content image audit, which checks
// that every cover/body-image reference resolves, that work Markdown uses
// `![](...)` rather than raw `<img>`, and that body-image Markdown stands on its
// own line. (Asset-role classification — bibliography thumbnail vs body image,
// public-Markdown URL shape — is covered by the manifest + PAN008 deploy checks.)

import type { Rule, RuleContext } from "../lib/rule.ts";
import type { Finding } from "../lib/finding.ts";
import { runPythonCheck } from "../lib/python.ts";

/** PAN007: every image reference in src/content/ resolves on disk. */
export const pan007AssetRefs: Rule = {
  id: "PAN007-asset-refs",
  title: "PAN007: every cover/body-image reference in src/content/ must resolve on disk",
  tier: "core",
  run(ctx: RuleContext): Finding[] {
    return runPythonCheck(ctx, {
      id: "PAN007-asset-refs",
      category: "asset-refs",
      severity: "fatal",
      script: "python/media_refs.py",
      contract:
        "Authored assets live with their work and are referenced by relative path; every `cover:` and `![](./images/…)` reference must resolve on disk. Work Markdown uses `![](...)`, not raw `<img>`, and a body image stands on its own line.",
      why: "A Markdown image that doesn't resolve renders broken everywhere the body is served — dev, preview, the static deploy, and the public Markdown export — and a raw `<img>` or inline image bypasses the asset pipeline and public-URL rewriting.",
      repair:
        "Fix the reference to point at the co-located asset (or add the missing file to the work bundle); convert raw `<img>` to `![](...)`; put a body image on its own line. The check prints each unresolved reference.",
      doNotFixBy:
        "Pointing the reference at a machine-local or legacy path, or moving the asset to public/ just to get a URL — assets stay co-located with the work.",
    });
  },
};
