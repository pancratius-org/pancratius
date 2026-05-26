#!/usr/bin/env -S node --experimental-strip-types
import { existsSync, mkdirSync, readdirSync, statSync, writeFileSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { stderr } from "node:process";

import { SEGMENT_OF, type RoutedKind } from "../src/lib/kinds.ts";
import { DEFAULT_LOCALE, LOCALES, type Locale } from "../src/lib/locales.ts";
import { crossRefTargets, integerField, readFrontmatter, stringField } from "./frontmatter.ts";

const REPO_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const CONTENT = join(REPO_ROOT, "src", "content");
const OUTPUT = join(REPO_ROOT, "data", "slug-map.json");

type RoutedEntry = {
  kind: RoutedKind;
  number: number;
  languages: Partial<Record<Locale, { slug: string; url: string }>>;
};

type PageEntry = {
  slug: string;
  languages: Partial<Record<Locale, string>>;
};

function workUrl(segment: string, slug: string, lang: Locale): string {
  return lang === DEFAULT_LOCALE ? `/${segment}/${slug}/` : `/${lang}/${segment}/${slug}/`;
}

function pageUrl(slug: string, lang: Locale): string {
  return lang === DEFAULT_LOCALE ? `/${slug}/` : `/${lang}/${slug}/`;
}

function collectMarkdown(root: string): string[] {
  if (!existsSync(root)) return [];
  const files: string[] = [];
  for (const workKey of readdirSync(root).sort()) {
    const workDir = join(root, workKey);
    if (!statSync(workDir).isDirectory()) continue;
    for (const name of readdirSync(workDir).sort()) {
      if (name.endsWith(".md")) files.push(join(workDir, name));
    }
  }
  return files;
}

function collectRoutedEntries(): { entries: RoutedEntry[]; errors: string[] } {
  const bucket = new Map<string, RoutedEntry>();
  const crossRefs: { path: string; kind: string; number: number }[] = [];

  for (const [kind, segment] of Object.entries(SEGMENT_OF) as [RoutedKind, string][]) {
    for (const mdPath of collectMarkdown(join(CONTENT, segment))) {
      const lang = mdPath.slice(0, -".md".length).split("/").at(-1) as Locale;
      if (!LOCALES.includes(lang)) continue;
      const fm = readFrontmatter(mdPath, REPO_ROOT);
      const fmKind = stringField(fm, "kind");
      if (fmKind !== kind) {
        throw new Error(`${relative(REPO_ROOT, mdPath)}: kind ${String(fmKind)} does not match collection ${kind}`);
      }
      const number = integerField(fm, "number");
      const slug = stringField(fm, "slug");
      if (number === undefined || !slug) {
        throw new Error(`${relative(REPO_ROOT, mdPath)}: missing number/slug`);
      }
      const key = `${kind}:${number}`;
      const entry: RoutedEntry = bucket.get(key) ?? { kind, number, languages: {} };
      entry.languages[lang] = { slug, url: workUrl(segment, slug, lang) };
      bucket.set(key, entry);
      for (const ref of crossRefTargets(fm)) crossRefs.push({ path: mdPath, ...ref });
    }
  }

  const known = new Set(bucket.keys());
  const errors = crossRefs
    .filter((ref) => !known.has(`${ref.kind}:${ref.number}`))
    .map((ref) =>
      `${relative(REPO_ROOT, ref.path)}: cross_refs target (${ref.kind} #${ref.number}) does not exist`,
    );
  const entries = [...bucket.values()].sort((a, b) => a.kind.localeCompare(b.kind) || a.number - b.number);
  return { entries, errors };
}

function collectPages(): PageEntry[] {
  const root = join(CONTENT, "pages");
  const bucket = new Map<string, PageEntry>();
  for (const mdPath of collectMarkdown(root)) {
    const lang = mdPath.slice(0, -".md".length).split("/").at(-1) as Locale;
    if (!LOCALES.includes(lang)) continue;
    const fm = readFrontmatter(mdPath, REPO_ROOT);
    const slug = stringField(fm, "slug");
    if (!slug) throw new Error(`${relative(REPO_ROOT, mdPath)}: missing slug`);
    const entry = bucket.get(slug) ?? { slug, languages: {} };
    entry.languages[lang] = pageUrl(slug, lang);
    bucket.set(slug, entry);
  }
  return [...bucket.values()].sort((a, b) => a.slug.localeCompare(b.slug));
}

function main(): number {
  const { entries, errors } = collectRoutedEntries();
  if (errors.length) {
    for (const error of errors) stderr.write(`error: ${error}\n`);
    stderr.write(`slug-map: ${errors.length} dangling cross_refs target(s); aborting.\n`);
    return 2;
  }

  const payload = {
    generated_at: new Date().toISOString().replace(/\.\d{3}Z$/, "Z"),
    entries,
    pages: collectPages(),
  };
  mkdirSync(dirname(OUTPUT), { recursive: true });
  writeFileSync(OUTPUT, `${JSON.stringify(payload, null, 2)}\n`, "utf-8");

  const byKind = new Map<string, number>();
  for (const entry of entries) byKind.set(entry.kind, (byKind.get(entry.kind) ?? 0) + 1);
  const summary = [...byKind.entries()].sort().map(([kind, count]) => `${kind}=${count}`).join(", ");
  console.log(`slug-map: ${relative(REPO_ROOT, OUTPUT)}  entries(${summary})  pages=${payload.pages.length}`);
  return 0;
}

process.exit(main());
