#!/usr/bin/env -S node --experimental-strip-types
// The audit runner (`npm run audit`). Selects rules by tier for the requested
// mode, runs each pure scanner against the repo root, prints the report, and
// exits non-zero only when a fatal finding is present. See docs/audit-harness.md
// and docs/tooling.md (audit is the site door: `npm run audit` is canonical).

import type { Finding } from "./lib/finding.ts";
import type { Rule, Tier } from "./lib/rule.ts";
import { makeContext } from "./lib/rule.ts";
import { REPO_ROOT } from "./lib/repo.ts";
import { renderReport, hasFatal } from "./lib/report.ts";
import { RULES } from "./rules/index.ts";

type Mode = "default" | "agent" | "deploy";

interface ModeConfig {
  tiers: ReadonlySet<Tier>;
  showInfo: boolean;
  title: string;
}

const MODES: Record<Mode, ModeConfig> = {
  // PR gate: fast deterministic core only; fatals gate CI.
  default: { tiers: new Set<Tier>(["core"]), showInfo: false, title: "Pancratius audit" },
  // Agent view: core + non-blocking heuristics, everything shown grouped.
  agent: { tiers: new Set<Tier>(["core", "heuristic"]), showInfo: true, title: "Pancratius audit (agent)" },
  // Post-build crawl/index checks; need an emitted dist/.
  deploy: { tiers: new Set<Tier>(["deploy"]), showInfo: false, title: "Pancratius audit (deploy)" },
};

function parseMode(argv: readonly string[]): Mode {
  const arg = argv[2];
  if (arg === undefined || arg === "default") return "default";
  if (arg === "agent" || arg === "deploy") return arg;
  process.stderr.write(`unknown audit mode: ${arg}\nusage: harness.ts [default|agent|deploy]\n`);
  process.exit(2);
}

async function main(): Promise<void> {
  const mode = parseMode(process.argv);
  const config = MODES[mode];
  const ctx = makeContext(REPO_ROOT);
  const selected: readonly Rule[] = RULES.filter((r) => config.tiers.has(r.tier));

  const findings: Finding[] = [];
  for (const rule of selected) {
    findings.push(...(await rule.run(ctx)));
  }

  process.stdout.write(renderReport(findings, { showInfo: config.showInfo, title: config.title }));
  process.exit(hasFatal(findings) ? 1 : 0);
}

await main();
