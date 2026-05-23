import type { APIRoute } from "astro";
import rss from "@astrojs/rss";

import { DEFAULT_LOCALE, workUrl } from "@/lib/i18n";
import { getAllWorkPairs } from "@/lib/works";

const locale = DEFAULT_LOCALE;

/**
 * "New works in the corpus" feed. Items are works (books + poems) — projects
 * are themed sections, not works, and are not feed items. Sorted by an
 * editorial date: poems use their `date` frontmatter; books synthesize a
 * stable date from `number` so consumer sort order is preserved without
 * lying about real publication moments. When real per-work dates land, swap
 * the synthetic source for them.
 */
function pubDateFor(number: number, dateField: string | null | undefined): Date {
  if (typeof dateField === "string" && dateField.length >= 10) {
    const d = new Date(dateField);
    if (!Number.isNaN(d.getTime())) return d;
  }
  return new Date(Date.UTC(2025, 0, 1 + number));
}

export const GET: APIRoute = async (context) => {
  const pairs = await getAllWorkPairs();
  return rss({
    title:       "Панкратиус — новые работы",
    description: "Тексты Сергея Орехова (Панкратиуса). Свободно — людям и языковым моделям. CC0.",
    site:        context.site!,
    items: pairs.map(p => {
      const entry = p.entries[DEFAULT_LOCALE]!;
      const date = pubDateFor(
        p.number,
        "date" in entry.data ? (entry.data as { date?: string | null }).date : null,
      );
      return {
        title:       entry.data.title,
        description: entry.data.description,
        link:        workUrl(p.kind, entry.data.slug, locale),
        pubDate:     date,
        categories:  "tags" in entry.data ? (entry.data as { tags?: string[] }).tags ?? [] : [],
      };
    }).sort((a, b) => b.pubDate.getTime() - a.pubDate.getTime()),
    customData:  `<language>ru</language>`,
  });
};
