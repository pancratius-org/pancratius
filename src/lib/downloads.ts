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
const SITE_ORIGIN = (process.env.PUBLIC_SITE_URL ?? "https://pancratius.ru").replace(/\/+$/, "");

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

/** Strip frontmatter and rewrite image refs to public absolute URLs. */
function renderMarkdown(pair: WorkPair, entry: WorkEntry): Uint8Array {
  const raw = readSourceMd(pair, entry);
  let body = cleanPublicMarkdownBody(stripFrontmatter(raw), pair, entry);
  if (pair.kind === "poem") body = portabilizeVerse(body);
  return enc(body.trim() + "\n");
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
  const body = cleanPublicMarkdownBody(stripFrontmatter(raw), pair, entry);
  const flat = flattenMarkdown(body);
  const header =
    `${entry.data.title}\n` +
    `${"=".repeat([...entry.data.title].length)}\n` +
    `\n` +
    `Pancratius · CC0 · ${entry.data.lang.toUpperCase()} · ${entry.data.kind} #${entry.data.number}\n` +
    `\n`;
  return enc(header + flat);
}

function workImageUrl(pair: WorkPair, entry: WorkEntry, src: string): string {
  const trimmed = decodeHtmlEntities(src.trim());
  if (/^https?:\/\//i.test(trimmed)) return trimmed;
  if (trimmed.startsWith("/")) return new URL(trimmed, `${SITE_ORIGIN}/`).toString();
  const file = trimmed.replace(/^\.?\//, "");
  if (file.startsWith("images/")) {
    const base = workUrl(pair.kind, entry.data.slug, entry.data.lang as Locale);
    return new URL(`${base}${file}`, `${SITE_ORIGIN}/`).toString();
  }
  return trimmed;
}

function attrValue(attrs: string, name: string): string {
  const re = new RegExp(`${name}\\s*=\\s*(?:"([^"]*)"|'([^']*)')`, "i");
  const got = attrs.match(re);
  return got ? (got[1] ?? got[2] ?? "") : "";
}

function cleanPublicMarkdownBody(body: string, pair: WorkPair, entry: WorkEntry): string {
  let out = body.replace(/\r\n?/g, "\n");

  out = out.replace(/<blockquote\s+class=["']epigraph["'][^>]*>\s*([\s\S]*?)\s*<\/blockquote>/gi, (_m, inner) => {
    const footer = inner.match(/<footer[^>]*>([\s\S]*?)<\/footer>/i)?.[1] ?? "";
    const withoutFooter = inner.replace(/<footer[^>]*>[\s\S]*?<\/footer>/i, "");
    const text = htmlInlineToMarkdown(withoutFooter)
      .split("\n")
      .map(line => line.trim())
      .filter(Boolean)
      .map(line => `> ${line}`)
      .join("\n");
    const source = footer.trim() ? `\n> — ${htmlInlineToMarkdown(footer).trim()}` : "";
    return `\n\n${text}${source}\n\n`;
  });

  out = out.replace(/<div\s+class=["']verse-block["'][^>]*>\s*([\s\S]*?)\s*<\/div>/gi, (_m, inner) => {
    return `\n\n${htmlInlineToMarkdown(inner).trim()}\n\n`;
  });

  out = out.replace(/<p\s+class=["']signature["'][^>]*>\s*([\s\S]*?)\s*<\/p>/gi, (_m, inner) => {
    return `\n\n${htmlInlineToMarkdown(inner).trim()}\n\n`;
  });

  out = out.replace(/<img\b([^>]*?)\/?>/gi, (_m, attrs) => {
    const src = attrValue(attrs, "src");
    if (!src) return "";
    const alt = htmlInlineToMarkdown(attrValue(attrs, "alt")).replace(/\n+/g, " ").trim();
    return `\n\n![${alt}](${workImageUrl(pair, entry, src)})\n\n`;
  });

  out = out.replace(/!\[([^\]]*)]\(\.\/images\/([^)\s]+)\)/g, (_m, alt, file) => {
    return `![${alt}](${workImageUrl(pair, entry, `./images/${file}`)})`;
  });

  out = htmlInlineToMarkdown(out);
  out = out.replace(/[ \t]+\n/g, "\n");
  out = out.replace(/\n{4,}/g, "\n\n\n");
  return out.trim() + "\n";
}

function htmlInlineToMarkdown(input: string): string {
  let out = input;
  out = out.replace(/<br\s*\/?>/gi, "\n");
  out = out.replace(/<a\b[^>]*href=["']([^"']+)["'][^>]*>([\s\S]*?)<\/a>/gi, (_m, href, text) => {
    return `[${htmlInlineToMarkdown(text).trim()}](${decodeHtmlEntities(href)})`;
  });
  out = out.replace(/<strong\b[^>]*>([\s\S]*?)<\/strong>/gi, (_m, text) => `**${htmlInlineToMarkdown(text).trim()}**`);
  out = out.replace(/<b\b[^>]*>([\s\S]*?)<\/b>/gi, (_m, text) => `**${htmlInlineToMarkdown(text).trim()}**`);
  out = out.replace(/<em\b[^>]*>([\s\S]*?)<\/em>/gi, (_m, text) => `*${htmlInlineToMarkdown(text).trim()}*`);
  out = out.replace(/<i\b[^>]*>([\s\S]*?)<\/i>/gi, (_m, text) => `*${htmlInlineToMarkdown(text).trim()}*`);
  out = out.replace(/<\/?p\b[^>]*>/gi, "");
  out = out.replace(/<\/?div\b[^>]*>/gi, "");
  out = out.replace(/<[^>]+>/g, "");
  return decodeHtmlEntities(out);
}

function decodeHtmlEntities(s: string): string {
  return s
    .replace(/&nbsp;/g, " ")
    .replace(/&quot;/g, "\"")
    .replace(/&#39;/g, "'")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&amp;/g, "&");
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
