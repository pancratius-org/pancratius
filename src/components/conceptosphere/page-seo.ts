import { DEFAULT_LOCALE, LOCALES, LOCALE_META, localizePath, type Locale } from "@/lib/i18n";
import { absUrl, type SeoMeta } from "@/lib/seo";

import { conceptosphereStrings } from "./strings.ts";

function conceptospherePath(locale: Locale): string {
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
    ogLocale: LOCALE_META[locale].ogLocale,
    siteName: LOCALE_META[locale].siteLabel,
  };
}

export function conceptosphereAlternate(locale: Locale): Partial<Record<Locale, string>> {
  // Link to every other authored locale (currently the one non-active locale).
  const out: Partial<Record<Locale, string>> = {};
  for (const loc of LOCALES) {
    if (loc !== locale) out[loc] = conceptospherePath(loc);
  }
  return out;
}
