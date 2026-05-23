// Import boundary (docs/audit-harness.md → "PAN015: Retired Capability Surface",
// "PAN003: Single Sources Of Truth"). The import CLI converts corpus WORKS only;
// "work kinds" has one source of truth — `WORK_KINDS` in scripts/lib/kinds.py.
//
// PAN017 — import work-kinds guard. Asserts, by deriving from the SoT rather than
// restating it (PAN003):
//   1. scripts/import_docx.py's `--kind` argparse `choices` == `WORK_KINDS`;
//   2. `"project" not in WORK_KINDS` — projects are themed sections, not works
//      (PAN004), so re-adding `project` to the import surface is the exact
//      retired-capability regression PAN015 forbids;
//   3. `WORK_KINDS` ⊆ `SEGMENT_OF` keys — every work kind still routes
//      (`SEGMENT_OF` deliberately also carries `project` for routing).
//
// The detection lives in the Python checker (it must import kinds.py and parse
// the CLI's argparse); this TS rule owns the severity and the contract prose and
// wraps it via runPythonCheck, the same shape as PAN004-duplicate-identity.

import type { Rule, RuleContext } from "../lib/rule.ts";
import type { Finding } from "../lib/finding.ts";
import { runPythonCheck } from "../lib/python.ts";

const CATEGORY = "import-boundary";

export const pan017ImportWorkKinds: Rule = {
  id: "PAN017-import-work-kinds",
  title:
    "PAN017: the import CLI's --kind choices must equal WORK_KINDS, project must not be a work kind, and WORK_KINDS ⊆ SEGMENT_OF",
  tier: "core",
  run(ctx: RuleContext): Finding[] {
    return runPythonCheck(ctx, {
      id: "PAN017-import-work-kinds",
      category: CATEGORY,
      severity: "fatal",
      script: "python/import_work_kinds.py",
      contract:
        "The import CLI converts corpus WORKS only. Work kinds have one source of truth — `WORK_KINDS` in scripts/lib/kinds.py. scripts/import_docx.py's `--kind` argparse `choices` must equal `WORK_KINDS`; `project` must NOT be in `WORK_KINDS` (projects are themed sections, not works — PAN004); and `WORK_KINDS` must be a subset of `SEGMENT_OF`'s keys (every work kind still routes; SEGMENT_OF deliberately keeps `project` for routing).",
      why: "Re-admitting `project` as an importable kind, or hardcoding the import CLI's --kind list so it drifts from WORK_KINDS, is the retired-capability regression PAN015 forbids: the converter could write authored project sections through work machinery, and the import surface would stop matching the corpus definition.",
      repair:
        "Keep `WORK_KINDS = (\"book\", \"poem\")` as the SoT in scripts/lib/kinds.py and have scripts/import_docx.py use `choices=WORK_KINDS` (imported from lib.kinds), not a literal list. If a kind is genuinely promoted to a work, add it to WORK_KINDS (and SEGMENT_OF) — do not special-case it in the CLI.",
      doNotFixBy:
        "Hardcoding the --kind choices to silence the parity check, or adding `project` back to WORK_KINDS to make projects \"fit\" the work/import machinery instead of keeping them a section.",
    });
  },
};
