import type { APIRoute, GetStaticPaths } from "astro";
import { availableFormatsForWork, renderDownload, type DownloadFormat } from "@/lib/downloads";
import { getPairsByKind, type WorkPair } from "@/lib/works";

const locale = "en";

export const getStaticPaths = (async () => {
  const pairs = (await getPairsByKind("project")).filter(p => p.en !== null);
  const out: { params: { slug: string; format: DownloadFormat }; props: { pair: WorkPair; format: DownloadFormat } }[] = [];
  for (const pair of pairs) {
    for (const format of availableFormatsForWork(pair, "en")) {
      out.push({
        params: { slug: pair.en!.data.slug, format },
        props:  { pair, format },
      });
    }
  }
  return out;
}) satisfies GetStaticPaths;

interface Props { pair: WorkPair; format: DownloadFormat; }

export const GET: APIRoute<Props> = ({ props }) => {
  const { pair, format } = props;
  const { bytes, contentType, filename } = renderDownload(pair, locale, format);
  return new Response(new Uint8Array(bytes), {
    headers: {
      "Content-Type":        contentType,
      "Content-Disposition": `inline; filename="${filename}"`,
    },
  });
};
