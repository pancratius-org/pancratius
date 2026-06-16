#!/usr/bin/env node
import { createHash } from "node:crypto";
import {
  copyFileSync,
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  readdirSync,
  renameSync,
  rmSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { basename, dirname, join, relative, resolve } from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

import { SEGMENT_OF } from "../src/lib/kinds.ts";
import { DEFAULT_LOCALE, LOCALES, type Locale } from "../src/lib/locales.ts";
import { renderPublicWorkMarkdown } from "../src/lib/publication/public-markdown.ts";
import { publicationOrigin } from "../src/lib/publication/site.ts";
import { readFrontmatter, stringField } from "./frontmatter.ts";

const REPO_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const CONTENT = join(REPO_ROOT, "src", "content");
const CACHE_DIR = join(REPO_ROOT, ".cache", "bulk-archives");
const MANIFEST = join(REPO_ROOT, "data", "bulk-archives.json");
const PUBLIC_ORIGIN = publicationOrigin();

// The bulk corpus archive ships WORKS only — books + poems. Projects are themed
// sections, not corpus works: they're excluded here just as they are from the
// per-work download routes and the feeds, and their component-oriented landing
// HTML doesn't survive Markdown flattening anyway.
const KIND_DIRS: Record<"book" | "poem", string> = {
  book: SEGMENT_OF.book,
  poem: SEGMENT_OF.poem,
};
// The archive kinds, derived from KIND_DIRS so there's no second list to drift.
type ArchiveKind = keyof typeof KIND_DIRS;
const LANGS = LOCALES;
const ALL_FORMATS = ["md", "pdf", "epub"] as const;
const DEFAULT_FORMATS = ["md"] as const;

type Format = typeof ALL_FORMATS[number];

interface Entry {
  kind: ArchiveKind;
  lang: Locale;
  slug: string;
  mdPath: string;
  srcPath: string;
  workKey: string;
}

interface ArchiveInfo {
  name: string;
  format: Format;
  url: string;
  size: number;
  size_human: string;
  sha256: string;
  items: number;
}

function slugFor(mdPath: string): string | null {
  return stringField(readFrontmatter(mdPath, REPO_ROOT), "slug") ?? null;
}

function iterEntries(format: Format): Entry[] {
  const entries: Entry[] = [];
  for (const [kind, folderName] of Object.entries(KIND_DIRS) as [ArchiveKind, string][]) {
    entries.push(...entriesForKind(kind, folderName, format));
  }
  return entries;
}

function entriesForKind(kind: ArchiveKind, folderName: string, format: Format): Entry[] {
  const root = join(CONTENT, folderName);
  if (!existsSync(root)) return [];
  return readdirSync(root)
    .sort()
    .flatMap((workKey) => entriesForWork(kind, root, workKey, format));
}

function entriesForWork(kind: ArchiveKind, root: string, workKey: string, format: Format): Entry[] {
  const workDir = join(root, workKey);
  if (!statSync(workDir).isDirectory()) return [];
  return LANGS.flatMap((lang) => entryForLanguage(kind, workDir, workKey, lang, format));
}

function entryForLanguage(
  kind: ArchiveKind,
  workDir: string,
  workKey: string,
  lang: Locale,
  format: Format,
): Entry[] {
  const mdPath = join(workDir, `${lang}.md`);
  if (!existsSync(mdPath)) return [];
  const slug = slugFor(mdPath);
  if (!slug) return [];
  const srcPath = join(workDir, `${lang}.${format}`);
  return existsSync(srcPath) ? [{ kind, lang, slug, mdPath, srcPath, workKey }] : [];
}

function sha256Of(path: string): string {
  const hash = createHash("sha256");
  hash.update(readFileSync(path));
  return hash.digest("hex");
}

function humanBytes(size: number): string {
  let n = size;
  for (const unit of ["B", "KB", "MB", "GB"]) {
    if (n < 1024) return unit === "B" ? `${n} B` : `${n.toFixed(1)} ${unit}`;
    n /= 1024;
  }
  return `${n.toFixed(1)} TB`;
}

function zipWithSystem(zipPath: string, files: { arcname: string; path?: string; content?: string }[]): void {
  const tmpRoot = mkdtempSync(join(CACHE_DIR, `.tmp-${basename(zipPath, ".zip")}-`));
  const stagedZipPath = join(CACHE_DIR, `${basename(tmpRoot)}.zip`);
  try {
    for (const file of files) {
      const dst = join(tmpRoot, file.arcname);
      mkdirSync(dirname(dst), { recursive: true });
      if (file.content !== undefined) {
        writeFileSync(dst, file.content, "utf-8");
      } else if (file.path) {
        copyFileSync(file.path, dst);
      }
    }
    const args = ["-q", "-r", stagedZipPath, "."];
    const result = spawnSync("zip", args, { cwd: tmpRoot, encoding: "utf-8" });
    if (result.status !== 0) {
      throw new Error(`zip failed for ${zipPath}: ${result.stderr || result.stdout}`);
    }
    renameSync(stagedZipPath, zipPath);
  } finally {
    rmrf(stagedZipPath);
    rmrf(tmpRoot);
  }
}

function rmrf(path: string): void {
  rmSync(path, { recursive: true, force: true });
}

function buildArchive(format: Format): ArchiveInfo | null {
  mkdirSync(CACHE_DIR, { recursive: true });
  const outPath = join(CACHE_DIR, `all-${format}.zip`);
  const entries = iterEntries(format);
  if (entries.length === 0) return null;

  zipWithSystem(outPath, entries.map((entry) => {
    const arcname = `${KIND_DIRS[entry.kind]}/${entry.lang}/${entry.slug}.${format}`;
    if (format === "md") {
      return {
        arcname,
        content: renderPublicWorkMarkdown(readFileSync(entry.mdPath, "utf-8"), {
          origin: PUBLIC_ORIGIN,
          work: {
            kind: entry.kind,
            bundleKey: entry.workKey,
          },
        }),
      };
    }
    return { arcname, path: entry.srcPath };
  }));

  const size = statSync(outPath).size;
  return {
    name: `all-${format}.zip`,
    format,
    // The archive endpoint lives under the default locale (`src/pages/ru/downloads/`).
    url: `/${DEFAULT_LOCALE}/downloads/all-${format}.zip`,
    size,
    size_human: humanBytes(size),
    sha256: sha256Of(outPath),
    items: entries.length,
  };
}

function buildManifest(formats: Format[]): number {
  mkdirSync(CACHE_DIR, { recursive: true });
  const archives: ArchiveInfo[] = [];
  for (const format of formats) {
    const info = buildArchive(format);
    if (!info) continue;
    archives.push(info);
    process.stdout.write(`  bundled  ${info.name.padEnd(18)}  ${info.size_human.padStart(10)}  (${info.items} items)\n`);
  }
  const payload = {
    generated_at: new Date().toISOString().replace(/\.\d{3}Z$/, "Z"),
    archives,
  };
  mkdirSync(dirname(MANIFEST), { recursive: true });
  writeFileSync(MANIFEST, `${JSON.stringify(payload, null, 2)}\n`, "utf-8");
  process.stdout.write(`\nmanifest: ${relative(REPO_ROOT, MANIFEST)}  (${archives.length} archives)\n`);
  return 0;
}

function parseFormats(raw: string): Format[] {
  const requested = raw.split(",").map((s) => s.trim().toLowerCase()).filter(Boolean);
  const unknown = requested.filter((f) => !ALL_FORMATS.includes(f as Format));
  if (unknown.length) {
    throw new Error(`unknown format(s): ${unknown.join(", ")}. valid: ${ALL_FORMATS.join(", ")}`);
  }
  return (requested.length ? requested : [...DEFAULT_FORMATS]) as Format[];
}

function main(): number {
  const args = process.argv.slice(2);
  const formatArg = args.find((arg) => arg.startsWith("--formats="));
  const formatIndex = args.indexOf("--formats");
  const rawFormats = formatArg
    ? formatArg.slice("--formats=".length)
    : formatIndex >= 0
      ? args[formatIndex + 1] ?? ""
      : DEFAULT_FORMATS.join(",");
  const formats = parseFormats(rawFormats);
  return buildManifest(formats);
}

try {
  process.exit(main());
} catch (error) {
  process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`);
  process.exit(1);
}
