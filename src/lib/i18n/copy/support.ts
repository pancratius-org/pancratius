import type { Locale } from "../../locales";

export interface DownloadsPageCopy {
  aria: string;
  empty: string;
  stampBefore: string;
  items: string;
  size: string;
  numberLocale: string;
  blurb: Record<"md" | "pdf" | "epub", string>;
}

export const downloadsPageCopy = {
  ru: {
    aria: "Архивы библиотеки",
    empty: "Архивы будут собраны после следующей сборки сайта.",
    stampBefore: "Архивы пересобираются при каждом деплое. Последний - ",
    items: "Произведений",
    size: "Размер",
    numberLocale: "ru-RU",
    blurb: {
      md: "Чистый Markdown всех книг и стихов.",
      pdf: "Книги и стихи в PDF - для печати и офлайн-чтения.",
      epub: "Книги в EPUB - для электронных читалок.",
    },
  },
  en: {
    aria: "Library archives",
    empty: "Archives will be built with the next site build.",
    stampBefore: "Archives are rebuilt on every deploy. Last build - ",
    items: "Works",
    size: "Size",
    numberLocale: "en-US",
    blurb: {
      md: "Clean Markdown for every book and poem.",
      pdf: "Books and poems as PDFs for print and offline reading.",
      epub: "Books as EPUB files for e-readers.",
    },
  },
} satisfies Record<Locale, DownloadsPageCopy>;

export interface SupportChannelsCopy {
  aria: string;
  copy: string;
  copied: string;
  failed: string;
  copyAria(label: string): string;
}

export const supportChannelsCopy = {
  ru: {
    aria: "Каналы поддержки",
    copy: "копировать",
    copied: "скопировано",
    failed: "ошибка",
    copyAria: (label) => `Скопировать: ${label}`,
  },
  en: {
    aria: "Channels of support",
    copy: "copy",
    copied: "copied",
    failed: "failed",
    copyAria: (label) => `Copy: ${label}`,
  },
} satisfies Record<Locale, SupportChannelsCopy>;
