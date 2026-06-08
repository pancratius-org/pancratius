import postcss, { type Declaration } from "postcss";

import type { RuleContext } from "./rule.ts";

const RAW_LENGTH_RE = /clamp\([^;{}]+\)|[-+]?(?:\d*\.)?\d+(?:rem|ch|px|vw|vh|em)/gi;
const LAYOUT_PROPS = new Set([
  "max-width",
  "max-inline-size",
  "width",
  "inline-size",
  "grid-template-columns",
  "flex-basis",
]);
const LARGE_PIXEL_RE = /\b(?:220|320|480|608|720|960|1100|1200)px\b/g;

interface CssUse {
  file: string;
  line: number;
  property: string;
  value: string;
  selector: string;
}

interface CssValueGroup {
  value: string;
  count: number;
  properties: string[];
  examples: CssUse[];
}

export interface CssValueReport {
  files: number;
  blocks: number;
  declarations: number;
  repeated: CssValueGroup[];
  layout: CssValueGroup[];
  spacing: CssValueGroup[];
  typography: CssValueGroup[];
  largePixels: CssValueGroup[];
}

export interface CssValueOptions {
  minCount: number;
  limit: number;
  examples: number;
}

interface CssBlock {
  file: string;
  css: string;
  lineOffset: number;
}

const DEFAULT_OPTIONS: CssValueOptions = {
  minCount: 3,
  limit: 18,
  examples: 4,
};

export function analyzeCssValues(ctx: RuleContext, options: Partial<CssValueOptions> = {}): CssValueReport {
  const opts = { ...DEFAULT_OPTIONS, ...options };
  const files = cssSourceFiles(ctx);
  const blocks = files.flatMap((file) => extractCssBlocks(file, ctx.read(file)));
  const declarations = blocks.flatMap(readDeclarations);
  const withRawValues = declarations.filter((decl) => hasRawLength(decl.value));

  return {
    files: files.length,
    blocks: blocks.length,
    declarations: declarations.length,
    repeated: groupValues(withRawValues, opts),
    layout: groupValues(declarations.filter(isLayoutLiteral), opts),
    spacing: groupValues(declarations.filter(isSpacingLiteral), opts),
    typography: groupValues(declarations.filter(isTypographyLiteral), opts),
    largePixels: groupValues(declarations.flatMap(largePixelUses), { ...opts, minCount: 1 }),
  };
}

export function formatCssValueReport(report: CssValueReport, options: Partial<CssValueOptions> = {}): string {
  const opts = { ...DEFAULT_OPTIONS, ...options };
  const sections = [
    renderSection("Layout literals", report.layout, opts),
    renderSection("Spacing literals", report.spacing, opts),
    renderSection("Typography literals", report.typography, opts),
    renderSection("Large pixel anchors", report.largePixels, opts),
    renderSection("Repeated raw design values", report.repeated, opts),
  ];

  return [
    "CSS value diagnostic",
    `Scanned ${report.files} files, ${report.blocks} style blocks, ${report.declarations} declarations.`,
    "",
    ...sections,
  ].join("\n");
}

export function extractCssBlocks(file: string, source: string): CssBlock[] {
  if (file.endsWith(".css")) return [{ file, css: source, lineOffset: 0 }];

  const blocks: CssBlock[] = [];
  const styleRe = /<style\b[^>]*>([\s\S]*?)<\/style>/gi;
  let match: RegExpExecArray | null = styleRe.exec(source);

  while (match !== null) {
    const css = match[1];
    if (css === undefined) throw new Error(`could not read <style> block in ${file}`);
    const openEnd = match[0].indexOf(">");
    if (openEnd < 0) throw new Error(`malformed <style> block in ${file}`);
    blocks.push({
      file,
      css,
      lineOffset: lineNumberAt(source, match.index + openEnd + 1) - 1,
    });
    match = styleRe.exec(source);
  }

  return blocks;
}

function cssSourceFiles(ctx: RuleContext): string[] {
  return ctx.walk({
    filter: (file) => file.startsWith("src/") && (file.endsWith(".css") || file.endsWith(".astro")),
  });
}

function readDeclarations(block: CssBlock): CssUse[] {
  const root = postcss.parse(block.css, { from: block.file });
  const declarations: CssUse[] = [];

  root.walkDecls((decl) => {
    declarations.push({
      file: block.file,
      line: block.lineOffset + (decl.source?.start?.line ?? 1),
      property: decl.prop.toLowerCase(),
      value: normalizeValue(decl.value),
      selector: selectorFor(decl),
    });
  });

  return declarations;
}

function groupValues(uses: readonly CssUse[], options: CssValueOptions): CssValueGroup[] {
  const byValue = new Map<string, CssUse[]>();
  for (const use of uses) {
    const group = byValue.get(use.value);
    if (group === undefined) {
      byValue.set(use.value, [use]);
    } else {
      group.push(use);
    }
  }

  return [...byValue.entries()]
    .map(([value, valueUses]) => ({
      value,
      count: valueUses.length,
      properties: unique(valueUses.map((use) => use.property)),
      examples: valueUses.slice(0, options.examples),
    }))
    .filter((group) => group.count >= options.minCount)
    .sort((a, b) => b.count - a.count || a.value.localeCompare(b.value))
    .slice(0, options.limit);
}

function renderSection(title: string, groups: readonly CssValueGroup[], options: CssValueOptions): string {
  if (groups.length === 0) return `${title}\n  none\n`;

  const lines = [`${title}`];
  for (const group of groups.slice(0, options.limit)) {
    lines.push(`  ${group.value} — ${group.count} uses; properties: ${group.properties.join(", ")}`);
    for (const example of group.examples) {
      const selector = example.selector === "" ? "" : ` ${example.selector}`;
      lines.push(`    ${example.file}:${example.line} ${example.property}${selector}`);
    }
  }
  lines.push("");
  return lines.join("\n");
}

function selectorFor(decl: Declaration): string {
  const parent = decl.parent;
  return parent?.type === "rule" ? parent.selector : "";
}

function normalizeValue(value: string): string {
  return value
    .trim()
    .replace(/\s+/g, " ")
    .replace(/\s*,\s*/g, ", ")
    .replace(/\(\s+/g, "(")
    .replace(/\s+\)/g, ")");
}

function isLayoutLiteral(use: CssUse): boolean {
  return LAYOUT_PROPS.has(use.property) && hasRawLength(use.value);
}

function isSpacingLiteral(use: CssUse): boolean {
  return spacingProperty(use.property) && /clamp\(|[-+]?(?:\d*\.)?\d+(?:rem|px|vw|vh)/i.test(use.value);
}

function isTypographyLiteral(use: CssUse): boolean {
  // Leading is usually unitless (`line-height: 0.96`); don't require a unit here.
  if (use.property === "line-height") return /[-+]?(?:\d*\.)?\d+/.test(use.value);
  if (use.property !== "font-size" && use.property !== "letter-spacing") return false;
  return /[-+]?(?:\d*\.)?\d+(?:px|rem|em)/i.test(use.value);
}

function largePixelUses(use: CssUse): CssUse[] {
  const matches = use.value.match(LARGE_PIXEL_RE);
  if (matches === null) return [];
  return unique(matches).map((value) => ({ ...use, value }));
}

function spacingProperty(property: string): boolean {
  return (
    property === "padding" ||
    property.startsWith("padding-") ||
    property === "margin" ||
    property.startsWith("margin-") ||
    property === "gap" ||
    property === "row-gap" ||
    property === "column-gap"
  );
}

function hasRawLength(value: string): boolean {
  RAW_LENGTH_RE.lastIndex = 0;
  return RAW_LENGTH_RE.test(value);
}

function unique(values: readonly string[]): string[] {
  return [...new Set(values)].sort((a, b) => a.localeCompare(b));
}

function lineNumberAt(text: string, index: number): number {
  let line = 1;
  for (let i = 0; i < index; i += 1) {
    if (text.charCodeAt(i) === 10) line += 1;
  }
  return line;
}
