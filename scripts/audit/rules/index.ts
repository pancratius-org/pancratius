// The rule registry — the one explicit list of every rule the harness runs.
// Adding a rule is two lines: import it, append it. No auto-discovery, no plugin
// framework (docs/audit-harness.md → "Implementation Shape"): the list is
// greppable and a future agent can read it top to bottom.

import type { Rule } from "../lib/rule.ts";

import { rule as proofPython } from "./_example_python.ts";
import { rule as pan002 } from "./locales.ts";

export const RULES: readonly Rule[] = [
  // SCAFFOLD PROOF — Python path; remove when the first Python-backed rule lands.
  proofPython,
  pan002,
];
