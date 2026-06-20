// Pure formatters for video data — no imports, so tests can load this directly
// under node --test.

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
 * Localize a YouTube embed URL: set the player UI language (`hl`); when
 * `forcedCaptionLanguage` is set, also prefer that caption track and force
 * captions on (`cc_lang_pref` + `cc_load_policy`). The caller passes a caption
 * locale for translated pages, since the audio stays in the default locale.
 */
export function localizedEmbedUrl(
  base: string,
  playerLanguage: string,
  forcedCaptionLanguage: string | null,
): string {
  const url = new URL(base);
  url.searchParams.set("hl", playerLanguage);
  if (forcedCaptionLanguage !== null) {
    url.searchParams.set("cc_lang_pref", forcedCaptionLanguage);
    url.searchParams.set("cc_load_policy", "1");
  }
  return url.toString();
}
