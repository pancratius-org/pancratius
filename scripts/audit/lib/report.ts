// The pretty report. Human-readable text is the product (docs/audit-harness.md →
// "CLI Shape"): a finding should explain enough that an agent picks the right
// abstraction instead of just silencing the warning. The default (CI) view is
// terse — fatals in full, warnings in full, info summarised; agent mode shows
// everything grouped by severity.

import {
  type Finding,
  type Severity,
  bySeverityThenLocation,
  countBySeverity,
} from "./finding.ts";

export interface ReportOptions {
  /** Show info findings in full. Off for the terse CI view, on for agent mode. */
  showInfo: boolean;
  /** Heading line, e.g. "Pancratius audit (agent)". */
  title?: string;
}

function location(f: Finding): string | null {
  if (!f.file) return null;
  return f.line ? `${f.file}:${f.line}` : f.file;
}

function renderFinding(f: Finding): string {
  const lines = [`  ${f.rule} ${f.category}`];
  const loc = location(f);
  if (loc) lines.push(`  ${loc}`);
  lines.push(`  Observed: ${f.observed}`);
  lines.push(`  Contract: ${f.contract}`);
  lines.push(`  Why: ${f.why}`);
  lines.push(`  Repair: ${f.repair}`);
  if (f.doNotFixBy) lines.push(`  Do not fix by: ${f.doNotFixBy}`);
  return lines.join("\n");
}

function section(name: Uppercase<Severity>, findings: Finding[]): string {
  if (findings.length === 0) return `${name}\n  none`;
  return `${name}\n${findings.map(renderFinding).join("\n\n")}`;
}

export function renderReport(findings: readonly Finding[], opts: ReportOptions): string {
  const counts = countBySeverity(findings);
  const sorted = [...findings].sort(bySeverityThenLocation);
  const of = (s: Severity): Finding[] => sorted.filter((f) => f.severity === s);

  const blocks: string[] = [
    opts.title ?? "Pancratius audit",
    `fatal: ${counts.fatal}  warning: ${counts.warning}  info: ${counts.info}`,
    "",
    section("FATAL", of("fatal")),
    "",
    section("WARNING", of("warning")),
  ];

  if (opts.showInfo) {
    blocks.push("", section("INFO", of("info")));
  } else if (counts.info > 0) {
    blocks.push("", `INFO\n  ${counts.info} informational finding(s) — run \`npm run audit:agent\` to see them`);
  }

  return blocks.join("\n") + "\n";
}

/** Exit-relevant verdict: CI fails only when a fatal finding is present. */
export function hasFatal(findings: readonly Finding[]): boolean {
  return findings.some((f) => f.severity === "fatal");
}
