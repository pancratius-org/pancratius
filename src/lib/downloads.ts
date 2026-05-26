// Download dispatch. CI-side only: cheap text derivations for `.md` and
// `.txt`, file copies for the heavy formats (`.docx`, `.pdf`, `.epub`).
//
// Per `docs/downloads.md`, PDF/EPUB/DOCX are committed release artefacts
// inside the work bundle (`src/content/<kind>/<work>/<lang>.{docx,pdf,epub}`).
// The site build never runs pandoc, typst, or any document converter.
// Local refresh of those artefacts is the job of
// `pancratius downloads render`, not this module.

import { existsSync, readFileSync } from "node:fs";
import { resolve as resolvePath } from "node:path";

import { DEFAULT_LOCALE, type Locale } from "./i18n";
import { workBundleKey } from "./body-images";
import { markdownToPlainText } from "./publication/plain-text";
import { renderPublicWorkMarkdown } from "./publication/public-markdown";
import { COLLECTION_OF, type WorkEntry, type WorkPair, type WorkPairKind } from "./works";

export type DownloadFormat = "md" | "txt" | "docx" | "pdf" | "epub";

/**
 * Formats that may exist for each kind, per the contract. Only WORKS
 * (books/poems) have downloads — projects are themed sections, not works, and
 * never emit download routes (the key type is `WorkPairKind`, which excludes
 * `project`).
 */
export const FORMATS_PER_KIND: Record<WorkPairKind, DownloadFormat[]> = {
  book:    ["md", "txt", "docx", "pdf", "epub"],
  poem:    ["md", "txt", "docx", "pdf"],
};

const REPO_ROOT = process.cwd();
const CONTENT   = resolvePath(REPO_ROOT, "src", "content");
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

export interface DownloadRenderOptions {
  origin: string;
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
  options: DownloadRenderOptions,
): DownloadResult {
  // Existence: a download for this locale exists only if the locale was
  // authored — never fall back to another locale's content.
  const entry = pair.entries[locale];
  if (!entry) {
    throw new Error(`renderDownload: no ${locale} entry for ${pair.kind} #${pair.number}`);
  }
  const filename = `${entry.data.slug}.${format}`;
  const bytes = generate(pair, entry, format, options);
  return { bytes, contentType: CONTENT_TYPE[format], filename };
}

function generate(
  pair: WorkPair,
  entry: WorkEntry,
  format: DownloadFormat,
  options: DownloadRenderOptions,
): Uint8Array {
  switch (format) {
    case "md":   return renderMarkdown(pair, entry, options);
    case "txt":  return renderPlainText(pair, entry, options);
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
      `Run \`uv run pancratius downloads render\` to refresh release artefacts.`,
    );
  }
  return new Uint8Array(readFileSync(path));
}

/** Strip frontmatter and rewrite image refs to public absolute URLs. */
function renderMarkdown(pair: WorkPair, entry: WorkEntry, options: DownloadRenderOptions): Uint8Array {
  const raw = readSourceMd(pair, entry);
  return enc(renderPublicWorkMarkdown(raw, {
    origin: options.origin,
    work: {
      kind: pair.kind,
      bundleKey: workBundleKey(pair),
    },
  }));
}

/** Flatten Markdown to readable plain text. */
function renderPlainText(pair: WorkPair, entry: WorkEntry, options: DownloadRenderOptions): Uint8Array {
  const raw = readSourceMd(pair, entry);
  const body = renderPublicWorkMarkdown(raw, {
    origin: options.origin,
    work: {
      kind: pair.kind,
      bundleKey: workBundleKey(pair),
    },
  });
  const flat = markdownToPlainText(body);
  const header =
    `${entry.data.title}\n` +
    `${"=".repeat([...entry.data.title].length)}\n` +
    `\n` +
    `Pancratius · CC0 · ${entry.data.lang.toUpperCase()} · ${entry.data.kind} #${entry.data.number}\n` +
    `\n`;
  return enc(header + flat);
}

// ─────────────────────────────────────────────────────────────────────
// Source IO.
// ─────────────────────────────────────────────────────────────────────

function workFolder(pair: WorkPair): string {
  const folder = pair.entries[DEFAULT_LOCALE]!.id.split("--")[0];
  return resolvePath(CONTENT, COLLECTION_OF[pair.kind], folder);
}

function readSourceMd(pair: WorkPair, entry: WorkEntry): string {
  const path = resolvePath(workFolder(pair), `${entry.data.lang}.md`);
  return readFileSync(path, "utf-8");
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
