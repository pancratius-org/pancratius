// The finding is the product of the harness. Everything else (rules, runner,
// report) exists to produce and present these. A finding teaches a boundary: it
// names the fact observed, the contract it breaks, why that costs a reader or a
// deploy, how to repair it, and the wrong way to make it pass. See
// docs/audit-harness.md → "Finding Shape".

/** Three severities; CI fails only on `fatal`. See the doc's Severity table. */
export type Severity = "fatal" | "warning" | "info";

export const SEVERITIES: readonly Severity[] = ["fatal", "warning", "info"];

export interface Finding {
  /** Stable rule id, e.g. `PAN001` or a family member `PAN016-source-language`. */
  rule: string;
  severity: Severity;
  /** Short kebab category for grouping, e.g. `path-boundary`. */
  category: string;
  /** Repo-relative POSIX path of the offending file, when one exists. */
  file?: string;
  /** 1-based line number within `file`, when known. */
  line?: number;
  /** The concrete fact found in the tree. */
  observed: string;
  /** The durable contract or architectural pressure that fact violates. */
  contract: string;
  /** Why it matters — the reader/deploy/source-of-truth harm. */
  why: string;
  /** The suggested repair. */
  repair: string;
  /** The tempting wrong "fix" that preserves the bad model. Optional. */
  doNotFixBy?: string;
}

const RANK: Record<Severity, number> = { fatal: 0, warning: 1, info: 2 };

/** Sort fatal-first, then by rule id, then by file/line — stable report order. */
export function bySeverityThenLocation(a: Finding, b: Finding): number {
  if (RANK[a.severity] !== RANK[b.severity]) return RANK[a.severity] - RANK[b.severity];
  if (a.rule !== b.rule) return a.rule < b.rule ? -1 : 1;
  const af = a.file ?? "";
  const bf = b.file ?? "";
  if (af !== bf) return af < bf ? -1 : 1;
  return (a.line ?? 0) - (b.line ?? 0);
}

export function countBySeverity(findings: readonly Finding[]): Record<Severity, number> {
  const counts: Record<Severity, number> = { fatal: 0, warning: 0, info: 0 };
  for (const f of findings) counts[f.severity] += 1;
  return counts;
}
