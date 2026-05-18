#!/usr/bin/env -S node --experimental-strip-types
import { spawnSync } from "node:child_process";
import { readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));

const scripts = readdirSync(here)
  .filter((n: string) => n.endsWith(".py"))
  .sort();

type Result = { name: string; code: number; stdout: string; stderr: string };
const results: Result[] = [];

for (const name of scripts) {
  const path = join(here, name);
  process.stderr.write(`\n=== ${name} ===\n`);
  const res = spawnSync("uv", ["run", path], { encoding: "utf-8" });
  process.stdout.write(res.stdout ?? "");
  process.stderr.write(res.stderr ?? "");
  results.push({
    name,
    code: res.status ?? 1,
    stdout: res.stdout ?? "",
    stderr: res.stderr ?? "",
  });
}

const failed = results.filter((r) => r.code !== 0);
process.stderr.write("\n========== summary ==========\n");
for (const r of results) {
  process.stderr.write(`  ${r.code === 0 ? "PASS" : "FAIL"}  ${r.name}\n`);
}
process.stderr.write(`\n${failed.length} of ${results.length} audits failed\n`);
process.exit(failed.length === 0 ? 0 : 1);
