import { DEFAULT_LOCALE, LOCALES, localizePath, type Locale } from "@/lib/i18n";
import { absUrl, type SeoMeta } from "@/lib/seo";

import { conceptosphereStrings } from "./strings";

export function conceptospherePath(locale: Locale): string {
  return localizePath("/conceptosphere/", locale);
}

export function conceptosphereSeo(site: URL | undefined, locale: Locale): SeoMeta {
  const strings = conceptosphereStrings(locale);
  const canonical = absUrl(site, conceptospherePath(locale));
  return {
    title: strings.seo.title,
    description: strings.seo.description,
    canonical,
    ogImage: null,
    ogType: "website",
    alternates: [
      ...LOCALES.map((loc) => ({
        hreflang: loc,
        href: absUrl(site, conceptospherePath(loc)),
      })),
      {
        hreflang: "x-default",
        href: absUrl(site, conceptospherePath(DEFAULT_LOCALE)),
      },
    ],
    jsonLd: null,
    locale,
  };
}

export function conceptosphereAlternate(locale: Locale): Partial<Record<Locale, string>> {
  const other = locale === "ru" ? "en" : "ru";
  return { [other]: conceptospherePath(other) };
}
