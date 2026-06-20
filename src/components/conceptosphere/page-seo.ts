import { LOCALES, LOCALE_META, localizePath, type Locale } from "@/lib/i18n";
import { OG_CONCEPTOSPHERE_PATH, ogImageUrl } from "@/lib/og";
import { absUrl, type SeoMeta } from "@/lib/seo";

import { conceptosphereStrings } from "./strings.ts";

function conceptospherePath(locale: Locale): string {
  return localizePath("/conceptosphere/", locale);
}

export function conceptosphereSeo(locale: Locale): SeoMeta {
  const strings = conceptosphereStrings(locale);
  const canonical = absUrl(conceptospherePath(locale));
  const alternates = LOCALES.map((loc) => ({
    hreflang: loc,
    href: absUrl(conceptospherePath(loc)),
  }));
  // x-default → EN when authored (the global face), else the default-locale version.
  const fallback = alternates.find((a) => a.hreflang === "en") ?? alternates[0];
  return {
    title: strings.seo.title,
    description: strings.seo.description,
    canonical,
    ogImage: ogImageUrl(OG_CONCEPTOSPHERE_PATH, locale),
    ogImageAlt: strings.seo.title,
    ogType: "website",
    alternates: fallback
      ? [...alternates, { hreflang: "x-default", href: fallback.href }]
      : alternates,
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
