// SCAFFOLD PROOF RULE — pure-TS path. This is not a real contract: it exists only
// to prove the skeleton end-to-end (a TS rule reads the tree, emits a full-shaped
// finding, gates CI, and self-tests in both polarities). Delete it together with
// _example_python.ts and their fixtures once the first real rule (PAN002) lands.
//
// It is also the minimal worked example a future agent copies: read files via the
// context, return findings, keep it pure.

import type { Rule, RuleContext } from "../lib/rule.ts";
import type { Finding } from "../lib/finding.ts";

const ID = "PAN000-proof-ts";
const MARKER = "AUDIT_PROOF_BAD";

export const rule: Rule = {
  id: ID,
  title: `Scaffold proof (TS): the marker file ${MARKER} must not exist`,
  tier: "core",
  run(ctx: RuleContext): Finding[] {
    const findings: Finding[] = [];
    for (const rel of ctx.walk()) {
      if (rel.split("/").pop() !== MARKER) continue;
      findings.push({
        rule: ID,
        severity: "fatal",
        category: "proof",
        file: rel,
        observed: `scaffold marker file ${MARKER} is present in the tree`,
        contract: "This is a harness self-proof, not a product contract.",
        why: "If this fires on the real repo, the skeleton's file walk or fatal gating is wired wrong.",
        repair: `Delete ${rel}.`,
        doNotFixBy: "Adding it to an ignore list — the proof's whole point is that it can fire.",
      });
    }
    return findings;
  },
};
