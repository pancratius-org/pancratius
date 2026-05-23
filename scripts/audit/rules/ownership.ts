// Generated/authored ownership and the import/render/build split
// (docs/audit-harness.md → PAN005, PAN012). CI builds and publishes the site; it
// never manufactures the library. The library-management tooling (pandoc, typst,
// the embedding stack, DOCX optimizers, source importers/renderers) is local/admin
// work that mutates source or renders release artifacts — it must never run in CI.
//
// PAN012 is a thin wrapper over the Python check that parses the workflow YAML and
// scans each step's run:/uses: (not comments) for that banned tooling. PAN005
// (build steps mutating authored Markdown, --clean deleting a content kind, etc.)
// will be added here as deterministic members land, incident-first.

import type { Rule, RuleContext } from "../lib/rule.ts";
import type { Finding } from "../lib/finding.ts";
import { runPythonCheck } from "../lib/python.ts";

/** PAN012: CI workflows must not install or run library-management tooling. */
export const pan012CiSeparation: Rule = {
  id: "PAN012-ci-separation",
  title: "PAN012: CI must not install or run pandoc/typst/embedding/importer/renderer tooling",
  tier: "core",
  run(ctx: RuleContext): Finding[] {
    return runPythonCheck(ctx, {
      id: "PAN012-ci-separation",
      category: "import-render-build-split",
      severity: "fatal",
      script: "python/ci_separation.py",
      contract:
        "CI builds and publishes the site only; it never manufactures the library (architecture.md \"Shape\"; downloads.md \"CI Contract\"). A workflow step must not install or run pandoc, typst, the embedding stack, DOCX optimizers, or the source importers/renderers — those are local/admin activities that mutate source or render release artifacts.",
      why: "If CI renders or imports, the deploy pipeline depends on heavy local tooling (pandoc/typst/MLX) and can mutate or regenerate committed source — making the build non-reproducible and able to overwrite authored content. The split is what keeps CI a pure build-and-publish.",
      repair:
        "Run import/render/optimize/embedding locally via the library door (uv) and commit the results; CI only packages and publishes what is already in src/content/. Remove the offending install/run step from the workflow.",
      doNotFixBy:
        "Adding a pandoc/typst install step or invoking an importer/renderer in CI to 'just make the artifact in the pipeline' — that erases the import/render/build boundary.",
    });
  },
};
