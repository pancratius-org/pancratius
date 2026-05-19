// Download dispatch. CI-side only: cheap text derivations for `.md` and
// `.txt`, file copies for the heavy formats (`.docx`, `.pdf`, `.epub`).
//
// Per `docs/downloads.md`, PDF/EPUB/DOCX are committed release artefacts
// inside the work bundle (`content/<kind>/<work>/<lang>.{docx,pdf,epub}`).
// The site build never runs pandoc, typst, or any document converter.
// Local refresh of those artefacts is the job of
// `scripts/render_downloads.py`, not this module.

import { existsSync, readFileSync } from "node:fs";
import { resolve as resolvePath } from "node:path";

import type { Locale } from "./i18n";
import { workUrl } from "./i18n";
import type { WorkEntry, WorkPair } from "./works";

export type DownloadFormat = "md" | "txt" | "docx" | "pdf" | "epub";

/** Formats that may exist for each kind, per the contract. */
export const FORMATS_PER_KIND: Record<"book" | "poem" | "project", DownloadFormat[]> = {
  book:    ["md", "txt", "docx", "pdf", "epub"],
  poem:    ["md", "txt", "docx", "pdf"],
  project: ["md", "txt", "docx", "pdf"],
};

const REPO_ROOT = process.cwd();
const CONTENT   = resolvePath(REPO_ROOT, "content");

const CONTENT_TYPE: Record<DownloadFormat, string> = {
  md:   "text/markdown; charset=utf-8",
  txt:  "text/plain; charset=utf-8",
  docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  pdf:  "application/pdf",
  epub: "application/epub+zip",
};

export interface DownloadResult {
  bytes:       Uint8Array;
  contentType: string;
  filename:    string;
}

/**
 * Render the requested format for `(pair, locale)`. Returns the bytes the
 * endpoint should serve. Throws if the locale has no entry or the format's
 * source artefact is missing — both are caught at build time via
 * `availableFormatsForWork`, so this throw only fires on a contract bug.
 */
export function renderDownload(
  pair: WorkPair,
  locale: Locale,
  format: DownloadFormat,
): DownloadResult {
  const entry = locale === "en" ? pair.en : pair.ru;
  if (!entry) {
    throw new Error(`renderDownload: no ${locale} entry for ${pair.kind} #${pair.number}`);
  }
  const filename = `${entry.data.slug}.${format}`;
  const bytes = generate(pair, entry, format);
  return { bytes, contentType: CONTENT_TYPE[format], filename };
}

function generate(pair: WorkPair, entry: WorkEntry, format: DownloadFormat): Uint8Array {
  switch (format) {
    case "md":   return renderMarkdown(pair, entry);
    case "txt":  return renderPlainText(pair, entry);
    case "docx":
    case "pdf":
    case "epub": return copySibling(pair, entry, format);
  }
}

/**
 * Formats available for a specific work in this build environment. `.md` and
 * `.txt` derive trivially from the markdown source; `.docx`, `.pdf`, `.epub`
 * require their committed sibling artefact in the work bundle. Routes use
 * this from `getStaticPaths` so missing artefacts simply mean fewer download
 * endpoints — never a build crash or placeholder bytes.
 */
export function availableFormatsForWork(
  pair: WorkPair,
  lang: Locale,
): DownloadFormat[] {
  return FORMATS_PER_KIND[pair.kind].filter(format => {
    if (format === "md" || format === "txt") return true;
    return siblingArtefactExists(pair, lang, format);
  });
}

// ─────────────────────────────────────────────────────────────────────
// File-copy and cheap text derivations.
// ─────────────────────────────────────────────────────────────────────

function copySibling(pair: WorkPair, entry: WorkEntry, ext: string): Uint8Array {
  const path = resolvePath(workFolder(pair), `${entry.data.lang}.${ext}`);
  if (!existsSync(path)) {
    throw new Error(
      `Missing ${ext.toUpperCase()} for ${pair.kind} #${pair.number} (${entry.data.lang}): ` +
      `${path} is not committed in the work bundle. ` +
      `Run \`uv run scripts/render_downloads.py\` to refresh release artefacts.`,
    );
  }
  return new Uint8Array(readFileSync(path));
}

/** Strip frontmatter and rewrite image refs to site-relative URLs. */
function renderMarkdown(pair: WorkPair, entry: WorkEntry): Uint8Array {
  const raw = readSourceMd(pair, entry);
  const body = stripFrontmatter(raw);
  let rewritten = rewriteImageRefsToSiteRelative(body, pair, entry);
  if (pair.kind === "poem") rewritten = portabilizeVerse(rewritten);
  const header =
    `# ${entry.data.title}\n` +
    `\n` +
    `<!-- Pancratius · CC0 · ${entry.data.lang.toUpperCase()} · ${entry.data.kind} #${entry.data.number} -->\n` +
    `\n`;
  return enc(header + rewritten);
}

/**
 * Verse source uses single newlines for line breaks and blank lines for
 * stanza breaks (rendered by the site via `white-space: pre-line`).
 * Downloadable `.md` should be portable for readers whose Markdown engine
 * uses strict CommonMark — append two trailing spaces (portable hard break)
 * to every verse line whose next line is also non-blank.
 */
function portabilizeVerse(body: string): string {
  const lines = body.split("\n");
  for (let i = 0; i < lines.length - 1; i++) {
    const cur = lines[i];
    const next = lines[i + 1];
    if (cur.trim() && next.trim()) {
      lines[i] = cur.replace(/\s+$/, "") + "  ";
    }
  }
  return lines.join("\n");
}

/** Flatten Markdown to readable plain text. */
function renderPlainText(pair: WorkPair, entry: WorkEntry): Uint8Array {
  const raw = readSourceMd(pair, entry);
  const body = stripFrontmatter(raw);
  const flat = flattenMarkdown(body);
  const header =
    `${entry.data.title}\n` +
    `${"=".repeat([...entry.data.title].length)}\n` +
    `\n` +
    `Pancratius · CC0 · ${entry.data.lang.toUpperCase()} · ${entry.data.kind} #${entry.data.number}\n` +
    `\n`;
  return enc(header + flat);
}

/**
 * Rewrite `./images/X` in a markdown body to `/<segment>/{slug}/images/X`.
 * The `.md` download lives at `/<segment>/{slug}.md` (no trailing slash); the
 * original `./images/X` would resolve to `/<segment>/images/X` and 404.
 */
function rewriteImageRefsToSiteRelative(body: string, pair: WorkPair, entry: WorkEntry): string {
  const base = workUrl(pair.kind, entry.data.slug, entry.data.lang as Locale);
  let out = body.replace(/!\[([^\]]*)]\(\.\/images\/([^)\s]+)\)/g, (_m, alt, file) => {
    return `![${alt}](${base}images/${file})`;
  });
  out = out.replace(/(<img\b[^>]*\bsrc=["'])\.\/images\/([^"']+)(["'])/gi, (_m, pre, file, post) => {
    return `${pre}${base}images/${file}${post}`;
  });
  return out;
}

// ─────────────────────────────────────────────────────────────────────
// Source IO.
// ─────────────────────────────────────────────────────────────────────

function workFolder(pair: WorkPair): string {
  const folder = pair.ru.id.split("--")[0];
  const segment = pair.kind === "book" ? "books" : pair.kind === "poem" ? "poetry" : "projects";
  return resolvePath(CONTENT, segment, folder);
}

function readSourceMd(pair: WorkPair, entry: WorkEntry): Uint8Array {
  const path = resolvePath(workFolder(pair), `${entry.data.lang}.md`);
  return readFileSync(path);
}

function siblingArtefactExists(pair: WorkPair, lang: Locale, ext: string): boolean {
  return existsSync(resolvePath(workFolder(pair), `${lang}.${ext}`));
}

// ─────────────────────────────────────────────────────────────────────
// Plain-text flattening.
// ─────────────────────────────────────────────────────────────────────

// UTF-8 BOM. Static hosts often serve .md/.txt as `text/markdown` /
// `text/plain` without a `charset` parameter; the BOM is the portable signal
// that tells any viewer to decode as UTF-8 instead of falling back to latin-1.
function enc(s: string): Uint8Array {
  return new TextEncoder().encode("﻿" + s);
}

function stripFrontmatter(buf: Uint8Array): string {
  const text = new TextDecoder("utf-8").decode(buf);
  if (!text.startsWith("---")) return text;
  const end = text.indexOf("\n---", 3);
  if (end < 0) return text;
  return text.slice(end + 4).replace(/^\s+/, "");
}

function flattenMarkdown(md: string): string {
  let out = md;
  out = out.replace(/```[a-zA-Z0-9_-]*\n([\s\S]*?)```/g, "$1");
  out = out.replace(/!\[([^\]]*)]\([^)]+\)/g, "$1");
  out = out.replace(/\[([^\]]+)]\([^)]+\)/g, "$1");
  out = out.replace(/^#+\s+/gm, "");
  out = out.replace(/^>\s?/gm, "");
  out = out.replace(/^[\s]*[-*+]\s+/gm, "");
  out = out.replace(/^[\s]*\d+\.\s+/gm, "");
  out = out.replace(/(\*\*|__)(.*?)\1/g, "$2");
  out = out.replace(/(\*|_)(.*?)\1/g, "$2");
  out = out.replace(/~~(.*?)~~/g, "$1");
  out = out.replace(/\\\n/g, "\n");
  out = out.replace(/\n{3,}/g, "\n\n");
  return out.trim() + "\n";
}
