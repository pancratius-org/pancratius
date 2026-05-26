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

function cleanPublicMarkdownBody(
  body: string,
  work: PublicationWork,
  origin: string,
): string {
  let out = body.replace(/\r\n?/g, "\n");

  out = out.replace(/<blockquote\s+class=["']epigraph["'][^>]*>\s*([\s\S]*?)\s*<\/blockquote>/gi, (_match, inner: string) => {
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

  out = out.replace(/<div\s+class=["']verse-block["'][^>]*>\s*([\s\S]*?)\s*<\/div>/gi, (_match, inner: string) => {
    return `\n\n${htmlInlineToMarkdown(inner).trim()}\n\n`;
  });

  out = out.replace(/<div\s+class=["']answer-block["'][^>]*>\s*([\s\S]*?)\s*<\/div>/gi, (_match, inner: string) => {
    return `\n\n${htmlInlineToMarkdown(inner).trim()}\n\n`;
  });

  out = out.replace(/<p\s+class=["']signature["'][^>]*>\s*([\s\S]*?)\s*<\/p>/gi, (_match, inner: string) => {
    return `\n\n${htmlInlineToMarkdown(inner).trim()}\n\n`;
  });

  out = out.replace(/<img\b([^>]*?)\/?>/gi, (_match, attrs: string) => {
    const src = attrValue(attrs, "src");
    if (!src) return "";
    const alt = htmlInlineToMarkdown(attrValue(attrs, "alt")).replace(/\n+/g, " ").trim();
    return `\n\n![${alt}](${publicImageUrl(work, src, origin)})\n\n`;
  });

  out = rewriteMarkdownImageRefs(out, work, origin);

  out = htmlInlineToMarkdown(out);
  out = out.replace(/[ \t]+\n/g, "\n");
  out = out.replace(/\n{4,}/g, "\n\n\n");
  return out.trim() + "\n";
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

  const file = trimmed.replace(/^\.?\//, "");
  if (file.startsWith("images/")) {
    return `${origin}/assets/${WORK_SEGMENT[work.kind]}/${work.bundleKey}/${file}`;
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
    if (current.trim() && next.trim()) {
      lines[i] = current.replace(/\s+$/, "") + "  ";
    }
  }
  return lines.join("\n");
}

function normalizeOrigin(origin: string): string {
  const url = new URL(origin);
  return url.origin;
}

function htmlInlineToMarkdown(input: string): string {
  let out = input;
  out = out.replace(/<br\s*\/?>/gi, "\n");
  out = out.replace(/<a\b[^>]*href=["']([^"']+)["'][^>]*>([\s\S]*?)<\/a>/gi, (_match, href: string, text: string) => {
    return `[${htmlInlineToMarkdown(text).trim()}](${decodeHtmlEntities(href)})`;
  });
  out = out.replace(/<strong\b[^>]*>([\s\S]*?)<\/strong>/gi, (_match, text: string) => `**${htmlInlineToMarkdown(text).trim()}**`);
  out = out.replace(/<b\b[^>]*>([\s\S]*?)<\/b>/gi, (_match, text: string) => `**${htmlInlineToMarkdown(text).trim()}**`);
  out = out.replace(/<em\b[^>]*>([\s\S]*?)<\/em>/gi, (_match, text: string) => `*${htmlInlineToMarkdown(text).trim()}*`);
  out = out.replace(/<i\b[^>]*>([\s\S]*?)<\/i>/gi, (_match, text: string) => `*${htmlInlineToMarkdown(text).trim()}*`);
  out = out.replace(/<\/?p\b[^>]*>/gi, "");
  out = out.replace(/<\/?div\b[^>]*>/gi, "");
  out = out.replace(/<[^>]+>/g, "");
  return decodeHtmlEntities(out);
}

function attrValue(attrs: string, name: string): string {
  const re = new RegExp(`${name}\\s*=\\s*(?:"([^"]*)"|'([^']*)')`, "i");
  const got = attrs.match(re);
  return got ? (got[1] ?? got[2] ?? "") : "";
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
