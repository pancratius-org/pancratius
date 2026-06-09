// Typography role drift (docs/architecture.md → "Styling"). Shared typography
// roles are defined once in src/styles/typography.css; consumers reference them
// through var(). Re-typing a role's distinctive display value as a raw literal
// silently reintroduces the scatter the roles removed and drifts out of sync
// with the role.
//
// The rule DERIVES the watched values from typography.css (the `--type-*`
// clamp tokens) rather than hardcoding them (PAN003 "derive, do not restate"):
// adding a role automatically extends the guard. Only distinctive clamp display
// sizes are watched — plain numbers (0.98, leading/tracking) recur in honest
// local roles, so matching them would be noise.

import type { Rule, RuleContext } from "../lib/rule.ts";
import type { Finding } from "../lib/finding.ts";
import { typographyRoleDrift } from "../lib/css_values.ts";

const ID = "PAN020-typography-role-drift";

export const pan020TypographyRoleDrift: Rule = {
  id: ID,
  title: "PAN020: shared typography-role values are referenced via var(), not re-typed as raw literals",
  tier: "core",
  run(ctx: RuleContext): Finding[] {
    return typographyRoleDrift(ctx).flatMap((group) =>
      group.uses.map((use) => ({
        rule: ID,
        severity: "fatal" as const,
        category: "typography",
        file: use.file,
        line: use.line,
        observed: `${use.file}:${use.line} sets ${use.property}: ${group.value} — the raw literal of typography role ${group.token}`,
        contract: `Shared typography roles live once in src/styles/typography.css; consumers reference them via var() (architecture.md → Styling). A role's distinctive display value must not be re-typed as a raw literal elsewhere.`,
        why: `Re-typing a role's value reintroduces the scatter the roles removed: the copy drifts out of sync with the role, and future agents read the literal as a local value and spread it further.`,
        repair: `Replace ${group.value} with var(${group.token}).`,
        doNotFixBy: `Nudging the literal to dodge the match, or deleting the role token.`,
      })),
    );
  },
};
