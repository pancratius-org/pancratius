export type PublicMarkdownKind = "book" | "poem" | "project";

export interface PublicMarkdownWork {
  kind: PublicMarkdownKind;
  workKey: string;
  isVerse?: boolean;
}

const WORK_SEGMENT: Record<PublicMarkdownKind, string> = {
  book: "books",
  poem: "poetry",
  project: "projects",
};

const DEFAULT_SITE_ORIGIN = "https://pancratius.ru";

export function renderPublicMarkdown(
  raw: string,
  work: PublicMarkdownWork,
  siteOrigin = process.env.PUBLIC_SITE_URL ?? DEFAULT_SITE_ORIGIN,
): string {
  let body = cleanPublicMarkdownBody(stripFrontmatter(raw), work, siteOrigin);
  if (work.isVerse) body = portabilizeVerse(body);
  return body.trim() + "\n";
}

export function stripFrontmatter(text: string): string {
  if (!text.startsWith("---")) return text;
  const end = text.indexOf("\n---", 3);
  return end >= 0 ? text.slice(end + 4).replace(/^\s*\n/, "") : text;
}

export function flattenPublicMarkdown(body: string): string {
  let out = body;
  out = out.replace(/```[a-zA-Z0-9_-]*\n([\s\S]*?)```/g, "$1");
  out = out.replace(/^#{1,6}\s+/gm, "");
  out = out.replace(/!\[([^\]]*)]\([^)]+\)/g, "$1");
  out = out.replace(/\[([^\]]+)]\([^)]+\)/g, "$1");
  out = out.replace(/^>\s?/gm, "");
  out = out.replace(/^[\t ]*[-*+]\s+/gm, "");
  out = out.replace(/^[\t ]*\d+\.\s+/gm, "");
  out = out.replace(/(\*\*|__)(.*?)\1/g, "$2");
  out = out.replace(/(\*|_)(.*?)\1/g, "$2");
  out = out.replace(/~~(.*?)~~/g, "$1");
  out = out.replace(/`([^`]+)`/g, "$1");
  out = out.replace(/\\\n/g, "\n");
  out = out.replace(/\n{3,}/g, "\n\n");
  return out.trim() + "\n";
}

function cleanPublicMarkdownBody(
  body: string,
  work: PublicMarkdownWork,
  siteOrigin: string,
): string {
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

  out = out.replace(/<div\s+class=["']answer-block["'][^>]*>\s*([\s\S]*?)\s*<\/div>/gi, (_m, inner) => {
    return `\n\n${htmlInlineToMarkdown(inner).trim()}\n\n`;
  });

  out = out.replace(/<p\s+class=["']signature["'][^>]*>\s*([\s\S]*?)\s*<\/p>/gi, (_m, inner) => {
    return `\n\n${htmlInlineToMarkdown(inner).trim()}\n\n`;
  });

  out = out.replace(/<img\b([^>]*?)\/?>/gi, (_m, attrs) => {
    const src = attrValue(attrs, "src");
    if (!src) return "";
    const alt = htmlInlineToMarkdown(attrValue(attrs, "alt")).replace(/\n+/g, " ").trim();
    return `\n\n![${alt}](${workImageUrl(work, src, siteOrigin)})\n\n`;
  });

  out = out.replace(/!\[([^\]]*)]\(\.\/images\/([^)\s]+)\)/g, (_m, alt, file) => {
    return `![${alt}](${workImageUrl(work, `./images/${file}`, siteOrigin)})`;
  });

  out = htmlInlineToMarkdown(out);
  out = out.replace(/[ \t]+\n/g, "\n");
  out = out.replace(/\n{4,}/g, "\n\n\n");
  return out.trim() + "\n";
}

function workImageUrl(work: PublicMarkdownWork, src: string, siteOrigin: string): string {
  const origin = siteOrigin.replace(/\/+$/, "");
  const trimmed = decodeHtmlEntities(src.trim());
  if (/^https?:\/\//i.test(trimmed)) return trimmed;
  if (trimmed.startsWith("/")) return new URL(trimmed, `${origin}/`).toString();
  const file = trimmed.replace(/^\.?\//, "");
  if (file.startsWith("images/")) {
    return `${origin}/assets/${WORK_SEGMENT[work.kind]}/${work.workKey}/${file}`;
  }
  return trimmed;
}

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
