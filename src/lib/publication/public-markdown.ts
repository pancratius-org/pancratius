import { SEGMENT_OF, type CorpusWorkKind } from "../kinds.ts";
import { parseMarkdownDocument } from "./source.ts";

export interface PublicationWork {
  kind: CorpusWorkKind;
  bundleKey: string;
}

export interface RenderPublicWorkMarkdownOptions {
  work: PublicationWork;
  origin: string;
}

const WORK_SEGMENT: Record<CorpusWorkKind, string> = {
  book: SEGMENT_OF.book,
  poem: SEGMENT_OF.poem,
};

const MARKDOWN_IMAGE_RE = /!\[([^\]]*)]\(\s*(<?)([^<>\s)]+)(>?)(\s+(?:"[^"]*"|'[^']*'|\([^)]*\)))?\s*\)/g;
const URL_SCHEME_RE = /^([a-zA-Z][a-zA-Z0-9+.\-]*):/;
const ALLOWED_HREF_SCHEMES = new Set(["http", "https", "mailto"]);
const HTML_NAMED_ENTITIES: Partial<Record<string, string>> = {
  amp: "&",
  apos: "'",
  colon: ":",
  gt: ">",
  lt: "<",
  nbsp: " ",
  quot: "\"",
};
const HTML_RAW_MARKUP_RE = /<!--[\s\S]*?(?:-->|$)|<![^\s<>][^>]*(?:>|$)|<\?[A-Za-z][\s\S]*?(?:\?>|$)/g;
const HTML_TAG_RE = /<\/?([A-Za-z][A-Za-z0-9:-]*)(?:\s+[^<>]*?)?\s*\/?>/g;
const HTML_ATTR_RE = /\s+([A-Za-z_:][A-Za-z0-9_:.-]*)\s*=\s*(?:"([^"]*)"|'([^']*)')/g;
const SPAN_DIR_VALUES = new Set(["ltr", "rtl", "auto"]);

export function renderPublicWorkMarkdown(
  sourceMarkdown: string,
  options: RenderPublicWorkMarkdownOptions,
): string {
  let body = cleanPublicMarkdownBody(
    parseMarkdownDocument(sourceMarkdown).body,
    options.work,
    normalizeOrigin(options.origin),
  );
  if (options.work.kind === "poem") body = portabilizeVerse(body);
  return body.trim() + "\n";
}

export function publicWorkMarkdownAssetPaths(
  sourceMarkdown: string,
  work: PublicationWork,
): string[] {
  const context = publicMarkdownContext(work);
  const body = parseMarkdownDocument(sourceMarkdown).body.replace(/\r\n?/g, "\n");
  const paths = new Set<string>();

  collectHtmlImageAssetPaths(body, work, context, paths);
  collectMarkdownImageAssetPaths(body, work, context, paths);

  return Array.from(paths).sort();
}

function collectHtmlImageAssetPaths(
  body: string,
  work: PublicationWork,
  context: string,
  paths: Set<string>,
): void {
  for (const match of body.matchAll(/<img\b([^>]*?)\/?>/gi)) {
    const tag = match[0];
    const attrs = match[1];
    if (attrs === undefined) {
      throw unsupportedHtmlError(tag, context, "image attribute parser matched without attributes");
    }
    const parsed = parseHtmlAttributes(attrs, tag, context);
    requireOnlyAttributes(parsed, ["src", "alt"], tag, context);
    const src = parsed.get("src");
    if (!src) throw unsupportedHtmlError(tag, context, "missing required src attribute");
    const assetPath = publicImageAssetPath(work, src);
    if (assetPath) paths.add(assetPath);
  }
}

function collectMarkdownImageAssetPaths(
  body: string,
  work: PublicationWork,
  context: string,
  paths: Set<string>,
): void {
  for (const match of body.matchAll(MARKDOWN_IMAGE_RE)) {
    const openAngle = match[2] ?? "";
    const target = match[3];
    const closeAngle = match[4] ?? "";
    if ((openAngle && !closeAngle) || (!openAngle && closeAngle)) continue;
    if (target === undefined) {
      throw new Error(`Markdown image parser matched without a target in ${context}`);
    }
    const assetPath = publicImageAssetPath(work, target);
    if (assetPath) paths.add(assetPath);
  }
}

function cleanPublicMarkdownBody(
  body: string,
  work: PublicationWork,
  origin: string,
): string {
  const context = publicMarkdownContext(work);
  let out = body.replace(/\r\n?/g, "\n");

  out = out.replace(/<blockquote\s+class=(["'])epigraph\1>\s*([\s\S]*?)\s*<\/blockquote>/gi, (_match, _quote: string, inner: string) => {
    const footer = inner.match(/<footer>([\s\S]*?)<\/footer>/i)?.[1] ?? "";
    const withoutFooter = inner.replace(/<footer>[\s\S]*?<\/footer>/i, "");
    const text = htmlInlineToMarkdown(stripPlainParagraphTags(withoutFooter), context)
      .split("\n")
      .map(line => line.trim())
      .filter(Boolean)
      .map(line => `> ${line}`)
      .join("\n");
    const source = footer.trim() ? `\n> — ${htmlInlineToMarkdown(footer, context).trim()}` : "";
    return `\n\n${text}${source}\n\n`;
  });

  out = out.replace(/<div\s+class=(["'])verse-block\1>\s*([\s\S]*?)\s*<\/div>/gi, (_match, _quote: string, inner: string) => {
    return `\n\n${portabilizeVerse(htmlInlineToMarkdown(stripPlainParagraphTags(inner), context).trim())}\n\n`;
  });

  out = out.replace(/<p\s+class=(["'])signature\1>\s*([\s\S]*?)\s*<\/p>/gi, (_match, _quote: string, inner: string) => {
    return `\n\n${htmlInlineToMarkdown(inner, context).trim()}\n\n`;
  });

  out = out.replace(/<img\b([^>]*?)\/?>/gi, (match, attrs: string) => {
    const parsed = parseHtmlAttributes(attrs, match, context);
    requireOnlyAttributes(parsed, ["src", "alt"], match, context);
    const src = parsed.get("src");
    if (!src) throw unsupportedHtmlError(match, context, "missing required src attribute");
    const alt = htmlInlineToMarkdown(parsed.get("alt") ?? "", context).replace(/\n+/g, " ").trim();
    return `\n\n![${alt}](${publicImageUrl(work, src, origin)})\n\n`;
  });

  out = rewriteMarkdownImageRefs(out, work, origin);

  out = htmlInlineToMarkdown(out, context);
  out = trimTrailingWhitespacePreservingHardBreaks(out);
  out = out.replace(/\n{4,}/g, "\n\n\n");
  return out.trim() + "\n";
}

function publicMarkdownContext(work: PublicationWork): string {
  return `public Markdown for ${work.kind}/${work.bundleKey}`;
}

function rewriteMarkdownImageRefs(
  input: string,
  work: PublicationWork,
  origin: string,
): string {
  return input.replace(
    MARKDOWN_IMAGE_RE,
    (match, alt: string, openAngle: string, target: string, closeAngle: string, title = "") => {
      if ((openAngle && !closeAngle) || (!openAngle && closeAngle)) return match;
      const rewritten = publicImageUrl(work, target, origin);
      if (rewritten === target) return match;
      return `![${alt}](${rewritten}${title})`;
    },
  );
}

function publicImageUrl(work: PublicationWork, src: string, origin: string): string {
  const trimmed = decodeHtmlEntities(src.trim());
  if (/^https?:\/\//i.test(trimmed)) return trimmed;

  const scheme = URL_SCHEME_RE.exec(trimmed)?.[1];
  if (scheme) {
    throw new Error(`Unsupported public Markdown image URL scheme ${JSON.stringify(scheme)} in ${JSON.stringify(src)}`);
  }

  if (trimmed.startsWith("/")) return new URL(trimmed, `${origin}/`).toString();

  const assetPath = publicImageAssetPath(work, trimmed);
  if (assetPath) {
    return `${origin}/${assetPath}`;
  }

  throw new Error(
    `Unsupported local public Markdown image path ${JSON.stringify(src)}. ` +
    "Use a remote URL, a root-relative URL, or a work-bundle images/... path.",
  );
}

function publicImageAssetPath(work: PublicationWork, src: string): string | null {
  const trimmed = decodeHtmlEntities(src.trim());
  if (/^https?:\/\//i.test(trimmed)) return null;

  const scheme = URL_SCHEME_RE.exec(trimmed)?.[1];
  if (scheme) {
    throw new Error(`Unsupported public Markdown image URL scheme ${JSON.stringify(scheme)} in ${JSON.stringify(src)}`);
  }

  if (trimmed.startsWith("/")) {
    return trimmed.startsWith("/assets/") ? trimmed.slice(1) : null;
  }

  const file = trimmed.replace(/^\.?\//, "");
  if (file.startsWith("images/")) {
    return `assets/${WORK_SEGMENT[work.kind]}/${work.bundleKey}/${file}`;
  }

  throw new Error(
    `Unsupported local public Markdown image path ${JSON.stringify(src)}. ` +
    "Use a remote URL, a root-relative URL, or a work-bundle images/... path.",
  );
}

function portabilizeVerse(body: string): string {
  const lines = body.split("\n");
  for (let i = 0; i < lines.length - 1; i++) {
    const current = lines[i];
    const next = lines[i + 1];
    if (current === undefined || next === undefined) {
      throw new Error("verse lineation loop exceeded its line bounds");
    }
    if (current.trim() && next.trim()) {
      lines[i] = current.replace(/\s+$/, "") + "  ";
    }
  }
  return lines.join("\n");
}

function trimTrailingWhitespacePreservingHardBreaks(body: string): string {
  return body.split("\n").map((line) => {
    if (line.endsWith("  ")) return line.replace(/[ \t]+$/, "  ");
    return line.replace(/[ \t]+$/, "");
  }).join("\n");
}

function normalizeOrigin(origin: string): string {
  const url = new URL(origin);
  return url.origin;
}

function htmlInlineToMarkdown(input: string, context = "public Markdown"): string {
  let out = input;
  out = out.replace(/<br\s*\/?>/gi, "\n");
  out = out.replace(/<a\b([^>]*)>([\s\S]*?)<\/a>/gi, (match, attrs: string, text: string) => {
    const tag = openingTag(match);
    const parsed = parseHtmlAttributes(attrs, tag, context);
    requireOnlyAttributes(parsed, ["href"], tag, context);
    const rawHref = parsed.get("href");
    if (rawHref === undefined) throw unsupportedHtmlError(tag, context, "missing required href attribute");
    const href = validatePublicHref(rawHref, tag, context);
    if (!href) throw unsupportedHtmlError(tag, context, "missing required href attribute");
    return `[${htmlInlineToMarkdown(text, context).trim()}](${href})`;
  });
  out = out.replace(/<strong\b([^>]*)>([\s\S]*?)<\/strong>/gi, (match, attrs: string, text: string) => {
    requireNoAttributes(attrs, openingTag(match), context);
    return `**${htmlInlineToMarkdown(text, context).trim()}**`;
  });
  out = out.replace(/<em\b([^>]*)>([\s\S]*?)<\/em>/gi, (match, attrs: string, text: string) => {
    requireNoAttributes(attrs, openingTag(match), context);
    return `*${htmlInlineToMarkdown(text, context).trim()}*`;
  });
  out = out.replace(/<span\b([^>]*)>([\s\S]*?)<\/span>/gi, (match, attrs: string, text: string) => {
    const tag = openingTag(match);
    const parsed = parseHtmlAttributes(attrs, tag, context);
    requireOnlyAttributes(parsed, ["dir"], tag, context);
    const dir = parsed.get("dir");
    if (!dir || !SPAN_DIR_VALUES.has(dir.toLowerCase())) {
      throw unsupportedHtmlError(tag, context, "span requires dir=\"ltr\", dir=\"rtl\", or dir=\"auto\"");
    }
    return htmlInlineToMarkdown(text, context).trim();
  });
  refuseRemainingHtml(out, context);
  return decodeHtmlEntities(out);
}

function stripPlainParagraphTags(input: string): string {
  return input.replace(/<\/?p>/gi, "");
}

function openingTag(match: string): string {
  return match.match(/^<[^>]+>/)?.[0] ?? match;
}

function parseHtmlAttributes(attrs: string, tag: string, context: string): Map<string, string> {
  const parsed = new Map<string, string>();
  let end = 0;
  for (const match of attrs.matchAll(HTML_ATTR_RE)) {
    if (attrs.slice(end, match.index).trim()) {
      throw unsupportedHtmlError(tag, context, "attributes must be quoted name=value pairs");
    }
    const rawName = match[1];
    if (rawName === undefined) {
      throw unsupportedHtmlError(tag, context, "attribute parser matched without a name");
    }
    const name = rawName.toLowerCase();
    if (parsed.has(name)) {
      throw unsupportedHtmlError(tag, context, `duplicate ${name} attribute`);
    }
    parsed.set(name, match[2] ?? match[3] ?? "");
    end = match.index + match[0].length;
  }
  if (attrs.slice(end).trim()) {
    throw unsupportedHtmlError(tag, context, "attributes must be quoted name=value pairs");
  }
  return parsed;
}

function requireNoAttributes(attrs: string, tag: string, context: string): void {
  if (attrs.trim()) {
    throw unsupportedHtmlError(tag, context, "attributes are not supported on this tag");
  }
}

function requireOnlyAttributes(
  attrs: Map<string, string>,
  allowed: readonly string[],
  tag: string,
  context: string,
): void {
  const allowedSet = new Set(allowed);
  const unsupported = Array.from(attrs.keys()).filter((name) => !allowedSet.has(name));
  if (unsupported.length) {
    throw unsupportedHtmlError(tag, context, `unsupported attribute ${JSON.stringify(unsupported[0])}`);
  }
}

function refuseRemainingHtml(input: string, context: string): void {
  HTML_RAW_MARKUP_RE.lastIndex = 0;
  const rawMarkup = HTML_RAW_MARKUP_RE.exec(input);
  HTML_RAW_MARKUP_RE.lastIndex = 0;
  if (rawMarkup) {
    throw unsupportedHtmlError(rawMarkup[0], context);
  }

  HTML_TAG_RE.lastIndex = 0;
  const match = HTML_TAG_RE.exec(input);
  HTML_TAG_RE.lastIndex = 0;
  if (!match) return;
  throw unsupportedHtmlError(match[0], context);
}

function unsupportedHtmlError(tag: string, context: string, reason?: string): Error {
  const suffix = reason ? `: ${reason}` : "";
  return new Error(
    `Unsupported raw HTML ${JSON.stringify(tag)} in ${context}${suffix}. ` +
    "Supported raw HTML is limited to the documented publication wrappers and inline strong, em, br, img, a, and span[dir] tags.",
  );
}

function validatePublicHref(rawHref: string, tag: string, context: string): string {
  const href = decodeHtmlEntities(rawHref).trim();
  const scheme = URL_SCHEME_RE.exec(href)?.[1];
  if (scheme && !ALLOWED_HREF_SCHEMES.has(scheme.toLowerCase())) {
    throw unsupportedHtmlError(tag, context, `unsupported href URL scheme ${JSON.stringify(scheme.toLowerCase())}`);
  }
  return href;
}

function decodeHtmlEntities(s: string): string {
  return s.replace(/&(#x[0-9a-f]+|#\d+|[a-z][a-z0-9]+);/gi, (entity, name: string) => {
    const lower = name.toLowerCase();
    if (lower.startsWith("#x")) {
      const codepoint = Number.parseInt(lower.slice(2), 16);
      return decodeCodepointEntity(entity, codepoint);
    }
    if (lower.startsWith("#")) {
      const codepoint = Number.parseInt(lower.slice(1), 10);
      return decodeCodepointEntity(entity, codepoint);
    }
    return HTML_NAMED_ENTITIES[lower] ?? entity;
  });
}

function decodeCodepointEntity(entity: string, codepoint: number): string {
  if (!Number.isInteger(codepoint) || codepoint < 0 || codepoint > 0x10ffff) return entity;
  try {
    return String.fromCodePoint(codepoint);
  } catch {
    return entity;
  }
}
