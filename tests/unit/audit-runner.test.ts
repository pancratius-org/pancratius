import assert from "node:assert/strict";
import { describe, test } from "node:test";

import { normalizePythonResult, type PythonCheckSpec } from "../../audit/lib/python.ts";
import { renderRuleTimings } from "../../audit/lib/report.ts";
import { mapBounded, type RuleRun } from "../../audit/lib/runner.ts";

describe("mapBounded", () => {
  test("caps active work and returns registry order", async () => {
    let active = 0;
    let peak = 0;
    const releases: Array<() => void> = [];
    const items = [0, 1, 2, 3];

    const running = mapBounded(items, 2, async (item) => {
      active += 1;
      peak = Math.max(peak, active);
      await new Promise<void>((resolve) => releases.push(resolve));
      active -= 1;
      return `result-${item}`;
    });

    await waitFor(() => releases.length === 2);
    releases.shift()?.();
    await waitFor(() => releases.length === 2);
    releases.splice(0).forEach((release) => release());
    await waitFor(() => releases.length === 1);
    releases.shift()?.();

    assert.deepEqual(await running, ["result-0", "result-1", "result-2", "result-3"]);
    assert.equal(peak, 2);
  });

  test("rejects an unbounded worker count", async () => {
    await assert.rejects(mapBounded([1], 0, (value) => Promise.resolve(value)), /positive integer/);
  });
});

test("rule timings follow registry order with stable formatting", () => {
  const runs: RuleRun[] = [
    fakeRun("PAN002-second", 1_234),
    fakeRun("PAN001-first", 5),
  ];

  assert.equal(
    renderRuleTimings(runs),
    "RULE TIMINGS\n  PAN002-second     1.23s\n  PAN001-first      0.01s\n",
  );
});

test("Python rule failures preserve the script evidence", () => {
  const [finding] = normalizePythonResult(PYTHON_SPEC, {
    error: new Error("Command failed with exit code 1"),
    status: 1,
    stdout: "FAIL: locale registries differ\n",
    stderr: "",
  });

  assert.equal(finding?.observed, "FAIL: locale registries differ");
});

test("Python launch failures remain readable", () => {
  const [finding] = normalizePythonResult(PYTHON_SPEC, {
    error: new Error("spawn uv ENOENT"),
    status: null,
    stdout: "",
    stderr: "",
  });

  assert.equal(finding?.observed, "python check failed to run: spawn uv ENOENT");
});

const PYTHON_SPEC: PythonCheckSpec = {
  id: "PAN003-locale-parity",
  category: "ssot-parity",
  severity: "fatal",
  script: "python/locales.py",
  contract: "Locale registries agree.",
  why: "Routes and library operations need the same locales.",
  repair: "Update the owning registry.",
};

function fakeRun(id: string, durationMs: number): RuleRun {
  return {
    rule: { id, title: id, tier: "core", run: () => [] },
    findings: [],
    durationMs,
  };
}

async function waitFor(predicate: () => boolean): Promise<void> {
  while (!predicate()) await new Promise((resolve) => setImmediate(resolve));
}
