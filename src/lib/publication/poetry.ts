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
  const lines = body.split("\n");
  let start = 0;
  while (start < lines.length && lines[start].trim() === "") start++;
  if (start < lines.length && isPoemTitleLine(lines[start], title)) {
    start++;
    while (start < lines.length && lines[start].trim() === "") start++;
  }
  return lines.slice(start);
}

function normalizedPoemTitleLine(value: string): string {
  return value.replace(/[…\*]+$/g, "").replace(/[,;:!?\.]/g, "").trim().toLowerCase();
}
