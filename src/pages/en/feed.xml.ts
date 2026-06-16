import type { APIRoute } from "astro";
import rss from "@astrojs/rss";

import type { Locale } from "@/lib/i18n";
import { workUrl } from "@/lib/i18n";
import { originFor } from "@/lib/origins";
import { getAllWorkPairs, localizedWorkPairs } from "@/lib/works";

const locale: Locale = "en";

function pubDateFor(number: number, dateField: string | null | undefined): Date {
  if (typeof dateField === "string" && dateField.length >= 10) {
    const d = new Date(dateField);
    if (!Number.isNaN(d.getTime())) return d;
  }
  return new Date(Date.UTC(2025, 0, 1 + number));
}

export const GET: APIRoute = async () => {
  const pairs = localizedWorkPairs(await getAllWorkPairs(), locale);
  return rss({
    title:       "Pancratius — new works",
    description: "Sergey Orekhov's writings in English translation. Free — for humans and for language models. CC0.",
    site:        originFor(locale),
    items: pairs.map(({ pair, entry }) => {
      const date = pubDateFor(
        pair.number,
        "date" in entry.data ? (entry.data as { date?: string | null }).date : null,
      );
      return {
        title:       entry.data.title,
        description: entry.data.description,
        link:        workUrl(pair.kind, entry.data.slug, locale),
        pubDate:     date,
        categories:  "tags" in entry.data ? (entry.data as { tags?: string[] }).tags ?? [] : [],
      };
    }).sort((a, b) => b.pubDate.getTime() - a.pubDate.getTime()),
    customData:  `<language>en</language>`,
  });
};
