#!/usr/bin/env -S node --experimental-strip-types
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
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

function minifyJsonFile(path: string): string {
  const parsed: unknown = JSON.parse(readFileSync(path, "utf-8"));
  const minified = JSON.stringify(parsed);
  if (minified === undefined) {
    throw new Error("JSON payload must be an object, array, or scalar");
  }
  return minified;
}

function sameText(path: string, text: string): boolean {
  return existsSync(path) && readFileSync(path, "utf-8") === text;
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
    let minified: string;
    try {
      minified = minifyJsonFile(src);
    } catch (err: unknown) {
      stderr.write(`invalid graph payload JSON in data/${name}: ${String(err)}\n`);
      return 1;
    }

    const dst = join(DST_DIR, name);
    if (sameText(dst, minified)) continue;
    writeFileSync(dst, minified);
    stderr.write(`minified ${name} -> ${relative(REPO_ROOT, DST_DIR)}/\n`);
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
