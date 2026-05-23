// The rule registry — the one explicit list of every rule the harness runs.
// Adding a rule is two lines: import it, append it. No auto-discovery, no plugin
// framework (docs/audit-harness.md → "Implementation Shape"): the list is
// greppable and a future agent can read it top to bottom.

import type { Rule } from "../lib/rule.ts";

import { rule as proofTs } from "./_example.ts";
import { rule as proofPython } from "./_example_python.ts";

export const RULES: readonly Rule[] = [
  // SCAFFOLD PROOFS — remove when PAN002 lands (see the rule files).
  proofTs,
  proofPython,
];
