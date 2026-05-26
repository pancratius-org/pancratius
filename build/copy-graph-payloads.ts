#!/usr/bin/env -S node --experimental-strip-types
import { copyFileSync, existsSync, mkdirSync, readFileSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { stderr } from "node:process";

const REPO_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const SRC_DIR = join(REPO_ROOT, "data");
const DST_DIR = join(REPO_ROOT, "public", "data");

const PAYLOADS = [
  "pancratius-concepts-graph.json",
  "pancratius-books-graph.json",
] as const;

function sameBytes(a: string, b: string): boolean {
  return existsSync(a) && readFileSync(a).equals(readFileSync(b));
}

function main(): number {
  mkdirSync(DST_DIR, { recursive: true });
  const missing: string[] = [];

  for (const name of PAYLOADS) {
    const src = join(SRC_DIR, name);
    if (!existsSync(src)) {
      missing.push(name);
      continue;
    }
    const dst = join(DST_DIR, name);
    if (sameBytes(dst, src)) continue;
    copyFileSync(src, dst);
    stderr.write(`copied ${name} -> ${relative(REPO_ROOT, DST_DIR)}/\n`);
  }

  if (missing.length) {
    stderr.write(
      `conceptosphere payloads missing in data/: ${missing.join(", ")} ` +
        "- run `uv run pancratius conceptosphere graph generate` to regenerate\n",
    );
    return 1;
  }
  return 0;
}

process.exit(main());
