#!/usr/bin/env node
// The audit runner (`mise run audit:repo`). Selects rules by tier for the requested
// mode, runs each pure scanner against the repo root, prints the report, and
// exits non-zero only when a fatal finding is present. See docs/audit-harness.md
// and docs/tooling.md (`mise run audit:repo` is the repository entry point).

import type { Finding } from "./lib/finding.ts";
import type { Rule, Tier } from "./lib/rule.ts";
import { makeContext } from "./lib/rule.ts";
import { REPO_ROOT } from "./lib/repo.ts";
import { renderReport, renderRuleTimings, hasFatal } from "./lib/report.ts";
import { runRules } from "./lib/runner.ts";
import { RULES } from "./rules/index.ts";

type Mode = "default" | "agent" | "post-build";

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
  // Rules that need an emitted dist/ (PAN014 link crawl, PAN008 asset scan).
  "post-build": { tiers: new Set<Tier>(["post-build"]), showInfo: false, title: "Pancratius audit (post-build)" },
};

function parseMode(argv: readonly string[]): Mode {
  const arg = argv[2];
  if (arg === undefined || arg === "default") return "default";
  if (arg === "agent" || arg === "post-build") return arg;
  process.stderr.write(`unknown audit mode: ${arg}\nusage: harness.ts [default|agent|post-build]\n`);
  process.exit(2);
}

async function main(): Promise<void> {
  const mode = parseMode(process.argv);
  const config = MODES[mode];
  const ctx = makeContext(REPO_ROOT);
  const selected: readonly Rule[] = RULES.filter((r) => config.tiers.has(r.tier));

  const runs = await runRules(selected, ctx);
  const findings: Finding[] = runs.flatMap((run) => run.findings);

  process.stdout.write(renderReport(findings, { showInfo: config.showInfo, title: config.title }));
  process.stdout.write(`\n${renderRuleTimings(runs)}`);
  process.exit(hasFatal(findings) ? 1 : 0);
}

await main();
