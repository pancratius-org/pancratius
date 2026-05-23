// SCAFFOLD PROOF RULE — Python-subprocess path. Proves the normalizer end-to-end:
// a TS rule shells to a Python check via runPythonCheck, the check honours
// PANCRATIUS_AUDIT_ROOT so it runs against a fixture, and its non-zero exit
// becomes a full-shaped finding. Delete with _example.ts once PAN002 lands.

import type { Rule, RuleContext } from "../lib/rule.ts";
import type { Finding } from "../lib/finding.ts";
import { runPythonCheck } from "../lib/python.ts";

const ID = "PAN000-proof-py";

export const rule: Rule = {
  id: ID,
  title: "Scaffold proof (Python): the marker file AUDIT_PROOF_PY_BAD must not exist",
  tier: "core",
  run(ctx: RuleContext): Finding[] {
    return runPythonCheck(ctx, {
      id: ID,
      category: "proof",
      severity: "fatal",
      script: "python/_example_check.py",
      contract: "This is a harness self-proof of the Python-subprocess path, not a product contract.",
      why: "If this fires on the real repo, the Python normalizer or its root override is wired wrong.",
      repair: "Delete the marker file the wrapped check reports.",
      doNotFixBy: "Making the wrapped script exit 0 unconditionally.",
    });
  },
};
