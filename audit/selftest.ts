#!/usr/bin/env node
// The both-polarity self-test (`npm run audit:selftest`). A rotted audit is false
// confidence (docs/audit-harness.md → "Self-Tests"), so every gating rule ships
// with two tiny fixtures and this runner enforces them:
//
//   fixtures/<rule.id>/bad/   — a known violation; the rule MUST fire (>=1 finding)
//   fixtures/<rule.id>/good/  — a legitimate state; the rule MUST NOT fire (0)
//
// The good fixture is the insurance that a fatal rule won't scream on an allowed
// future change. Both are MANDATORY for every `core` and `post-build` rule; a missing
// fixture fails the run. Heuristic (info-only, non-gating) rules are exempt.
//
// Adding a rule with its two fixtures auto-registers its test — no edits here.

import { join } from "node:path";
import { existsSync } from "node:fs";
import { spawnSync } from "node:child_process";

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

let pandocAvailable: boolean | null = null;
/** Whether pandoc is on PATH. CI never installs it (docs/downloads.md), so a
 * `requiresPandoc` rule cannot run — and cannot be self-tested — there. */
function hasPandoc(): boolean {
  pandocAvailable ??= !spawnSync("pandoc", ["--version"], { stdio: "ignore" }).error;
  return pandocAvailable;
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

  const fixtureIssue = fixturePresenceResult(rule, hasBad, hasGood);
  if (fixtureIssue) return [fixtureIssue];

  // The fixtures must still EXIST (checked above), but a rule whose oracle needs
  // pandoc cannot fire without it: skip the run, as the rule itself does, rather
  // than read its forced silence as a dead audit. Locally (pandoc present) it runs.
  if (rule.requiresPandoc && !hasPandoc()) {
    return [{ ok: true, label: `${rule.id} (skipped — pandoc unavailable, cannot self-test)` }];
  }

  const results: Result[] = [];
  if (hasBad) results.push(await checkBadFixture(rule, badDir));
  if (hasGood) results.push(await checkGoodFixture(rule, goodDir));
  return results;
}

function fixturePresenceResult(rule: Rule, hasBad: boolean, hasGood: boolean): Result | null {
  if (rule.tier === "heuristic") {
    return !hasBad && !hasGood
      ? { ok: true, label: `${rule.id} (heuristic, no fixtures required)` }
      : null;
  }

  if (hasBad && hasGood) return null;
  return {
    ok: false,
    label: `${rule.id} fixtures`,
    detail: `a ${rule.tier} rule needs both fixtures/${rule.id}/bad and /good (bad=${hasBad}, good=${hasGood})`,
  };
}

async function checkBadFixture(rule: Rule, badDir: string): Promise<Result> {
  try {
    const found = await runAgainst(rule, badDir);
    return badFixtureResult(rule, found);
  } catch (err) {
    return { ok: false, label: `${rule.id} bad`, detail: `rule threw: ${String(err)}` };
  }
}

function badFixtureResult(rule: Rule, found: readonly Finding[]): Result {
  const shape = found.map(shapeError).find((error) => error !== null) ?? null;
  const firedFatal = found.some((finding) => finding.severity === "fatal");
  if (found.length === 0) {
    return { ok: false, label: `${rule.id} bad`, detail: "rule did not fire on the known-bad fixture" };
  }
  if (shape) return { ok: false, label: `${rule.id} bad`, detail: shape };
  if (!firedFatal) {
    return {
      ok: false,
      label: `${rule.id} bad`,
      detail: `${rule.tier} (gating) rule fired ${found.length} finding(s) but none are FATAL — it would NOT block CI; a gating rule's bad fixture must produce a fatal finding`,
    };
  }
  return { ok: true, label: `${rule.id} bad (fired: ${found.length}, fatal)` };
}

async function checkGoodFixture(rule: Rule, goodDir: string): Promise<Result> {
  try {
    const found = await runAgainst(rule, goodDir);
    return goodFixtureResult(rule, found);
  } catch (err) {
    return { ok: false, label: `${rule.id} good`, detail: `rule threw: ${String(err)}` };
  }
}

function goodFixtureResult(rule: Rule, found: readonly Finding[]): Result {
  if (found.length === 0) return { ok: true, label: `${rule.id} good (silent)` };
  return {
    ok: false,
    label: `${rule.id} good`,
    detail: `rule false-positived on the known-good fixture: ${found
      .map((finding) => finding.observed)
      .join(" | ")}`,
  };
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
