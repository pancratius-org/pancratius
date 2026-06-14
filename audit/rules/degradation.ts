// PAN022 — conceptosphere / book-reference RU-only degradation invariant
// (docs/audit-harness.md → "PAN022"). A `post-build`-tier rule: it needs the
// emitted `dist/`, so it runs only on `npm run audit:post-build`, never on the
// fast PR gate.
//
// THE INVARIANT (conceptosphere-bilingual-design.md §4): on an English URL a
// book that has no `en.md` falls back to its Russian original. That language
// flip must read as DELIBERATE, never as a leak — the shared treatment
// (`RussianOriginalBadge.astro` + its conceptosphere DOM twin
// `russian-badge.ts`) emits the `.ru-original__pill` "Russian original" pill
// next to the Cyrillic title. PAN021 proves every concept/community LABEL is
// translated; this rule proves the remaining surface — RU-only BOOK TITLES
// rendered under `/en/` — always carries the degradation badge.
//
// WHY a STANDING gate and not a one-time grep: a future render surface that
// prints a book title but bypasses the shared component would silently ship a
// raw-Russian title behind English chrome, and `astro build` succeeding proves
// nothing about it. This crawl of the EMITTED `/en/` HTML is the only thing that
// keeps the invariant true as new book-reference surfaces are added.
//
// CONTEXTS COVERED (the stable wrapper classes the shared SSR components emit;
// each is a self-contained book-reference UNIT whose title must be badged when
// Cyrillic):
//   - `.book__body`        — BookCard.astro book cards (book index, and the
//                            SimilarPair "See also"/"Similar" rows, which render
//                            BookCard). Title in `.book__title`.
//   - `.cs-book-row-meta`  — conceptosphere mobile-fallback "Books" rows
//                            (MobileList.astro). Title in `.cs-title`; the
//                            sibling `.cs-book-row-concepts` list is EXCLUDED
//                            from title detection (translated concept labels,
//                            never the book title).
//   - `.cs-detail-books > li` — conceptosphere mobile-fallback concept
//                            "appears in" lists (MobileList.astro). Title is the
//                            row's `<a>` text.
//
// OUT OF CRAWL SCOPE — DOCUMENTED, NOT IGNORED: the desktop graph side panel
// (`panel-render.ts`, classes `.cs-b-meta` / `.cs-books`) is built in the
// browser DOM at runtime, so it never lands in static `dist/**/*.html` and this
// HTML crawl cannot see it. It is held to the SAME invariant structurally: it is
// the conceptosphere twin of the shared badge (`russianOriginalBadge()` in
// `russian-badge.ts`) and emits the identical `.ru-original__pill`. A NEW
// book-reference surface MUST either localize the title or render the shared
// badge; if it is a NEW SSR wrapper class, add it to `BOOK_REF_CONTEXTS` below so
// this gate sees it.
//
// INVERSE (cheap mis-wire detector): a `/ru/` (default-locale) page must carry
// ZERO pills. A pill on the default-locale surface means a book was wrongly
// treated as a non-default-locale fallback — a locale mis-wire — and fires too.

import type { Rule, RuleContext } from "../lib/rule.ts";
import type { Finding } from "../lib/finding.ts";

const ID = "PAN022-conceptosphere-degradation";
const CATEGORY = "ru-degradation";

// The shared degradation treatment's stable marker. Both the Astro component and
// its conceptosphere DOM twin emit this class on the pill; the literal copy
// ("Russian original") lives in `localeBadge`/`ConceptosphereStrings` so the two
// surfaces cannot drift. We key on the class (the structural contract), not the
// copy string (which localization could change).
const PILL_CLASS = "ru-original__pill";

// Cyrillic script range (covers Russian + extended Cyrillic). A book title that
// contains ANY Cyrillic letter is, on an English URL, an un-localized RU title.
const CYRILLIC = /[Ѐ-ӿԀ-ԯ]/;

// Cap emitted findings; still COUNT every violation so the summary is honest.
const MAX_FINDINGS = 100;

const CONTRACT =
  "On `/en/` every book-reference render context (`.book__body`, `.cs-book-row-meta`, `.cs-detail-books` rows) that shows a Cyrillic-script (RU-only) book title MUST carry the shared `.ru-original__pill` \"Russian original\" badge; on the default-locale `/ru/` surface that pill must NEVER appear.";
const WHY =
  "A raw Russian title under an English URL with no degradation badge is the silent-Russian-leak the i18n contract forbids — the language flip must read as deliberate. A pill on a default-locale page is the inverse defect: a book wrongly treated as a fallback, i.e. a locale mis-wire. `astro build` proves neither; only crawling the emitted HTML does.";
const REPAIR_EN =
  "Render the book title through a surface that either localizes it or emits the shared badge (`RussianOriginalBadge.astro`, or `russianOriginalBadge()` for DOM-built rows). If this is a NEW SSR book-reference wrapper class, add it to BOOK_REF_CONTEXTS in audit/rules/degradation.ts so the gate covers it.";
const REPAIR_RU =
  "Remove the degradation badge from the default-locale surface: a `/ru/` book is the original, not a fallback. The pill leaking here means the fallback decision (`displayWorkEntry` linkLocale / `row.localized`) is mis-wired for the default locale.";
const DO_NOT_FIX_BY =
  "Hand-writing a one-off pill into the new surface's markup, or downgrading this rule below fatal — that re-opens the exact bypass (a book-reference surface that skips the shared component) this gate exists to close.";

/** A book-reference UNIT shape the shared SSR components emit. */
interface BookRefContext {
  /** The stable wrapper class that delimits one book-reference unit. */
  readonly wrapperClass: string;
  /**
   * Where the book TITLE text lives inside the unit:
   *  - `{ titleClass }`: the title is the text of the first element carrying
   *    this class (e.g. `.book__title`, `.cs-title`).
   *  - `"anchor"`: the title is the text of the unit's first `<a>` (the
   *    `.cs-detail-books` row links the bare title).
   * Non-title text in the unit (book number, translated concept list) is never
   * read for Cyrillic, so a localized row with an English title + a stray
   * Cyrillic concept elsewhere does not false-fire.
   */
  readonly title: { readonly titleClass: string } | "anchor";
  /** Human name of the surface, for the finding text. */
  readonly surface: string;
}

const BOOK_REF_CONTEXTS: readonly BookRefContext[] = [
  { wrapperClass: "book__body", title: { titleClass: "book__title" }, surface: "book card" },
  { wrapperClass: "cs-book-row-meta", title: { titleClass: "cs-title" }, surface: "conceptosphere mobile book row" },
  { wrapperClass: "cs-detail-books", title: "anchor", surface: "conceptosphere concept \"appears in\" row" },
];

interface Violation {
  readonly file: string;
  readonly surface: string;
  readonly title: string;
  /** "en" = missing badge on a Cyrillic title; "ru" = stray pill on default locale. */
  readonly kind: "en" | "ru";
}

interface DegradationReport {
  findings: Finding[];
  violationCount: number;
}

export const pan022ConceptosphereDegradation: Rule = {
  id: ID,
  title:
    "PAN022: every `/en/` book-reference context with a Cyrillic (RU-only) book title carries the shared `.ru-original__pill` badge; `/ru/` carries zero pills",
  tier: "post-build",
  run(ctx: RuleContext): Finding[] {
    return degradationReport(ctx).findings;
  },
};

function degradationReport(ctx: RuleContext): DegradationReport {
  const report: DegradationReport = { findings: [], violationCount: 0 };
  for (const htmlRel of emittedHtmlFiles(ctx)) {
    const html = maskScriptStyleBodies(ctx.read(htmlRel));
    for (const v of violationsInHtml(htmlRel, html)) addViolation(report, v);
  }
  appendCapFinding(report);
  return report;
}

/**
 * Blank the BODY of every `<script>`/`<style>` (keeping newlines so the markup
 * length is preserved), leaving tags intact. The pill's CLASS name appears in
 * every page's INLINED scoped CSS (`.ru-original__pill{…}`) — without masking,
 * the inverse `/ru/` check would false-fire on that stylesheet text on every
 * default-locale page, and a `/en/` unit could be falsely "badged" by a CSS
 * mention. Masking limits both checks to rendered markup only. Same technique as
 * PAN014's link crawl.
 */
function maskScriptStyleBodies(html: string): string {
  return html.replace(
    /(<(script|style)\b[^>]*>)([\s\S]*?)(<\/\2\s*>)/gi,
    (_full, open: string, _tag: string, body: string, close: string) =>
      open + body.replace(/[^\n]/g, " ") + close,
  );
}

function emittedHtmlFiles(ctx: RuleContext): string[] {
  return ctx.walk({
    unignore: ["dist"],
    filter: (rel) => rel.startsWith("dist/") && rel.endsWith(".html"),
  });
}

/** True for an emitted file under the English locale prefix (`dist/en/...`). */
function isEnglishLocale(htmlRel: string): boolean {
  return htmlRel === "dist/en" || htmlRel.startsWith("dist/en/");
}

function violationsInHtml(htmlRel: string, html: string): Violation[] {
  return isEnglishLocale(htmlRel)
    ? missingBadgeViolations(htmlRel, html)
    : strayPillViolations(htmlRel, html);
}

/** /en/: each book-reference unit with a Cyrillic title must contain a pill. */
function missingBadgeViolations(htmlRel: string, html: string): Violation[] {
  const out: Violation[] = [];
  for (const context of BOOK_REF_CONTEXTS) {
    for (const unit of bookRefUnits(html, context)) {
      const title = unitTitle(unit, context.title);
      if (title === null || !CYRILLIC.test(title)) continue;
      if (unit.includes(PILL_CLASS)) continue;
      out.push({ file: htmlRel, surface: context.surface, title: title.trim(), kind: "en" });
    }
  }
  return out;
}

/** /ru/ (default locale): the degradation pill must never appear. */
function strayPillViolations(htmlRel: string, html: string): Violation[] {
  if (!html.includes(PILL_CLASS)) return [];
  return [{ file: htmlRel, surface: "default-locale page", title: "", kind: "ru" }];
}

/**
 * Each book-reference UNIT in the HTML for one context. A unit is the element
 * carrying `wrapperClass` together with its full subtree, captured by walking
 * the tag stream from the opening tag to its matching close (so a nested badge
 * or title inside the unit is included). Emitted Astro HTML is well-formed, so a
 * depth counter over `<tag>`/`</tag>` is exact.
 *
 * `.cs-detail-books` is a LIST wrapper whose ROWS are the units (one `<li>`
 * per book); for it we descend to each direct child element. Every other
 * context's wrapper IS the unit.
 */
function bookRefUnits(html: string, context: BookRefContext): string[] {
  const wrappers = elementsWithClass(html, context.wrapperClass);
  if (context.wrapperClass !== "cs-detail-books") return wrappers;
  // A `.cs-detail-books` <ul> holds one <li> book-reference unit per book.
  return wrappers.flatMap((ul) => childElements(ul, "li"));
}

function unitTitle(unit: string, title: BookRefContext["title"]): string | null {
  const [first] =
    title === "anchor" ? childElements(unit, "a") : elementsWithClass(unit, title.titleClass);
  return first === undefined ? null : elementText(first);
}

/**
 * Every element in `html` whose `class` attribute contains `cls` as a
 * whitespace-delimited token, returned as the full outer HTML of each
 * (open-tag … matching-close). Robust to class ORDER and to extra classes
 * (`class="a book__body c"` matches `book__body`).
 */
function elementsWithClass(html: string, cls: string): string[] {
  const out: string[] = [];
  const open = new RegExp(`<([a-zA-Z][\\w-]*)\\b[^>]*\\bclass\\s*=\\s*"([^"]*)"[^>]*>`, "g");
  let m: RegExpExecArray | null;
  while ((m = open.exec(html)) !== null) {
    const tag = m[1];
    const classAttr = m[2];
    if (tag === undefined || classAttr === undefined) {
      throw new Error("class-element extractor matched without tag/class captures");
    }
    if (!hasClassToken(classAttr, cls)) continue;
    const element = captureElement(html, tag, m.index);
    if (element !== null) out.push(element);
  }
  return out;
}

/** Direct (and nested) descendant elements of `parent` with the given tag name. */
function childElements(parent: string, tag: string): string[] {
  const out: string[] = [];
  const open = new RegExp(`<${tag}\\b[^>]*>`, "gi");
  let m: RegExpExecArray | null;
  while ((m = open.exec(parent)) !== null) {
    const element = captureElement(parent, tag, m.index);
    if (element !== null) {
      out.push(element);
      // Skip past this element so a nested same-tag isn't double-counted as a sibling.
      open.lastIndex = m.index + element.length;
    }
  }
  return out;
}

function hasClassToken(classAttr: string, cls: string): boolean {
  return classAttr.split(/\s+/).includes(cls);
}

/**
 * From `openIndex` (the `<` of an opening `<tag …>`), return the element's full
 * outer HTML up to its matching `</tag>`, counting nested same-name tags. Void/
 * self-closing tags (`<tag .../>`) return just the tag. Returns null if no match
 * is found (malformed input — never on emitted Astro HTML).
 */
function captureElement(html: string, tag: string, openIndex: number): string | null {
  const openEnd = html.indexOf(">", openIndex);
  if (openEnd === -1) return null;
  if (html[openEnd - 1] === "/") return html.slice(openIndex, openEnd + 1); // self-closing
  const both = new RegExp(`<${tag}\\b[^>]*?>|</${tag}\\s*>`, "gi");
  both.lastIndex = openEnd + 1;
  let depth = 1;
  let m: RegExpExecArray | null;
  while ((m = both.exec(html)) !== null) {
    const isClose = m[0].startsWith("</");
    const selfClosing = !isClose && m[0].endsWith("/>");
    if (selfClosing) continue;
    depth += isClose ? -1 : 1;
    if (depth === 0) return html.slice(openIndex, m.index + m[0].length);
  }
  return null;
}

/** Visible text of an element: strip tags, collapse whitespace, decode the few
 *  entities Astro emits in text. Sufficient to test for a Cyrillic title. */
function elementText(element: string): string {
  const inner = element.replace(/^<[^>]*>/, "").replace(/<\/[^>]*>\s*$/, "");
  return decodeEntities(inner.replace(/<[^>]*>/g, " ")).replace(/\s+/g, " ").trim();
}

function decodeEntities(text: string): string {
  return text
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&nbsp;/g, " ")
    .replace(/&#x([0-9a-fA-F]+);/g, (_s, hex: string) => String.fromCodePoint(parseInt(hex, 16)))
    .replace(/&#(\d+);/g, (_s, dec: string) => String.fromCodePoint(parseInt(dec, 10)));
}

function addViolation(report: DegradationReport, v: Violation): void {
  report.violationCount += 1;
  if (report.findings.length >= MAX_FINDINGS) return;
  report.findings.push(violationFinding(v));
}

function violationFinding(v: Violation): Finding {
  const observed =
    v.kind === "en"
      ? `${v.file}: ${v.surface} renders the Cyrillic (RU-only) book title "${v.title}" with no \`.${PILL_CLASS}\` "Russian original" badge in its context — a raw Russian title leaking under an English URL.`
      : `${v.file}: a default-locale (\`/ru/\`) page carries a \`.${PILL_CLASS}\` degradation badge — the pill belongs only on non-default-locale fallbacks, so a book is mis-wired as a fallback here.`;
  return {
    rule: ID,
    severity: "fatal",
    category: CATEGORY,
    file: v.file,
    observed,
    contract: CONTRACT,
    why: WHY,
    repair: v.kind === "en" ? REPAIR_EN : REPAIR_RU,
    doNotFixBy: DO_NOT_FIX_BY,
  };
}

function appendCapFinding(report: DegradationReport): void {
  if (report.violationCount <= report.findings.length || report.findings.length === 0) return;
  report.findings.push({
    rule: ID,
    severity: "fatal",
    category: CATEGORY,
    file: "dist",
    observed: `${report.violationCount} RU-degradation violations found across emitted HTML; ${report.findings.length} shown (cap ${MAX_FINDINGS}).`,
    contract: CONTRACT,
    why: WHY,
    repair: REPAIR_EN,
    doNotFixBy: DO_NOT_FIX_BY,
  });
}
