import type { Locale } from "../../locales";
import { spellEnglishCardinal } from "../numbers";
import { plRu, RU_PLURALS } from "../plural";

export interface LibraryFilterCopy {
  search: string;
  searchAria: string;
  shown: string;
  topic: string;
  query: string;
  reset: string;
  empty: string;
  tagsLabel: string;
  k: string;
}

export const libraryFilterCopy = {
  ru: {
    search: "Найти по названию",
    searchAria: "Поиск по названиям книг",
    shown: "из",
    topic: "тема —",
    query: "поиск —",
    reset: "сбросить",
    empty: "Ничего не найдено.",
    tagsLabel: "Темы",
    k: "⌘K",
  },
  en: {
    search: "Search by title",
    searchAria: "Search book titles",
    shown: "of",
    topic: "tag —",
    query: "query —",
    reset: "reset",
    empty: "Nothing matched.",
    tagsLabel: "Tags",
    k: "⌘K",
  },
} satisfies Record<Locale, LibraryFilterCopy>;

export interface BookCardCopy {
  /** Prefix for the cover image alt text, e.g. "Cover" / "Обложка". */
  coverAltPrefix: string;
}

export const bookCardCopy = {
  ru: { coverAltPrefix: "Обложка" },
  en: { coverAltPrefix: "Cover" },
} satisfies Record<Locale, BookCardCopy>;

export interface BooksIndexCopy {
  eyebrow: string;
  headingLabel(count: number): string;
  sub(total: number, available: number): string;
  fullCatalogLink?: string;
}

export const booksIndexCopy = {
  ru: {
    eyebrow: "Обсерватория Света",
    headingLabel: (total) => plRu(total, RU_PLURALS.book),
    sub: (total) => `Полное собрание — от 01 до ${String(total).padStart(2, "0")}. По числу, по году, по метке. Каждая страница — отдельная книга. Читайте, скачивайте, делитесь.`,
  },
  en: {
    eyebrow: "Observatory of Light",
    headingLabel: () => "books in English",
    sub: (total, available) => `Of ${total} books in the library, ${available} have been translated into English.`,
    fullCatalogLink: "See the full Russian catalogue. Read, download, and share.",
  },
} satisfies Record<Locale, BooksIndexCopy>;

export interface RelatedCopy {
  see_also: string;
  similar: string;
  star: string;
  projectHeading: string;
  projectSub: string;
}

export const relatedCopy = {
  ru: {
    see_also: "См. также",
    similar: "Похожие книги",
    star: "Совпадение по двум показателям",
    projectHeading: "Читать дальше",
    projectSub: "Книги, в которых этот проект продолжается.",
  },
  en: {
    see_also: "See also",
    similar: "Similar books",
    star: "Both signals converge here",
    projectHeading: "Read further",
    projectSub: "Books where this project unfolds further.",
  },
} satisfies Record<Locale, RelatedCopy>;

// The label before a list of download links — one source for every surface
// that offers downloads (work actions, colophon).
export const downloadLabelCopy: Record<Locale, string> = {
  ru: "Скачать:",
  en: "Download:",
};

export interface ColophonCopy {
  rights_before: string;
  rights_link: string;
  rights_after: string;
  machineTranslation: string;
  original: string;
}

export const colophonCopy = {
  ru: {
    rights_before: "Все тексты — в ",
    rights_link: "общественном достоянии (CC0)",
    rights_after: ". Берите. Переводите. Передавайте.",
    machineTranslation: "Машинный перевод с русского.",
    original: "Оригинал",
  },
  en: {
    rights_before: "All texts are in the ",
    rights_link: "public domain (CC0)",
    rights_after: ". Take them. Translate. Pass them on.",
    machineTranslation: "Machine translation from Russian.",
    original: "Original",
  },
} satisfies Record<Locale, ColophonCopy>;

export interface BookPageCopy {
  back(total: number): string;
  meta: string;
  coverAlt(title: string): string;
  srPrefix(number: string): string;
  pagerAria: string;
}

export const bookPageCopy = {
  ru: {
    back: (total) => `← к ${total} ${plRu(total, RU_PLURALS.bookDative)}`,
    meta: "Книга",
    coverAlt: (title) => `Обложка книги: ${title}`,
    srPrefix: (number) => `Книга ${number}. `,
    pagerAria: "Другие книги",
  },
  en: {
    back: () => "← back to library",
    meta: "Book",
    coverAlt: (title) => `Cover: ${title}`,
    srPrefix: (number) => `Book ${number}. `,
    pagerAria: "Other books",
  },
} satisfies Record<Locale, BookPageCopy>;

export interface PoemPageCopy {
  back: string;
  meta: string;
}

export const poemPageCopy = {
  ru: {
    back: "← к стихам",
    meta: "Стихотворение",
  },
  en: {
    back: "← back to poetry",
    meta: "Poem",
  },
} satisfies Record<Locale, PoemPageCopy>;

export interface PoetryIndexCopy {
  eyebrow(count: number): string;
  headingLabel(count: number): string;
  intro: string;
  /** Hero attribution prefix, e.g. "← Стихотворение №" / "← Poem No.". */
  heroAttrPrefix: string;
  fallbackBanner?: string;
  /** Month abbreviations for "<month> <year>" date formatting (index 0 = January). */
  months: readonly string[];
}

export const poetryIndexCopy: Record<Locale, PoetryIndexCopy> = {
  ru: {
    eyebrow: () => "Псалмы наших дней",
    headingLabel: (count) => plRu(count, RU_PLURALS.poem),
    intro: "Тексты, не оторванные от молитвы. Стихи, рождённые в тишине.",
    heroAttrPrefix: "← Стихотворение №",
    months: [
      "янв.", "фев.", "мар.", "апр.", "мая", "июня",
      "июля", "авг.", "сент.", "окт.", "нояб.", "дек.",
    ],
  },
  en: {
    eyebrow: spellEnglishCardinal,
    headingLabel: () => "poems",
    intro: "The originals are in Russian. Each row links to the poem; English translations will land here as they are authored.",
    heroAttrPrefix: "← Poem No.",
    fallbackBanner: "Original Russian — translations forthcoming.",
    months: [
      "Jan", "Feb", "Mar", "Apr", "May", "Jun",
      "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
    ],
  },
};

/**
 * Format an ISO date (`2025-09-30`) as "<month> <year>" using the locale's
 * month abbreviations. Falls back to the raw year (or the input) when the date
 * can't be parsed. Keeps Russian month names off `/en/` pages.
 */
export function formatMonthYear(iso: string, months: readonly string[]): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  if (!m) return iso;
  const year = m[1];
  const monthText = m[2];
  if (year === undefined || monthText === undefined) {
    throw new Error("ISO date parser matched without year or month captures");
  }
  const month = parseInt(monthText, 10);
  const monthName = months[month - 1];
  if (month >= 1 && month <= 12 && monthName !== undefined) return `${monthName} ${year}`;
  return year;
}
