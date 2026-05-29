import type { Locale } from "../../locales";

export interface VideosIndexCopy {
  eyebrow: string;
  headingLabel(count: number): string;
  sub(total: number, available: number): string;
  fullCatalogLink?: string;
  channelsHeading: string;
  channelsSub: string;
  openChannel: string;
}

export const videosIndexCopy = {
  ru: {
    eyebrow: "Видео",
    headingLabel: (count) => count === 1 ? "видео" : "видео",
    sub: (total) => `Каталог видео Панкратиуса — ${total}. Каждое со своей страницей и, где есть, с письменным разбором.`,
    channelsHeading: "Каналы",
    channelsSub: "Точки сбора. Подпишитесь там, где удобнее.",
    openChannel: "Открыть канал",
  },
  en: {
    eyebrow: "Video",
    headingLabel: () => "videos in English",
    sub: (total, available) => `Of ${total} catalogued videos, ${available} have English commentary.`,
    fullCatalogLink: "See the full Russian catalogue.",
    channelsHeading: "Channels",
    channelsSub: "Where the uploads live. Subscribe wherever it suits.",
    openChannel: "Open channel",
  },
} satisfies Record<Locale, VideosIndexCopy>;

export interface VideoCardCopy {
  coverAltPrefix: string;
  bothLangsBadge: string;
  fallbackBadge: string;
}

export const videoCardCopy = {
  ru: {
    coverAltPrefix: "Превью",
    bothLangsBadge: "RU · EN",
    fallbackBadge:  "Russian only",
  },
  en: {
    coverAltPrefix: "Thumbnail",
    bothLangsBadge: "RU · EN",
    fallbackBadge:  "Russian only",
  },
} satisfies Record<Locale, VideoCardCopy>;

export interface VideoPageCopy {
  back(total: number): string;
  meta: string;
  coverAlt(title: string): string;
  watchOn(platform: string): string;
  mirrorsLabel: string;
  publishedLabel: string;
  durationLabel: string;
  playAria: string;
  pagerAria: string;
  channelLabel: string;
}

export const videoPageCopy = {
  ru: {
    back: (total) => `← к ${total} видео`,
    meta: "Видео",
    coverAlt: (title) => `Превью видео: ${title}`,
    watchOn: (platform) => `Смотреть на ${platform}`,
    mirrorsLabel: "Также:",
    publishedLabel: "Опубликовано:",
    durationLabel: "Длительность:",
    playAria: "Запустить видео",
    pagerAria: "Другие видео",
    channelLabel: "Канал:",
  },
  en: {
    back: () => "← back to videos",
    meta: "Video",
    coverAlt: (title) => `Thumbnail: ${title}`,
    watchOn: (platform) => `Watch on ${platform}`,
    mirrorsLabel: "Also:",
    publishedLabel: "Published:",
    durationLabel: "Duration:",
    playAria: "Play video",
    pagerAria: "Other videos",
    channelLabel: "Channel:",
  },
} satisfies Record<Locale, VideoPageCopy>;
