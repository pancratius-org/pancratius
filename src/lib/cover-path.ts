// The cover asset-naming policy, enforced in one place. A cover lives inside
// its content bundle as `cover.<lang>.<ext>` and is referenced from frontmatter
// as a relative path like `./cover.ru.jpg`. Both works and videos share this
// shape; the only difference is whether SVG is permitted.

import { LOCALES, type Locale } from "./locales";

export interface CoverRef {
  /** The relative path as it appears in frontmatter, e.g. `./cover.ru.jpg`. */
  rel: string;
  /** Resolved language hint inferred from the filename. */
  lang: Locale;
  /** File extension lowercased without leading dot. */
  ext: string;
}

const RASTER_EXT = "jpe?g|png|webp|avif";

/**
 * Parse and validate a `cover:` frontmatter value. Returns null when absent;
 * throws on a malformed path so a naming-policy violation surfaces at build.
 * `context` names the noun for the error ("Cover path" / "Video cover path").
 */
export function parseCoverPath(
  value: string | null | undefined,
  opts: { context: string; allowSvg?: boolean },
): CoverRef | null {
  if (!value) return null;
  const exts = opts.allowSvg ? `${RASTER_EXT}|svg` : RASTER_EXT;
  const re = new RegExp(`^\\./cover\\.(${LOCALES.join("|")})\\.(${exts})$`, "i");
  const match = re.exec(value.trim());
  if (!match) {
    throw new Error(
      `${opts.context} ${JSON.stringify(value)} violates asset-naming policy. ` +
      `Expected ./cover.<${LOCALES.join("|")}>.<jpg|png|webp|avif> inside the bundle.`,
    );
  }
  const lang = match[1];
  const ext = match[2];
  if (lang === undefined || ext === undefined) {
    throw new Error(`${opts.context} ${JSON.stringify(value)} matched without locale or extension`);
  }
  return {
    rel: value.trim(),
    lang: lang.toLowerCase() as Locale,
    ext: ext.toLowerCase(),
  };
}
