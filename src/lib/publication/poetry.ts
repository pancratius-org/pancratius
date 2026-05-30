import { parseMarkdownDocument } from "./source.ts";

export function poemPreviewLines(bodyMarkdown: string, title: string, lineCount: number): string {
  const lines = poemBodyLines(bodyMarkdown, title);
  return lines.slice(0, lineCount).join("\n");
}

export function poemFirstStanza(bodyMarkdown: string, title: string): string {
  const stanza: string[] = [];
  for (const line of poemBodyLines(bodyMarkdown, title)) {
    if (line.trim() === "") break;
    stanza.push(line);
  }
  return stanza.join("\n");
}

export function isPoemTitleLine(line: string, title: string): boolean {
  return normalizedPoemTitleLine(line) === normalizedPoemTitleLine(title);
}

function poemBodyLines(bodyMarkdown: string, title: string): string[] {
  const body = parseMarkdownDocument(bodyMarkdown).body.trim();
  // Generated poem bodies encode lineation as two-trailing-space CommonMark hard
  // breaks. These previews are PLAIN TEXT (not Markdown-rendered): they rejoin the
  // lines with "\n" and rely on `white-space: pre-line` to show the breaks, so the
  // trailing hard-break spaces are stripped here to keep the extracted text clean.
  const lines = dropLeadingBlankLines(body.split("\n").map(line => line.replace(/ +$/, "")));
  const firstLine = lines[0];
  if (firstLine !== undefined && isPoemTitleLine(firstLine, title)) {
    return dropLeadingBlankLines(lines.slice(1));
  }
  return lines;
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
