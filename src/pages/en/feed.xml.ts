import type { APIRoute } from "astro";
import rss from "@astrojs/rss";

import type { Locale } from "@/lib/i18n";
import { workUrl } from "@/lib/i18n";
import { getAllWorkPairs } from "@/lib/works";

const locale: Locale = "en";

function pubDateFor(number: number, dateField: string | null | undefined): Date {
  if (typeof dateField === "string" && dateField.length >= 10) {
    const d = new Date(dateField);
    if (!Number.isNaN(d.getTime())) return d;
  }
  return new Date(Date.UTC(2025, 0, 1 + number));
}

export const GET: APIRoute = async (context) => {
  // Surface only works whose EN title is editorially real. Untranslated
  // placeholders would mislead EN-locale RSS subscribers into thinking they
  // have a translation.
  const pairs = (await getAllWorkPairs()).filter(p => {
    if (!p.en) return false;
    return p.en.data.title_is_untranslated !== true;
  });
  return rss({
    title:       "Pancratius — new works",
    description: "Sergey Orekhov's writings in English translation. Free — for humans and for language models. CC0.",
    site:        context.site!,
    items: pairs.map(p => {
      const entry = p.en!;
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
    customData:  `<language>en</language>`,
  });
};
