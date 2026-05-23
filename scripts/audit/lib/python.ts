// The Python-subprocess normalizer. The existing content/corpus audits are
// Python and are NOT rewritten for purity (docs/tooling.md): the harness shells
// to them and turns their PASS/FAIL exit into the harness finding language. The
// TS side owns severity and the contract/why/repair prose; the script owns the
// detection and prints the evidence.
//
// Convention for a wrapped script: it must respect `PANCRATIUS_AUDIT_ROOT` (the
// tree to scan) so it can be pointed at a fixture, falling back to the real repo
// root when the env var is absent. Exit 0 = clean; non-zero = the contract is
// broken and stdout/stderr carry the evidence.

import { spawnSync } from "node:child_process";
import { join } from "node:path";

import type { Finding, Severity } from "./finding.ts";
import type { RuleContext } from "./rule.ts";
import { AUDIT_DIR } from "./repo.ts";

export interface PythonCheckSpec {
  /** Rule id carried onto the finding. */
  id: string;
  category: string;
  severity: Severity;
  /** Script path relative to scripts/audit, e.g. `python/locales.py`. */
  script: string;
  contract: string;
  why: string;
  repair: string;
  doNotFixBy?: string;
}

/**
 * Run a wrapped Python check against `ctx.root` and normalize its result.
 * Returns `[]` when the script exits 0, one finding (with the captured output as
 * `observed`) when it exits non-zero, and one finding when the subprocess itself
 * fails to run — a check that cannot execute is not a passing check.
 */
export function runPythonCheck(ctx: RuleContext, spec: PythonCheckSpec): Finding[] {
  const scriptPath = join(AUDIT_DIR, spec.script);
  // `--frozen`: a scanner must not mutate state. Without it `uv run` may resolve
  // and update the lockfile / install as a side effect; --frozen runs against the
  // committed uv.lock as-is and errors instead of touching it (CI's ruff/ty/pytest
  // already run --frozen). Keeps the harness a pure read-only verifier.
  const res = spawnSync("uv", ["run", "--frozen", "--quiet", scriptPath], {
    encoding: "utf-8",
    env: { ...process.env, PANCRATIUS_AUDIT_ROOT: ctx.root },
    maxBuffer: 32 * 1024 * 1024,
  });

  if (res.error || res.status === null) {
    return [
      {
        rule: spec.id,
        severity: spec.severity,
        category: spec.category,
        file: spec.script,
        observed: `python check failed to run: ${res.error?.message ?? "process did not exit normally"}`,
        contract: spec.contract,
        why: "A check that cannot execute gives false confidence; treat it as failing until it runs.",
        repair: "Ensure `uv` is installed and the wrapped script runs: uv run " + spec.script,
        doNotFixBy: "Removing the check from the harness to make the run pass.",
      },
    ];
  }

  if (res.status === 0) return [];

  const output = `${res.stdout ?? ""}${res.stderr ?? ""}`.trim();
  return [
    {
      rule: spec.id,
      severity: spec.severity,
      category: spec.category,
      file: spec.script,
      observed: output === "" ? `exit ${res.status}` : output,
      contract: spec.contract,
      why: spec.why,
      repair: spec.repair,
      doNotFixBy: spec.doNotFixBy,
    },
  ];
}
