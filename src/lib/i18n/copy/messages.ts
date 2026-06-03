import type { Locale } from "../../locales";
import { plRu, RU_PLURALS } from "../plural";

// ─────────────────────────────────────────────────────────────────────
// Calendar localization. Russian needs two month forms: nominative for a
// month *heading* («Май 2026») and genitive for a *date* («31 мая»). English
// uses one set of names with a different word order.
// ─────────────────────────────────────────────────────────────────────

const MONTHS_NOMINATIVE: Record<Locale, readonly string[]> = {
  ru: ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"],
  en: ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"],
};

const MONTHS_GENITIVE: Record<Locale, readonly string[]> = {
  ru: ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"],
  en: MONTHS_NOMINATIVE.en,
};

/** Monday-first weekday abbreviations for the calendar header. */
export const WEEKDAYS_SHORT: Record<Locale, readonly string[]> = {
  ru: ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"],
  en: ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
};

// `iso` is always a `published_at` the `isoDate` schema has already validated as
// `YYYY-MM-DD`, so the fallbacks are unreachable belt-and-braces, not real cases.
function parts(iso: string): { y: number; m: number; d: number } {
  const [y, m, d] = iso.split("-").map(Number);
  return { y: y ?? 0, m: m ?? 1, d: d ?? 1 };
}

/** Nominative month + year, e.g. «Май 2026» / "May 2026". */
export function monthHeading(locale: Locale, year: number, month1: number): string {
  return `${MONTHS_NOMINATIVE[locale][month1 - 1]} ${year}`;
}

/** A post date. RU «31 мая» / «31 мая 2026»; EN "May 31" / "May 31, 2026". */
export function formatMessageDate(locale: Locale, iso: string, opts: { withYear?: boolean } = {}): string {
  const { y, m, d } = parts(iso);
  if (locale === "en") {
    const base = `${MONTHS_NOMINATIVE.en[m - 1]} ${d}`;
    return opts.withYear ? `${base}, ${y}` : base;
  }
  const base = `${d} ${MONTHS_GENITIVE.ru[m - 1]}`;
  return opts.withYear ? `${base} ${y}` : base;
}

// ─────────────────────────────────────────────────────────────────────
// Index page.
// ─────────────────────────────────────────────────────────────────────

export interface MessagesIndexCopy {
  eyebrow: string;
  /** Heading word after the count, declined for `count`. */
  headingLabel(count: number): string;
  sub(count: number): string;
  /** Shown on /en/ when no English posts exist yet. */
  emptySub: string;
  fullCatalogLink?: string;
  calendarAria: string;
  /** Chip on the current month's leaf. */
  todayChip: string;
  /** Pluralized "N посланий" tally beside a month, or under a marked day. */
  postsOnDay(count: number): string;
  /** Quiet tally for a month with no letters. */
  emptyMonth: string;
}

export const messagesIndexCopy = {
  ru: {
    eyebrow: "Послания",
    headingLabel: (count) => plRu(count, RU_PLURALS.message),
    sub: () => "Письма, что приходят по дням. Откройте день — и он откликнется.",
    emptySub: "Послания пока только на русском.",
    calendarAria: "Календарь посланий",
    todayChip: "Сегодня",
    postsOnDay: (count) => `${count} ${plRu(count, RU_PLURALS.message)}`,
    emptyMonth: "тишина",
  },
  en: {
    eyebrow: "Epistles",
    headingLabel: (count) => (count === 1 ? "epistle" : "epistles"),
    sub: () => "Letters that arrive by the day. Open a day and it answers.",
    emptySub: "The epistles are in Russian for now.",
    fullCatalogLink: "Read them in Russian.",
    calendarAria: "Calendar of epistles",
    todayChip: "Today",
    postsOnDay: (count) => `${count} ${count === 1 ? "epistle" : "epistles"}`,
    emptyMonth: "silence",
  },
} satisfies Record<Locale, MessagesIndexCopy>;

// ─────────────────────────────────────────────────────────────────────
// Detail page.
// ─────────────────────────────────────────────────────────────────────

export interface MessagePageCopy {
  back(total: number): string;
  meta: string;
  navLabel: string;
  todayLabel: string;
  allLink: string;
  pagerAria: string;
  prevAria: string;
  nextAria: string;
  relatedVideosHeading(count: number): string;
  tagsLabel: string;
}

export const messagePageCopy = {
  ru: {
    back: (total) => `← ко всем посланиям (${total})`,
    meta: "Послание",
    navLabel: "Послания",
    todayLabel: "Сегодня",
    allLink: "Все послания",
    pagerAria: "Другие послания",
    prevAria: "Предыдущее послание",
    nextAria: "Следующее послание",
    relatedVideosHeading: (count) => (count === 1 ? "Связанное видео" : "Связанные видео"),
    tagsLabel: "Темы",
  },
  en: {
    back: (total) => `← to all epistles (${total})`,
    meta: "Epistle",
    navLabel: "Epistles",
    todayLabel: "Today",
    allLink: "All epistles",
    pagerAria: "Other epistles",
    prevAria: "Previous epistle",
    nextAria: "Next epistle",
    relatedVideosHeading: (count) => (count === 1 ? "Related video" : "Related videos"),
    tagsLabel: "Topics",
  },
} satisfies Record<Locale, MessagePageCopy>;
