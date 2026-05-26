#!/usr/bin/env -S node --experimental-strip-types
import { cpSync, existsSync, rmSync } from "node:fs";
import { dirname, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const REPO_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const SOURCE = resolve(REPO_ROOT, "dist", "pagefind");
const TARGET = resolve(REPO_ROOT, "public", "pagefind");

function main(): void {
  if (!existsSync(SOURCE)) {
    console.log("pagefind dev sync: dist/pagefind not found; run `npm run build` once to enable local search");
    return;
  }
  if (existsSync(TARGET)) rmSync(TARGET, { recursive: true, force: true });
  cpSync(SOURCE, TARGET, { recursive: true });
  console.log(`pagefind dev sync: copied ${relative(REPO_ROOT, SOURCE)} -> ${relative(REPO_ROOT, TARGET)}`);
}

main();
