// Pure formatters for video data — no `astro:content` imports so tests can
// load this directly under node --test.

/**
 * Format an ISO 8601 duration (`PT8M42S`, `PT1H3M`) as a display string
 * (`8:42`, `1:03:00`). Drops leading zero hours; pads minutes/seconds to 2.
 */
export function formatDuration(iso: string): string {
  const m = /^PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$/.exec(iso);
  if (!m) return iso;
  const h = m[1] ? parseInt(m[1], 10) : 0;
  const mn = m[2] ? parseInt(m[2], 10) : 0;
  const s = m[3] ? parseInt(m[3], 10) : 0;
  const pad = (n: number) => n.toString().padStart(2, "0");
  return h > 0 ? `${h}:${pad(mn)}:${pad(s)}` : `${mn}:${pad(s)}`;
}

/**
 * Body-density layout heuristic: `blog` for substantive commentary, `compact`
 * for empty/short bodies. Caller passes the rendered headings count and the
 * raw body text length so this stays pure (no `astro:content` render).
 */
export function layoutFor(
  headingsCount: number,
  bodyText: string,
  thresholdChars = 600,
): "compact" | "blog" {
  if (headingsCount > 0) return "blog";
  const cleaned = bodyText.replace(/<!--[\s\S]*?-->/g, "").replace(/[\[\]#*_>`()-]/g, "").trim();
  return cleaned.length >= thresholdChars ? "blog" : "compact";
}
