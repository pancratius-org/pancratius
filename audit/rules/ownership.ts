// Generated/authored ownership and the import/render/build split
// (docs/audit-harness.md → PAN005, PAN012). CI validates, builds, and publishes the site; it
// never manufactures the library. The library-management tooling (pandoc, typst,
// the embedding stack, DOCX optimizers, source importers/renderers) is local/admin
// work that mutates source or renders release artifacts — it must never run in CI.
//
// PAN012 checks the workflow door: the exact CI tool subset, the two allowed
// locked mise entry points, disabled task auto-install, and direct banned commands.
// Mise itself owns transitive task discovery and graph resolution. PAN005 (build
// steps mutating authored Markdown, --clean deleting a content kind, etc.) will be
// added here as deterministic members land, incident-first.

import type { Rule, RuleContext } from "../lib/rule.ts";
import type { Finding } from "../lib/finding.ts";
import { runPythonCheck } from "../lib/python.ts";

/** PAN012: CI workflows must not install or run library-management tooling. */
export const pan012CiSeparation: Rule = {
  id: "PAN012-ci-separation",
  title:
    "PAN012: CI must not compile corpus records or run local library-management tooling",
  tier: "core",
  run(ctx: RuleContext): Finding[] {
    return runPythonCheck(ctx, {
      id: "PAN012-ci-separation",
      category: "import-render-build-split",
      severity: "fatal",
      script: "python/ci_separation.py",
      contract:
        "CI validates and publishes committed source; it never manufactures the library (architecture.md \"Shape\"; downloads.md \"CI Contract\"). Workflows may enter the repository task graph only through `mise --locked run verify` or `verify:content`. The mise action installs exactly Node, Python, and uv, and task auto-install stays disabled. Direct workflow commands must not compile intent-ai records, install or run pandoc/typst, invoke corpus mutation commands, or import the converter/IR/writer modules behind them.",
      why: "If CI renders or imports, the deploy pipeline depends on heavy local tooling (pandoc/typst/MLX) and can mutate or regenerate committed source — making the build non-reproducible and able to overwrite authored content. The split is what keeps CI a pure build-and-publish.",
      repair:
        "Move repository verification behind an allowed locked mise entry point. Run import/render/optimize/embedding locally through the library door and commit the results; CI only packages and publishes what is already in src/content/.",
      doNotFixBy:
        "Adding a pandoc/typst install step, invoking an importer/renderer, or `python -m`-running a converter/writer library module in CI to 'just make the artifact in the pipeline' — that erases the import/render/build boundary.",
    });
  },
};
