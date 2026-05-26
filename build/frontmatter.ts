import { readFileSync } from "node:fs";
import { relative } from "node:path";

import { parseMarkdownDocument, type Frontmatter } from "../src/lib/publication/source.ts";

export interface CrossRefTarget {
  kind: string;
  number: number;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function readFrontmatter(path: string, repoRoot: string): Frontmatter {
  const text = readFileSync(path, "utf-8");

  try {
    return parseMarkdownDocument(text, { frontmatter: "required" }).frontmatter;
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    throw new Error(`${relative(repoRoot, path)}: ${message}`);
  }
}

export function stringField(frontmatter: Frontmatter, key: string): string | undefined {
  const value = frontmatter[key];
  return typeof value === "string" ? value : undefined;
}

export function integerField(frontmatter: Frontmatter, key: string): number | undefined {
  const value = frontmatter[key];
  return typeof value === "number" && Number.isInteger(value) ? value : undefined;
}

export function crossRefTargets(frontmatter: Frontmatter): CrossRefTarget[] {
  const raw = frontmatter.cross_refs;
  if (!Array.isArray(raw)) return [];

  const targets: CrossRefTarget[] = [];
  for (const entry of raw) {
    if (!isRecord(entry) || !isRecord(entry.target)) continue;
    const kind = entry.target.kind;
    const number = entry.target.number;
    if (typeof kind === "string" && typeof number === "number" && Number.isInteger(number)) {
      targets.push({ kind, number });
    }
  }
  return targets;
}
