export const DEFAULT_PUBLICATION_ORIGIN = "https://pancratius.ru";

export function publicationOrigin(raw = process.env.PUBLIC_SITE_URL): string {
  return new URL(raw ?? DEFAULT_PUBLICATION_ORIGIN).origin;
}
