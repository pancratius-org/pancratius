import { parseMarkdownDocument } from "./source.ts";
import { markdownToPlainText } from "./plain-text.ts";

/** The poem's opening verse as plain text, up to `lineCount` lines. Some imported
 *  bodies carry a header before the verse (a date stamp, a bold title, an author
 *  link, the repeated title); those are skipped so the preview opens on verse,
 *  and inline markdown is stripped so no raw `**`/links leak into the text. */
export function poemPreviewLines(bodyMarkdown: string, title: string, lineCount: number): string {
  const lines = bodyLines(bodyMarkdown);
  let start = 0;
  for (const line of lines) {
    if (line.trim() !== "" && !isHeaderLine(line, title)) break;
    start++;
  }
  return lines
    .slice(start, start + lineCount)
    .map(line => (line.trim() === "" ? "" : markdownToPlainText(line).trim()))
    .join("\n");
}

/** The opening verse line with trailing punctuation replaced by a single ellipsis,
 *  so every incipit trails off the same way. "" when there is no verse. */
export function poemIncipit(bodyMarkdown: string, title: string): string {
  const line = poemPreviewLines(bodyMarkdown, title, 1);
  if (line === "") return "";
  return `${line.replace(/[\s.,…—–-]+$/u, "")}…`;
}

/** The opening stanza of a poem body, verbatim — no title-line stripping. For a
 *  curated hero pull-quote where the first line (which may double as the title)
 *  is itself the hook. */
export function poemLeadStanza(bodyMarkdown: string): string {
  const stanza: string[] = [];
  for (const line of bodyLines(bodyMarkdown)) {
    if (line.trim() === "") break;
    stanza.push(line);
  }
  return stanza.join("\n");
}

export function isPoemTitleLine(line: string, title: string): boolean {
  return normalizedPoemTitleLine(line) === normalizedPoemTitleLine(title);
}

/** A non-verse "header" line some imported poems carry before the verse: a
 *  markdown heading, a bold/bold-italic title, a link attribution, a date stamp,
 *  a stray escaped-asterisk note, or the title repeated. */
function isHeaderLine(line: string, title: string): boolean {
  const t = line.trim();
  if (t === "") return false;
  if (/^#{1,6}\s/.test(t)) return true;
  if (/^\[.+]\(.+\)$/.test(t)) return true;
  if (/^(\*\*\*|\*\*|__)[\s\S]+(\*\*\*|\*\*|__)$/.test(t)) return true;
  if (/\\\*/.test(t)) return true;
  if (/\b\d{4}\b/.test(t) && /[:,]|\bAM\b|\bPM\b/.test(t)) return true;
  return isPoemTitleLine(t, title);
}

/** Display lines of a poem body. Generated bodies encode lineation as two-
 *  trailing-space CommonMark hard breaks; these previews are plain text, not
 *  Markdown-rendered, so we keep newline-separated lines and drop the hard-break
 *  spaces from each. */
function bodyLines(bodyMarkdown: string): string[] {
  const body = parseMarkdownDocument(bodyMarkdown).body.trim();
  return dropLeadingBlankLines(body.split("\n").map(line => line.replace(/ +$/, "")));
}

function dropLeadingBlankLines(lines: readonly string[]): string[] {
  let start = 0;
  for (const line of lines) {
    if (line.trim() !== "") break;
    start++;
  }
  return lines.slice(start);
}

function normalizedPoemTitleLine(value: string): string {
  return value.replace(/[…*]+$/g, "").replace(/[,;:!?.]/g, "").trim().toLowerCase();
}
