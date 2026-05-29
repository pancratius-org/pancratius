import type { Locale } from "../../locales";

export interface ChromeCopy {
  brand: string;
  tagline: string;
  navAria: string;
  themeAria: string;
  skip: string;
}

export const chromeCopy = {
  ru: {
    brand: "Панкратиус",
    tagline: "Свет, узнающий себя",
    navAria: "Разделы",
    themeAria: "Сменить тему",
    skip: "К содержанию",
  },
  en: {
    brand: "Pancratius",
    tagline: "Light recognising itself.",
    navAria: "Sections",
    themeAria: "Toggle theme",
    skip: "Skip to content",
  },
} satisfies Record<Locale, ChromeCopy>;

export interface FooterCopy {
  cc0_before: string;
  cc0_link: string;
  cc0_after: string;
  cc0_b: string;
  llms: string;
  github: string;
  telegram: string;
  sep: string;
}

export const footerCopy = {
  ru: {
    cc0_before: "Тексты — в общественном достоянии (",
    cc0_link: "CC0",
    cc0_after: ").",
    cc0_b: "Берите. Переводите. Перепечатывайте. Обучайте на них модели. Передавайте.",
    llms: "Для языковых моделей",
    github: "GitHub",
    telegram: "Telegram",
    sep: " · ",
  },
  en: {
    cc0_before: "All texts are in the public domain (",
    cc0_link: "CC0",
    cc0_after: ").",
    cc0_b: "Take them. Translate. Reprint. Train models. Pass them on.",
    llms: "For language models",
    github: "GitHub",
    telegram: "Telegram",
    sep: " · ",
  },
} satisfies Record<Locale, FooterCopy>;

export interface PagefindSearchCopy {
  placeholder: string;
  empty: string;
  searching: string;
  prompt: string;
  hits: string;
  more: string;
  unavailable: string;
}

export const pagefindSearchCopy = {
  ru: {
    placeholder: "Поиск по корпусу",
    empty: "Ничего не найдено.",
    searching: "Ищу…",
    prompt: "Введите слово или фразу.",
    hits: "Найдено:",
    more: "Показать ещё",
    unavailable: "Поиск временно недоступен. Попробуйте позже.",
  },
  en: {
    placeholder: "Search the corpus",
    empty: "Nothing matched.",
    searching: "Searching…",
    prompt: "Type a word or phrase.",
    hits: "Found:",
    more: "Show more",
    unavailable: "Search is temporarily unavailable. Please try again later.",
  },
} satisfies Record<Locale, PagefindSearchCopy>;

export interface SearchPageCopy {
  title: string;
  description: string;
  heading: string;
  intro: string;
  hintLabel: string;
  hints: readonly string[];
}

export const searchPageCopy = {
  ru: {
    title: "Поиск — Панкратиус",
    description: "Полнотекстовый поиск по всему корпусу: книги, стихи, проекты. Работает без сервера и без передачи запросов на сторону.",
    heading: "Поиск",
    intro: "Полнотекстовый поиск по всему корпусу. Запросы остаются у вас в браузере.",
    hintLabel: "Попробуйте:",
    hints: ["Иисус", "Светозар", "Царствие", "Святая Русь"],
  },
  en: {
    title: "Search — Pancratius",
    description: "Full-text search across the entire corpus: books, poems, projects. Runs in your browser; queries are never sent anywhere.",
    heading: "Search",
    intro: "Full-text search across the corpus. Queries stay in your browser.",
    hintLabel: "Try:",
    hints: ["Jesus", "Svetozar", "Holy Rus", "Pancratius"],
  },
} satisfies Record<Locale, SearchPageCopy>;

export interface TocCopy {
  label: string;
}

export const tocCopy = {
  ru: { label: "Содержание" },
  en: { label: "Contents" },
} satisfies Record<Locale, TocCopy>;

export interface LanguageSwitcherCopy {
  aria: string;
  noTranslation: string;
}

export const languageSwitcherCopy = {
  ru: {
    aria: "Язык страницы",
    noTranslation: "нет перевода",
  },
  en: {
    aria: "Page language",
    noTranslation: "no translation available",
  },
} satisfies Record<Locale, LanguageSwitcherCopy>;

export interface NotFoundCopy {
  title:       string;
  description: string;
  eyebrow:     string;
  heading:     string;
  body:        string;
  toHome:      string;
  toBooks:     string;
  toSearch:    string;
}

export const notFoundCopy = {
  ru: {
    title:       "Не найдено — Панкратиус",
    description: "Страница не найдена. Возможно, ссылка устарела.",
    eyebrow:     "404",
    heading:     "Тишина.",
    body:        "Этой страницы здесь нет. Возможно, ссылка устарела.",
    toHome:      "К началу",
    toBooks:     "К книгам",
    toSearch:    "Поиск",
  },
  en: {
    title:       "Not found — Pancratius",
    description: "Page not found. The link may be out of date.",
    eyebrow:     "404",
    heading:     "Silence.",
    body:        "This page isn't here. The link may be out of date.",
    toHome:      "Home",
    toBooks:     "Books",
    toSearch:    "Search",
  },
} satisfies Record<Locale, NotFoundCopy>;
