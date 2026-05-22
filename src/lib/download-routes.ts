import type { APIRoute } from "astro";

import { availableFormatsForWork, renderDownload, type DownloadFormat } from "./downloads";
import type { Locale, WorkKind } from "./i18n";
import { getPairsByKind, type WorkPair } from "./works";

export interface DownloadRouteProps extends Record<string, unknown> {
  pair: WorkPair;
  locale: Locale;
  format: DownloadFormat;
}

export async function downloadStaticPaths(kind: WorkKind, locale: Locale) {
  const pairs = await getPairsByKind(kind);
  const out: {
    params: { slug: string; format: DownloadFormat };
    props: DownloadRouteProps;
  }[] = [];

  for (const pair of pairs) {
    const entry = locale === "en" ? pair.en : pair.ru;
    if (!entry) continue;
    for (const format of availableFormatsForWork(pair, locale)) {
      out.push({
        params: { slug: entry.data.slug, format },
        props: { pair, locale, format },
      });
    }
  }

  return out;
}

export const handleDownloadGET: APIRoute<DownloadRouteProps> = ({ props }) => {
  const { pair, locale, format } = props;
  const { bytes, contentType, filename } = renderDownload(pair, locale, format);
  return new Response(new Uint8Array(bytes), {
    headers: {
      "Content-Type": contentType,
      "Content-Disposition": `inline; filename="${filename}"`,
    },
  });
};
