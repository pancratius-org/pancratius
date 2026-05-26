import { parse } from "yaml";

export type Frontmatter = Record<string, unknown>;

export interface MarkdownDocument {
  frontmatter: Frontmatter;
  body: string;
}

export interface ParseMarkdownDocumentOptions {
  frontmatter?: "optional" | "required";
}

const OPENING_YAML_FENCE_RE = /^(?:\uFEFF)?---[ \t]*(?:\r?\n)/;
const CLOSING_YAML_FENCE_RE = /^---[ \t]*$/;

export function parseMarkdownDocument(
  markdown: string,
  options: ParseMarkdownDocumentOptions = {},
): MarkdownDocument {
  const split = splitYamlFrontmatter(markdown);
  if (!split) {
    if (options.frontmatter === "required") throw new Error("missing frontmatter");
    return { frontmatter: {}, body: markdown };
  }

  const parsed = parse(split.yaml) as unknown;
  if (parsed != null && !isRecord(parsed)) {
    throw new Error("frontmatter is not a mapping");
  }

  return {
    frontmatter: parsed ?? {},
    body: split.body,
  };
}

function splitYamlFrontmatter(markdown: string): { yaml: string; body: string } | null {
  const opening = OPENING_YAML_FENCE_RE.exec(markdown);
  if (!opening) return null;

  const yamlStart = opening[0].length;
  let lineStart = yamlStart;
  while (lineStart <= markdown.length) {
    const lineEnd = markdown.indexOf("\n", lineStart);
    const line = lineEnd === -1
      ? markdown.slice(lineStart)
      : markdown.slice(lineStart, lineEnd).replace(/\r$/, "");

    if (CLOSING_YAML_FENCE_RE.test(line)) {
      const bodyStart = lineEnd === -1 ? markdown.length : lineEnd + 1;
      return {
        yaml: markdown.slice(yamlStart, lineStart),
        body: markdown.slice(bodyStart).replace(/^\r?\n/, ""),
      };
    }

    if (lineEnd === -1) break;
    lineStart = lineEnd + 1;
  }

  throw new Error("unclosed frontmatter");
}

function isRecord(value: unknown): value is Frontmatter {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
