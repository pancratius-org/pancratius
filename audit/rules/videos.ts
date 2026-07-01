// PAN028 — a video's hook (frontmatter `description`) carries no promo junk.
//
// The scanner (`pancratius video sync`) drafts each video's `description` from
// the raw YouTube description, which bundles the message with an SEO opener,
// hashtags, and a promo footer (book ads, a Telegram channel, a donation block).
// The Python splitter's QA gate rejects junk at generation time; this is the
// content-layer backstop that runs in `npm run verify` — so if a junk hook ever
// reaches a committed file (a splitter regression, or a hand edit), the sync PR
// fails to build and never auto-merges. It guards the `description` field only:
// the reading `body` is covered by the generation-time gate, and older bodies
// predate this feature.
//
// The junk classes mirror the splitter's vocabulary
// (pancratius/video_description/patterns.py). They are the unambiguous ones — a
// URL, a Telegram handle, a bank-card number, a hashtag, a donation phrase, raw
// HTML, a promo emoji — never a faithful part of a lede. Religious/text symbols
// (✝ ☦ ✡) and a bare series reference ("из серии «…»") are deliberately NOT junk.

import { parse } from "yaml";
import type { Rule, RuleContext } from "../lib/rule.ts";
import type { Finding } from "../lib/finding.ts";

const ID = "PAN028-video-hook";
const CATEGORY = "content-junk";
const VIDEOS = "src/content/videos/";

interface JunkClass {
  name: string;
  pattern: RegExp;
}

// Kept in step with JUNK_PATTERNS in pancratius/video_description/patterns.py.
const JUNK: readonly JunkClass[] = [
  { name: "a URL", pattern: /https?:\/\/|www\.\w|\bt\.me\/|\b[a-z0-9][a-z0-9-]*\.(?:ru|com|org|net|me|to|tv|io|app|dev|info|xyz)\b/i },
  { name: "an @handle", pattern: /@[A-Za-z0-9_]{2,}/ },
  { name: "an e-mail", pattern: /\b[\w.+-]+@[\w-]+\.[a-z]{2,}/i },
  { name: "a card number", pattern: /\d{4}(?:[\s.\-]*\d{4}){3}/ },
  { name: "a hashtag", pattern: /(?:^|\s)#[^\s#]+/ },
  { name: "a promo/donation phrase", pattern: /Поддержать проект|поддержать канал|Следующее\s*[—–-]\s*здесь|Книги автора/i },
  { name: "raw HTML", pattern: /<\/?[a-z][a-z0-9]*(?:\s[^<>]*)?>/i },
  { name: "a promo emoji", pattern: /[\u{1F000}-\u{1FAFF}←-⇿✅✉❤⬅➡▶◀☑⭐✨]/u },
];

const frontmatterDescription = (text: string): string | null => {
  const match = /^---\n([\s\S]*?)\n---/.exec(text);
  const block = match?.[1];
  if (block === undefined) return null;
  try {
    const parsed: unknown = parse(block);
    const description = (parsed as Record<string, unknown> | null)?.description;
    return typeof description === "string" ? description : null;
  } catch {
    return null;
  }
};

const descriptionLine = (text: string): number => {
  const line = text.split("\n").findIndex((l) => /^description\s*:/.test(l));
  return line === -1 ? 1 : line + 1;
};

export const pan028VideoHook: Rule = {
  id: ID,
  title: "PAN028: a video hook (frontmatter description) carries no promo junk",
  tier: "core",
  run(ctx: RuleContext): Finding[] {
    const findings: Finding[] = [];
    for (const rel of ctx.walk({ filter: (p) => p.startsWith(VIDEOS) && p.endsWith(".md") })) {
      const text = ctx.read(rel);
      const description = frontmatterDescription(text);
      if (description === null) continue;
      for (const junk of JUNK) {
        if (!junk.pattern.test(description)) continue;
        findings.push({
          rule: ID,
          severity: "fatal",
          category: CATEGORY,
          file: rel,
          line: descriptionLine(text),
          observed: `${rel} has ${junk.name} in its \`description\` — the video's lede/SEO copy.`,
          contract:
            "A video's `description` (hook) is a clean reading lede: no links, handles, e-mails, card numbers, hashtags, donation phrases, raw HTML, or promo emoji. `pancratius video sync` drafts it that way (its QA gate at pancratius/video_description/qa.py); this is the committed-content backstop.",
          why: "The hook is the page's SEO/OG/card copy. A donation card number, a Telegram link, or a raw HTML tag there is discovery bait — or an injection vector — on a controlled reading surface, and the weekly sync auto-merges without human review.",
          repair: "Rewrite the `description` as a clean one-thought lede in the author's words; move any real reference into the body or drop it. Re-run `pancratius video sync` locally to redraft it.",
          doNotFixBy: "Loosening PAN028 or the splitter's junk patterns to admit the leak.",
        });
      }
    }
    return findings;
  },
};
