#!/usr/bin/env -S node --experimental-strip-types
import { createHash } from "node:crypto";
import {
  copyFileSync,
  existsSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  rmSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { basename, dirname, join, relative, resolve } from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

import { renderPublicMarkdown, type PublicMarkdownKind } from "../src/lib/public-markdown.ts";
import { SEGMENT_OF } from "../src/lib/kinds.ts";
import { LOCALES, type Locale } from "../src/lib/locales.ts";

const REPO_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const CONTENT = join(REPO_ROOT, "src", "content");
const CACHE_DIR = join(REPO_ROOT, ".cache", "bulk-archives");
const MANIFEST = join(REPO_ROOT, "data", "bulk-archives.json");

// The bulk corpus archive ships WORKS only — books + poems. Projects are themed
// sections, not corpus works: they're excluded here just as they are from the
// per-work download routes and the feeds, and their component-oriented landing
// HTML doesn't survive Markdown flattening anyway.
const KIND_DIRS: Record<"book" | "poem", string> = {
  book: SEGMENT_OF.book,
  poem: SEGMENT_OF.poem,
};
const LANGS = LOCALES;
const ALL_FORMATS = ["md", "pdf", "epub"] as const;
const DEFAULT_FORMATS = ["md"] as const;

type Format = typeof ALL_FORMATS[number];

interface Entry {
  kind: PublicMarkdownKind;
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

function frontmatterScalar(text: string, key: string): string | null {
  if (!text.startsWith("---")) return null;
  const end = text.indexOf("\n---", 3);
  if (end < 0) return null;
  const lines = text.slice(4, end).split("\n");
  for (const line of lines) {
    const match = new RegExp(`^${key}:\\s*(.+?)\\s*$`).exec(line);
    if (!match) continue;
    return match[1].replace(/^['"]|['"]$/g, "");
  }
  return null;
}

function slugFor(mdPath: string): string | null {
  return frontmatterScalar(readFileSync(mdPath, "utf-8"), "slug");
}

function iterEntries(format: Format): Entry[] {
  const entries: Entry[] = [];
  for (const [kind, folderName] of Object.entries(KIND_DIRS) as [PublicMarkdownKind, string][]) {
    const root = join(CONTENT, folderName);
    if (!existsSync(root)) continue;
    for (const workKey of readdirSync(root).sort()) {
      const workDir = join(root, workKey);
      if (!statSync(workDir).isDirectory()) continue;
      for (const lang of LANGS) {
        const mdPath = join(workDir, `${lang}.md`);
        if (!existsSync(mdPath)) continue;
        const slug = slugFor(mdPath);
        if (!slug) continue;
        const srcPath = join(workDir, `${lang}.${format}`);
        if (!existsSync(srcPath)) continue;
        entries.push({ kind, lang, slug, mdPath, srcPath, workKey });
      }
    }
  }
  return entries;
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
  const tmpRoot = join(CACHE_DIR, `.tmp-${basename(zipPath, ".zip")}`);
  rmrf(tmpRoot);
  mkdirSync(tmpRoot, { recursive: true });
  for (const file of files) {
    const dst = join(tmpRoot, file.arcname);
    mkdirSync(dirname(dst), { recursive: true });
    if (file.content !== undefined) {
      writeFileSync(dst, file.content, "utf-8");
    } else if (file.path) {
      copyFileSync(file.path, dst);
    }
  }
  const args = ["-q", "-r", zipPath, "."];
  const result = spawnSync("zip", args, { cwd: tmpRoot, encoding: "utf-8" });
  rmrf(tmpRoot);
  if (result.status !== 0) {
    throw new Error(`zip failed for ${zipPath}: ${result.stderr || result.stdout}`);
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
  if (existsSync(outPath)) rmrf(outPath);

  zipWithSystem(outPath, entries.map((entry) => {
    const arcname = `${KIND_DIRS[entry.kind]}/${entry.lang}/${entry.slug}.${format}`;
    if (format === "md") {
      return {
        arcname,
        content: renderPublicMarkdown(readFileSync(entry.mdPath, "utf-8"), {
          kind: entry.kind,
          workKey: entry.workKey,
          isVerse: entry.kind === "poem",
        }),
      };
    }
    return { arcname, path: entry.srcPath };
  }));

  const size = statSync(outPath).size;
  return {
    name: `all-${format}.zip`,
    format,
    url: `/downloads/all-${format}.zip`,
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
    ? formatArg.split("=", 2)[1]
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
