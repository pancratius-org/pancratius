// Import boundary (docs/audit-harness.md → "PAN015: Retired Capability Surface",
// "PAN003: Single Sources Of Truth"). `pancratius work import` converts corpus WORKS only;
// convertible corpus work kinds have one source of truth —
// `CORPUS_WORK_KINDS` in pancratius/kinds.py.
//
// PAN017 — import work-kinds guard. Asserts, by deriving from the SoT rather than
// restating it (PAN003):
//   1. pancratius/cli.py's `work import --kind` argparse `choices` derives from `CORPUS_WORK_KINDS`;
//   2. `"project" not in CORPUS_WORK_KINDS` — projects are themed sections, not works
//      (PAN004), so re-adding `project` to the import surface is the exact
//      retired-capability regression PAN015 forbids;
//   3. `CORPUS_WORK_KINDS` ⊆ `SEGMENT_OF` keys — every work kind still routes
//      (`SEGMENT_OF` deliberately also carries `project` for routing);
//
// The detection lives in the Python checker (it must import kinds.py and parse
// the public CLI's argparse); this TS rule owns the severity and the
// contract prose and wraps it via runPythonCheck, the same shape as PAN004.

import type { Rule, RuleContext } from "../lib/rule.ts";
import type { Finding } from "../lib/finding.ts";
import { runPythonCheck } from "../lib/python.ts";

const CATEGORY = "import-boundary";

export const pan017ImportWorkKinds: Rule = {
  id: "PAN017-import-work-kinds",
  title:
    "PAN017: work import --kind choices must derive from CORPUS_WORK_KINDS, project must not be a work kind, and CORPUS_WORK_KINDS ⊆ SEGMENT_OF",
  tier: "core",
  run(ctx: RuleContext): Finding[] {
    return runPythonCheck(ctx, {
      id: "PAN017-import-work-kinds",
      category: CATEGORY,
      severity: "fatal",
      script: "python/import_work_kinds.py",
      contract:
        "`pancratius work import` converts corpus WORKS only. Convertible corpus work kinds have one source of truth — `CORPUS_WORK_KINDS` in pancratius/kinds.py. pancratius/cli.py's `work import --kind` argparse `choices` must derive from `CORPUS_WORK_KINDS`; `project` must NOT be in `CORPUS_WORK_KINDS` (projects are themed sections, not works — PAN004); and `CORPUS_WORK_KINDS` must be a subset of `SEGMENT_OF`'s keys (every work kind still routes; SEGMENT_OF deliberately keeps `project` for routing).",
      why: "Re-admitting `project` as an importable kind, or hardcoding the public import command's --kind list so it drifts from CORPUS_WORK_KINDS, is the retired-capability regression PAN015 forbids: the converter could write authored project sections through work machinery, and the import surface would stop matching the corpus definition.",
      repair:
        "Keep `CORPUS_WORK_KINDS = (\"book\", \"poem\")` as the SoT in pancratius/kinds.py, and have pancratius/cli.py declare `work import --kind` with choices derived from CORPUS_WORK_KINDS. If a kind is genuinely promoted to a work, add it to CORPUS_WORK_KINDS (and SEGMENT_OF) — do not special-case it in the CLI.",
      doNotFixBy:
        "Hardcoding the --kind choices to silence the parity check, or adding `project` back to CORPUS_WORK_KINDS to make projects \"fit\" the work/import machinery instead of keeping them a section.",
    });
  },
};

// PAN019 — CLI door verify-boundary. The two-doors split (docs/tooling.md) cuts on
// mutate vs verify: the `pancratius` console-script MUTATES the corpus; verification
// (`check`/`test`/`audit`) is the npm site door's job. So the door must register no
// sub-parser named in the site-door verb family (the `site` proxy plus check/test/
// audit/build/dev/preview) — name-bound by nature (a verb's semantics aren't static),
// so it bars the whole family. The Python checker AST-scans pancratius/cli.py.
export const pan019CliVerifyBoundary: Rule = {
  id: "PAN019-cli-verify-boundary",
  title: "PAN019: the pancratius CLI door exposes no site-door verb (no audit/check/test/build/dev/preview verb, no `site` proxy)",
  tier: "core",
  run(ctx: RuleContext): Finding[] {
    return runPythonCheck(ctx, {
      id: "PAN019-cli-verify-boundary",
      category: CATEGORY,
      severity: "fatal",
      script: "python/cli_verify_boundary.py",
      contract:
        "The two-doors split (docs/tooling.md) cuts on what a command does to the world: `pancratius` MUTATES the corpus, while the npm site door BUILDS and VERIFIES it. So `pancratius/cli.py` must register NO argparse sub-parser (at any nesting level) named in the site-door verb family — the `site` proxy group, the verify verbs (`audit`, `check`, `test`), or the build/serve verbs (`build`, `dev`, `preview`). Discoverability of `npm run audit:repo` is a `--help`/skills-doc concern, not a routing one.",
      why: "A `pancratius site audit → npm run audit:repo` proxy (or any verify/build verb) inverts the doc's mutate/verify cut at the grammar level: it puts a site-door command under the mutate door, the exact `site`-proxy alternative tooling.md rejected. Barring the whole family keeps the seam CI-enforced instead of convention-only, and catches an accidental `check`/`build` door verb, not just `audit`/`site`.",
      repair:
        "Keep build+verify under `npm` (`npm run build`, `npm run audit:repo`, `astro check`, Playwright). The `pancratius` door only grows MUTATE verbs (import, scaffold, render, optimize, data generation); point users at the npm verbs from the skills doc and `--help`, not a proxy verb.",
      doNotFixBy:
        "Adding a `pancratius site audit`/`pancratius audit`/`pancratius check` convenience wrapper that shells to npm — that is the rejected `site` proxy; it re-creates a second surface for a site-door command under the wrong door.",
    });
  },
};

// PAN018 — writer-only-mutation guard. Import's safety boundary
// (docs/import-pipeline.md): import code *produces* a WritePlan; only the writer
// (pancratius/writer.py) mutates src/content. Every other import module that
// carries the marker `# import-pure: no filesystem mutation` must contain NO
// filesystem-mutation call. The scanned set is DERIVED from the markers (a
// self-extending SoT — later phases mark the parser/normalizer/lowerer and they
// are covered automatically), not hardcoded. writeplan.py carries the marker;
// writer.py deliberately does NOT (it is the designated mutator).
//
// The detection lives in the Python checker (it AST-parses each marked module);
// this TS rule owns the severity and the contract prose and wraps it via
// runPythonCheck, the same shape as PAN017.

export const pan018WriterOnlyMutation: Rule = {
  id: "PAN018-writer-only-mutation",
  title:
    "PAN018: modules marked `# import-pure: no filesystem mutation` must contain no filesystem-mutation calls (only the writer mutates src/content)",
  tier: "core",
  run(ctx: RuleContext): Finding[] {
    return runPythonCheck(ctx, {
      id: "PAN018-writer-only-mutation",
      category: CATEGORY,
      severity: "fatal",
      script: "python/writer_only_mutation.py",
      contract:
        "Import's safety boundary (docs/import-pipeline.md): import code produces a WritePlan; only the writer (pancratius/writer.py) mutates src/content. A module declares it is in the pure boundary with the marker comment `# import-pure: no filesystem mutation`, and every such module must contain NO filesystem-mutation call (.write_text/.write_bytes/.mkdir/.touch, shutil.copy*/move/rmtree, os.replace/remove/rename/unlink/makedirs, or open(..., write-mode)). The scanned set is derived FROM the markers, not hardcoded.",
      why: "If a marked-pure import module (writeplan, and later the parser/normalizer/lowerer) can quietly write or copy into src/content, the single-mutator boundary has leaked — exactly the old shape where parsing copies media into a work folder as a side effect and the WritePlan/dry-run/overwrite guarantees no longer hold.",
      repair:
        "Move the filesystem mutation into pancratius/writer.py (the designated mutator) and have the pure module return a WritePlan/WriteOp describing the intended write instead. If a module legitimately mutates the filesystem, it is not pure — remove its `import-pure` marker (and route its writes through the writer).",
      doNotFixBy:
        "Deleting the `# import-pure` marker just to silence the scan while keeping the write, or special-casing the offending call so the AST check misses it.",
    });
  },
};
