import { spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";

type Severity = "info" | "low" | "moderate" | "high" | "critical";

interface Advisory {
  name: string;
  dependency: string;
  url: string;
  severity: Severity;
}

interface Vulnerability {
  name: string;
  severity: Severity;
  isDirect: boolean;
  via: (string | Advisory)[];
  nodes: string[];
  fixAvailable: unknown;
}

interface AuditReport {
  vulnerabilities: Record<string, Vulnerability>;
}

export interface AuditContext {
  packages: Record<string, unknown>;
  lintStyleCommand: unknown;
}

const BRACES_ADVISORY = "https://github.com/advisories/GHSA-vfj7-8cjw-p6xm";
const REVIEWED_LINT_COMMAND = 'stylelint "src/**/*.{astro,css}"';

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function isSeverity(value: unknown): value is Severity {
  return value === "info" || value === "low" || value === "moderate" || value === "high" || value === "critical";
}

function isAdvisory(value: unknown): value is Advisory {
  return isRecord(value) && typeof value.name === "string" && typeof value.dependency === "string"
    && typeof value.url === "string" && isSeverity(value.severity);
}

function isVulnerability(value: unknown): value is Vulnerability {
  return isRecord(value) && typeof value.name === "string" && isSeverity(value.severity)
    && typeof value.isDirect === "boolean"
    && Array.isArray(value.via) && value.via.every((cause: unknown) => typeof cause === "string" || isAdvisory(cause))
    && Array.isArray(value.nodes) && value.nodes.every((node: unknown) => typeof node === "string")
    && Object.hasOwn(value, "fixAvailable");
}

export function parseAuditReport(source: string, status: number | null): AuditReport {
  if (status !== 0 && status !== 1) throw new Error(`npm audit failed (exit ${String(status)})`);
  const value: unknown = JSON.parse(source);
  const entries = Object.entries(auditEntries(value)).map(parseVulnerability);
  if (status === 1 && entries.length === 0) throw new Error("npm audit failed without findings");
  return { vulnerabilities: Object.fromEntries(entries) };
}

function auditEntries(value: unknown): Record<string, unknown> {
  if (!isRecord(value) || value.auditReportVersion !== 2 || Object.hasOwn(value, "error")
    || !isRecord(value.vulnerabilities)) throw new Error("npm audit returned an invalid report");
  return value.vulnerabilities;
}

function parseVulnerability([name, entry]: [string, unknown]): [string, Vulnerability] {
  if (!isVulnerability(entry) || entry.name !== name) throw new Error(`invalid audit entry: ${name}`);
  return [name, entry];
}

export function parseAuditContext(lockSource: string, packageSource: string): AuditContext {
  const lock: unknown = JSON.parse(lockSource);
  const manifest: unknown = JSON.parse(packageSource);
  if (!isRecord(lock) || lock.lockfileVersion !== 3 || !isRecord(lock.packages)
    || !isRecord(manifest) || !isRecord(manifest.scripts)) throw new Error("invalid dependency manifests");
  return { packages: lock.packages, lintStyleCommand: manifest.scripts["lint:style"] };
}

function acceptedBracesRisk(report: AuditReport, context: AuditContext, name: string, visited: ReadonlySet<string>): boolean {
  const entry = report.vulnerabilities[name];
  if (!entry || visited.has(name) || entry.severity !== "high" || entry.fixAvailable !== false
    || entry.nodes.length === 0 || entry.via.length === 0) return false;
  if (!reviewedConsumer(entry)) return false;
  if (!entry.nodes.every((node) => isRecord(context.packages[node]) && context.packages[node].dev === true)) return false;
  const next = new Set([...visited, name]);
  return entry.via.every((cause) => typeof cause === "string"
    ? acceptedBracesRisk(report, context, cause, next)
    : reviewedBracesAdvisory(entry, cause, context));
}

function reviewedConsumer(entry: Vulnerability): boolean {
  return !entry.isDirect || entry.name === "stylelint" || entry.name === "stylelint-config-recommended";
}

function reviewedBracesAdvisory(entry: Vulnerability, cause: Advisory, context: AuditContext): boolean {
  return entry.name === "braces" && cause.name === "braces" && cause.dependency === "braces"
    && cause.url === BRACES_ADVISORY && cause.severity === "high"
    && context.lintStyleCommand === REVIEWED_LINT_COMMAND
    && entry.nodes.every((node) => isRecord(context.packages[node]) && context.packages[node].version === "3.0.3");
}

export function blockingDependencies(report: AuditReport, context: AuditContext): string[] {
  return Object.values(report.vulnerabilities)
    .filter((entry) => entry.severity === "high" || entry.severity === "critical")
    .filter((entry) => !acceptedBracesRisk(report, context, entry.name, new Set()))
    .map((entry) => entry.name);
}

function main(): void {
  const result = spawnSync("npm", ["audit", "--json"], { encoding: "utf8" });
  process.stdout.write(result.stdout);
  process.stderr.write(result.stderr);
  if (result.error) throw result.error;
  const report = parseAuditReport(result.stdout, result.status);
  const context = parseAuditContext(readFileSync("package-lock.json", "utf8"), readFileSync("package.json", "utf8"));
  const blocking = blockingDependencies(report, context);
  if (blocking.length > 0) throw new Error(`high/critical dependency findings: ${blocking.join(", ")}`);
  if (acceptedBracesRisk(report, context, "braces", new Set())) {
    process.stderr.write(`Accepted development-tool risk: ${BRACES_ADVISORY}; fixed repository globs, braces 3.0.3, no available fix. See docs/tooling.md.\n`);
  }
}

if (import.meta.main) main();
