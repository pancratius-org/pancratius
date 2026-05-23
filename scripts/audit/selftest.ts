#!/usr/bin/env -S node --experimental-strip-types
// The both-polarity self-test (`npm run audit:selftest`). A rotted audit is false
// confidence (docs/audit-harness.md → "Self-Tests"), so every gating rule ships
// with two tiny fixtures and this runner enforces them:
//
//   fixtures/<rule.id>/bad/   — a known violation; the rule MUST fire (>=1 finding)
//   fixtures/<rule.id>/good/  — a legitimate state; the rule MUST NOT fire (0)
//
// The good fixture is the insurance that a fatal rule won't scream on an allowed
// future change. Both are MANDATORY for every `core` and `deploy` rule; a missing
// fixture fails the run. Heuristic (info-only, non-gating) rules are exempt.
//
// Adding a rule with its two fixtures auto-registers its test — no edits here.

import { join } from "node:path";
import { existsSync } from "node:fs";

import { type Finding, SEVERITIES } from "./lib/finding.ts";
import type { Rule } from "./lib/rule.ts";
import { makeContext } from "./lib/rule.ts";
import { AUDIT_DIR } from "./lib/repo.ts";
import { RULES } from "./rules/index.ts";

const FIXTURES = join(AUDIT_DIR, "fixtures");

interface Result {
  ok: boolean;
  label: string;
  detail?: string;
}

function shapeError(f: Finding): string | null {
  for (const key of ["rule", "category", "observed", "contract", "why", "repair"] as const) {
    if (!f[key] || f[key].trim() === "") return `finding from ${f.rule || "?"} has empty ${key}`;
  }
  if (!SEVERITIES.includes(f.severity)) {
    return `finding from ${f.rule} has invalid severity ${JSON.stringify(f.severity)}`;
  }
  return null;
}

async function runAgainst(rule: Rule, root: string): Promise<Finding[]> {
  return [...(await rule.run(makeContext(root)))];
}

async function checkRule(rule: Rule): Promise<Result[]> {
  const dir = join(FIXTURES, rule.id);
  const badDir = join(dir, "bad");
  const goodDir = join(dir, "good");
  const hasBad = existsSync(badDir);
  const hasGood = existsSync(goodDir);

  if (rule.tier === "heuristic") {
    if (!hasBad && !hasGood) return [{ ok: true, label: `${rule.id} (heuristic, no fixtures required)` }];
  } else if (!hasBad || !hasGood) {
    return [
      {
        ok: false,
        label: `${rule.id} fixtures`,
        detail: `a ${rule.tier} rule needs both fixtures/${rule.id}/bad and /good (bad=${hasBad}, good=${hasGood})`,
      },
    ];
  }

  const results: Result[] = [];

  if (hasBad) {
    try {
      const found = await runAgainst(rule, badDir);
      const shape = found.map(shapeError).find((e) => e !== null) ?? null;
      // A core/deploy rule is a GATING rule: CI exits non-zero only on `fatal`
      // (report.ts hasFatal), so its bad fixture must produce at least one FATAL
      // finding — otherwise the rule "fires" in the self-test yet would NOT block
      // CI (e.g. it accidentally returns a warning). Heuristic rules don't reach
      // here (they're exempt from fixtures above).
      const firedFatal = found.some((f) => f.severity === "fatal");
      if (found.length === 0) {
        results.push({ ok: false, label: `${rule.id} bad`, detail: "rule did not fire on the known-bad fixture" });
      } else if (shape) {
        results.push({ ok: false, label: `${rule.id} bad`, detail: shape });
      } else if (!firedFatal) {
        results.push({
          ok: false,
          label: `${rule.id} bad`,
          detail: `${rule.tier} (gating) rule fired ${found.length} finding(s) but none are FATAL — it would NOT block CI; a gating rule's bad fixture must produce a fatal finding`,
        });
      } else {
        results.push({ ok: true, label: `${rule.id} bad (fired: ${found.length}, fatal)` });
      }
    } catch (err) {
      results.push({ ok: false, label: `${rule.id} bad`, detail: `rule threw: ${String(err)}` });
    }
  }

  if (hasGood) {
    try {
      const found = await runAgainst(rule, goodDir);
      results.push(
        found.length === 0
          ? { ok: true, label: `${rule.id} good (silent)` }
          : {
              ok: false,
              label: `${rule.id} good`,
              detail: `rule false-positived on the known-good fixture: ${found
                .map((f) => f.observed)
                .join(" | ")}`,
            },
      );
    } catch (err) {
      results.push({ ok: false, label: `${rule.id} good`, detail: `rule threw: ${String(err)}` });
    }
  }

  return results;
}

async function main(): Promise<void> {
  const all: Result[] = [];
  for (const rule of RULES) all.push(...(await checkRule(rule)));

  process.stdout.write("Pancratius audit — self-test\n\n");
  for (const r of all) {
    process.stdout.write(`  ${r.ok ? "ok  " : "FAIL"}  ${r.label}\n`);
    if (!r.ok && r.detail) process.stdout.write(`        ${r.detail}\n`);
  }

  const failed = all.filter((r) => !r.ok);
  process.stdout.write(`\n${failed.length === 0 ? "all" : failed.length + " of " + all.length} self-test(s) ${failed.length === 0 ? "passed" : "FAILED"}\n`);
  process.exit(failed.length === 0 ? 0 : 1);
}

await main();
