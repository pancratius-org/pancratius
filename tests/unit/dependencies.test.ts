import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { describe, test } from "node:test";

import { blockingDependencies, parseAuditContext, parseAuditReport } from "../../audit/dependencies.ts";

const AUDIT = readFileSync(new URL("./fixtures/npm-audit-braces.json", import.meta.url), "utf8");
const LINT_COMMAND = 'stylelint "src/**/*.{astro,css}"';

function fixture() {
  const report = parseAuditReport(AUDIT, 1);
  const packages = Object.fromEntries(Object.values(report.vulnerabilities)
    .flatMap((entry) => entry.nodes.map((node) => [node, { version: "3.0.3", dev: true }])));
  const context = parseAuditContext(JSON.stringify({ lockfileVersion: 3, packages }),
    JSON.stringify({ scripts: { "lint:style": LINT_COMMAND } }));
  const braces = report.vulnerabilities.braces;
  assert.ok(braces);
  return { report, context, braces };
}

describe("dependency audit policy", () => {
  test("accepts the real six-node report for the reviewed development-only advisory", () => {
    const { report, context } = fixture();
    assert.deepEqual(blockingDependencies(report, context), []);
  });

  test("blocks a second high advisory even on the same development dependency", () => {
    const { report, context, braces } = fixture();
    braces.via.push({ name: "braces", dependency: "braces", severity: "high", url: "https://github.com/advisories/GHSA-new" });
    assert.ok(blockingDependencies(report, context).includes("braces"));
    assert.ok(blockingDependencies(report, context).includes("stylelint"));
  });

  test("blocks an unrelated high development dependency", () => {
    const { report, context, braces } = fixture();
    report.vulnerabilities.other = { ...braces, name: "other" };
    assert.deepEqual(blockingDependencies(report, context), ["other"]);
  });

  test("requires review if another development tool starts using the vulnerable parser", () => {
    const { report, context, braces } = fixture();
    report.vulnerabilities.other = { ...braces, name: "other", isDirect: true, via: ["braces"] };
    assert.deepEqual(blockingDependencies(report, context), ["other"]);
  });

  test("blocks production exposure anywhere in the affected chain", () => {
    const { report, context } = fixture();
    context.packages["node_modules/micromatch"] = { version: "4.0.8" };
    assert.ok(blockingDependencies(report, context).includes("stylelint"));
  });

  test("requires review when the pinned vulnerable version changes", () => {
    const { report, context } = fixture();
    context.packages["node_modules/braces"] = { version: "3.0.4", dev: true };
    assert.ok(blockingDependencies(report, context).includes("braces"));
  });

  test("requires the available upstream fix instead of keeping the exception", () => {
    const { report, context, braces } = fixture();
    braces.fixAvailable = { name: "braces", version: "3.0.4", isSemVerMajor: false };
    assert.ok(blockingDependencies(report, context).includes("stylelint"));
  });

  test("blocks a severity escalation", () => {
    const { report, context, braces } = fixture();
    braces.severity = "critical";
    assert.ok(blockingDependencies(report, context).includes("braces"));
  });

  test("requires re-review if the linter command accepts a different input", () => {
    const { report, context } = fixture();
    context.lintStyleCommand = 'stylelint "$USER_GLOB"';
    assert.ok(blockingDependencies(report, context).includes("stylelint"));
  });

  test("blocks missing and cyclic advisory causes", () => {
    const { report, context, braces } = fixture();
    braces.via = ["missing"];
    assert.ok(blockingDependencies(report, context).includes("braces"));
    braces.via = ["stylelint"];
    assert.ok(blockingDependencies(report, context).includes("stylelint"));
  });

  test("preserves the high threshold for unrelated moderate findings", () => {
    const { report, context, braces } = fixture();
    report.vulnerabilities.other = { ...braces, name: "other", severity: "moderate" };
    assert.deepEqual(blockingDependencies(report, context), []);
  });

  test("accepts a clean successful audit", () => {
    const { context } = fixture();
    const report = parseAuditReport('{"auditReportVersion":2,"vulnerabilities":{}}', 0);
    assert.deepEqual(blockingDependencies(report, context), []);
  });

  test("fails for unavailable audit data rather than treating it as clean", () => {
    assert.throws(() => parseAuditReport(AUDIT, 2), /npm audit failed/);
    assert.throws(() => parseAuditReport(AUDIT, null), /npm audit failed/);
    assert.throws(() => parseAuditReport("not JSON", 1));
    assert.throws(() => parseAuditReport('{"error":{"code":"ENETUNREACH"}}', 1), /invalid report/);
    assert.throws(() => parseAuditReport('{"auditReportVersion":2,"vulnerabilities":{}}', 1), /without findings/);
    assert.throws(() => parseAuditReport('{"auditReportVersion":2,"vulnerabilities":{"braces":{}}}', 1), /invalid audit entry/);
  });
});
