// PAN026 — EN prose double-quotes must render as correct directional curly glyphs.
//
// `pancratius work translate` drafts en.md from ru.md without the locale quote
// normalization the DOCX importer applies (content-model.md → Quotation marks), so a
// model can leave straight quotes, mis-faced curly quotes, or HTML entities behind.
// Sätteri's smart-quote pass (on by default) pairs straight quotes per block —
// open/close, state resetting at each paragraph — so a lone close (the last
// paragraph of multi-paragraph divine speech) renders as an opening “ where a
// closing ” is meant. Mis-faced curly quotes and `&#34;` entities pass straight
// through. RU is clean: it uses guillemets, not curly quotes.
//
// The rule reads each en.md through Sätteri's own MDAST (post-smart-punctuation), so
// raw-HTML attributes (`class="lineated"`, `dir=…`) and code spans never enter the
// check — it sees exactly what the build will render. It flags:
//   • a curly “ glued to a word/punctuation with nothing opening after it → a close,
//   • a curly ” at a boundary with a word immediately after it            → an open,
//   • a literal " surviving in prose (an entity/escape Sätteri can't curl).
// Flag, never auto-fix: ~76% of straight quotes in en.md live inside raw-HTML
// attributes a blanket straight→curly pass would shred.

import { markdownToMdast, type MdastNode } from "satteri";
import type { Rule, RuleContext } from "../lib/rule.ts";
import type { Finding } from "../lib/finding.ts";

const ID = "PAN026-quote-direction";
const FEATURES = { gfm: true, frontmatter: true, smartPunctuation: true } as const;

const BLOCKS = new Set(["paragraph", "heading", "tableCell"]);
// Inline nodes whose text never carries prose quotes (code, raw HTML attributes).
const SKIP_NODES = new Set(["inlineCode", "code", "html"]);
const isDoubleQuote = (ch: string): boolean => ch === "“" || ch === "”" || ch === '"';
// Characters that legitimately precede an opening quote (a boundary).
const OPEN_PREV = new Set([..."([{<«—–:/“‘"]);
const WORD = /[A-Za-z0-9Ѐ-ӿ]/;
// Literal emphasis markers read as transparent when judging a quote's neighbours
// (a `\*“\*Here` heading is still an opening quote in front of a word).
const MARKUP = new Set(["*", "_"]);
// One source token == one rendered double-quote: straight, curly, entity, or escape.
const DQUOTE = /&#34;|\\"|["“”]/g;

interface QuoteHit {
  /** index of the rendered double-quote within the block value */
  valueIndex: number;
  /** the text node that produced it, for source-offset mapping */
  node: MdastNode;
  /** its 0-based double-quote ordinal within that text node */
  ordinal: number;
}

/** A mis-rendered double-quote: what it currently faces, and what it should face. */
interface QuoteDefect {
  facing: string;
  want: string;
}

/**
 * Concatenate a block's prose value (text nodes + hard breaks), skipping code and
 * raw HTML, and record each rendered double-quote with the text node + ordinal
 * needed to map it back to a source line.
 */
function blockValue(block: MdastNode): { value: string; quotes: QuoteHit[] } {
  let value = "";
  const quotes: QuoteHit[] = [];
  const ordinals = new Map<MdastNode, number>();
  const walk = (n: MdastNode): void => {
    if (n.type === "text") {
      for (const ch of n.value) {
        if (isDoubleQuote(ch)) {
          const ordinal = ordinals.get(n) ?? 0;
          quotes.push({ valueIndex: value.length, node: n, ordinal });
          ordinals.set(n, ordinal + 1);
        }
        value += ch;
      }
    } else if (n.type === "break") {
      value += "\n"; // a hard break is a word boundary for quote direction
    } else if (!SKIP_NODES.has(n.type) && "children" in n) {
      for (const child of n.children) walk(child);
    }
  };
  if ("children" in block) for (const child of block.children) walk(child);
  return { value, quotes };
}

/** Nearest neighbour, skipping transparent literal markup (`*`, `_`). */
function prevNeighbour(t: string, i: number): string | undefined {
  for (let j = i - 1; j >= 0; j--) {
    const ch = t[j];
    if (ch === undefined || !MARKUP.has(ch)) return ch;
  }
  return undefined;
}
function nextNeighbour(t: string, i: number): string | undefined {
  for (let j = i + 1; j < t.length; j++) {
    const ch = t[j];
    if (ch === undefined || !MARKUP.has(ch)) return ch;
  }
  return undefined;
}
const boundaryBefore = (p: string | undefined): boolean =>
  p === undefined || /\s/.test(p) || OPEN_PREV.has(p);
const wordAfter = (n: string | undefined): boolean => n !== undefined && WORD.test(n);
// An opening “ that hugs a word/punctuation with nothing opening after it is a
// mis-rendered close — unless it hugs a digit, where it is a measurement mark (6“).
const isMisfacedOpen = (prev: string | undefined, next: string | undefined): boolean =>
  !boundaryBefore(prev) && !wordAfter(next) && !(prev !== undefined && /[0-9]/.test(prev));
// A closing ” at a boundary with a word right after it is a mis-rendered open.
const isMisfacedClose = (prev: string | undefined, next: string | undefined): boolean =>
  boundaryBefore(prev) && wordAfter(next);

/** Classify the double-quote at value index `i`, or null when it faces correctly. */
function classify(value: string, i: number): QuoteDefect | null {
  const c = value[i];
  const prev = prevNeighbour(value, i);
  const next = nextNeighbour(value, i);
  if (c === '"') return { facing: "an uncurled straight quote", want: "a directional “ or ”" };
  if (c === "“" && isMisfacedOpen(prev, next)) return { facing: "an opening “", want: "a closing ”" };
  if (c === "”" && isMisfacedClose(prev, next)) return { facing: "a closing ”", want: "an opening “" };
  return null;
}

/** Source line of a hit, mapped through the text node's ordinal-aligned tokens. */
function sourceLine(src: string, hit: QuoteHit): number {
  const start = hit.node.position?.start.offset;
  if (start === undefined) return hit.node.position?.start.line ?? 0;
  const raw = src.slice(start, hit.node.position?.end.offset);
  const token = [...raw.matchAll(DQUOTE)][hit.ordinal];
  const offset = token ? start + token.index : start;
  let line = 1;
  for (let k = 0; k < offset; k++) if (src[k] === "\n") line += 1;
  return line;
}

function snippet(value: string, i: number): string {
  return (value.slice(Math.max(0, i - 32), i) + "❰" + value[i] + "❱" + value.slice(i + 1, i + 24))
    .replace(/\s+/g, " ")
    .trim();
}

export const pan026QuoteDirection: Rule = {
  id: ID,
  title: "PAN026: EN prose quotes render as correct directional curly glyphs",
  tier: "core",
  run(ctx: RuleContext): Finding[] {
    const findings: Finding[] = [];
    for (const rel of ctx.walk({ filter: (p) => p.endsWith("/en.md") })) {
      const src = ctx.read(rel);
      let tree: MdastNode;
      try {
        tree = markdownToMdast(src, { features: FEATURES });
      } catch {
        continue; // a malformed file is PAN006B's concern, not this rule's
      }
      const visit = (n: MdastNode): void => {
        if (BLOCKS.has(n.type)) {
          const { value, quotes } = blockValue(n);
          for (const hit of quotes) {
            const defect = classify(value, hit.valueIndex);
            if (!defect) continue;
            const line = sourceLine(src, hit);
            findings.push({
              rule: ID,
              severity: "fatal",
              category: "quote-direction",
              file: rel,
              line,
              observed: `${rel}:${line} renders ${defect.facing} where ${defect.want} is meant — “${snippet(value, hit.valueIndex)}”`,
              contract:
                "English text uses American curly double quotes “…” facing the right way (content-model.md → Quotation marks). Sätteri's smart-quote pass pairs straight quotes per block, so an unbalanced or mis-typed quote renders backwards.",
              why: "A backwards quote — an opening “ at the end of quoted speech, or a closing ” opening it — reads as a typo on a public reading page and breaks the multi-paragraph speech the translation collapsed from the RU guillemets.",
              repair:
                "Write the explicit directional curly glyph in en.md (“ U+201C to open, ” U+201D to close) so the smart-quote pass leaves it alone; re-promote en.docx and rebuild en.epub/en.pdf for the work.",
              doNotFixBy:
                "A blanket straight→curly pass over en.md — ~76% of its straight quotes live inside raw-HTML attributes (class=\"lineated\", …) that pass would shred. Fix the flagged prose quote only.",
            });
          }
          return; // the block's inline children are already consumed by blockValue
        }
        if ("children" in n) for (const child of n.children) visit(child);
      };
      visit(tree);
    }
    return findings;
  },
};
