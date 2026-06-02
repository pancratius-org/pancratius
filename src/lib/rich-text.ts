// Render plain-text source copy (a video's full YouTube description, say) as
// paragraphs with bare URLs turned into links.
//
// The text is plain, NOT Markdown — running a Markdown parser over it would
// mangle stray `*`/`_`/`#` and misread line breaks. So this does the minimum a
// description needs: split on blank lines into paragraphs, autolink http(s)
// URLs, and leave everything else as text. The output is structured segments,
// not an HTML string, so the rendering component never needs `set:html` and the
// link `href` is always a real matched URL.

export type TextSegment =
  | { kind: "text"; text: string }
  | { kind: "link"; href: string; text: string };

// A URL runs to the first whitespace/`<`; `trimUrl` then peels trailing sentence
// punctuation back off — but keeps a `)` that closes a `(` inside the URL, so a
// link like `…/Foo_(bar)` survives intact.
const URL_RE = /https?:\/\/[^\s<]+/g;
const TRAILING_PUNCT = new Set([".", ",", ";", ":", "!", "?", "»", "\"", "'", "]", "}"]);

function trimUrl(url: string): string {
  let end = url.length;
  while (end > 0) {
    const ch = url[end - 1];
    if (ch === undefined) break;
    if (TRAILING_PUNCT.has(ch)) { end -= 1; continue; }
    if (ch === ")") {
      const slice = url.slice(0, end);
      const opens = (slice.match(/\(/g) ?? []).length;
      const closes = (slice.match(/\)/g) ?? []).length;
      if (closes > opens) { end -= 1; continue; }
    }
    break;
  }
  return url.slice(0, end);
}

/** Split into paragraphs (blank-line separated) and linkify URLs within each.
 *  Empty paragraphs are dropped; single newlines inside a paragraph are kept
 *  in the text (the renderer shows them as line breaks). */
export function richParagraphs(text: string): TextSegment[][] {
  return text
    .split(/\n[ \t]*\n/)
    .map(paragraph => paragraph.trim())
    .filter(paragraph => paragraph.length > 0)
    .map(linkifyParagraph);
}

function linkifyParagraph(paragraph: string): TextSegment[] {
  const segments: TextSegment[] = [];
  let cursor = 0;
  for (const match of paragraph.matchAll(URL_RE)) {
    const start = match.index;
    const href = trimUrl(match[0]);
    if (start > cursor) {
      segments.push({ kind: "text", text: paragraph.slice(cursor, start) });
    }
    segments.push({ kind: "link", href, text: href });
    // Any trailing punctuation peeled off the URL stays as ordinary text.
    cursor = start + href.length;
  }
  if (cursor < paragraph.length) {
    segments.push({ kind: "text", text: paragraph.slice(cursor) });
  }
  return segments;
}

// Collapse threshold for the view. INDEPENDENT of `layoutFor` (which measures
// the body to pick a masthead) — this measures the description so a wall of text
// can't push the video embed off-screen.
const COLLAPSE_CHARS = 600;
const COLLAPSE_LINES = 10;

/** Whether a block of source text is long enough to hide under a "show more". */
export function shouldCollapse(text: string): boolean {
  return text.length > COLLAPSE_CHARS || (text.match(/\n/g) ?? []).length >= COLLAPSE_LINES;
}
