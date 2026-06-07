import type { Locale } from "../../locales";
import type { VideoPlatform } from "../../videos";

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
    eyebrow: "Голос",
    headingLabel: (count) => count === 1 ? "видео" : "видео",
    sub: () => "Каталог видео Панкратиуса. Каждое со своей страницей и, где есть, с письменным разбором.",
    channelsHeading: "Каналы",
    channelsSub: "Точки сбора. Подпишитесь там, где удобнее.",
    openChannel: "Открыть канал",
  },
  en: {
    eyebrow: "Voice",
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
}

export const videoCardCopy = {
  ru: { coverAltPrefix: "Превью" },
  en: { coverAltPrefix: "Thumbnail" },
} satisfies Record<Locale, VideoCardCopy>;

export interface VideoPageCopy {
  back(total: number): string;
  meta: string;
  watchOn(platform: string): string;
  mirrorsLabel: string;
  publishedLabel: string;
  durationLabel: string;
  playAria: string;
  pagerAria: string;
  channelLabel: string;
  showMore: string;
  showLess: string;
  platformLabel(platform: VideoPlatform): string;
}

const platformLabels = {
  ru: {
    youtube: "YouTube",
    vimeo:   "Vimeo",
    rutube:  "RUTUBE",
    odysee:  "Odysee",
    self:    "зеркало",
    other:   "ссылка",
  },
  en: {
    youtube: "YouTube",
    vimeo:   "Vimeo",
    rutube:  "RUTUBE",
    odysee:  "Odysee",
    self:    "mirror",
    other:   "link",
  },
} satisfies Record<Locale, Record<VideoPlatform, string>>;

export const videoPageCopy = {
  ru: {
    back: (total) => `← к ${total} видео`,
    meta: "Видео",
    watchOn: (platform) => `Смотреть на ${platform}`,
    mirrorsLabel: "Также:",
    publishedLabel: "Опубликовано:",
    durationLabel: "Длительность:",
    playAria: "Запустить видео",
    pagerAria: "Другие видео",
    channelLabel: "Канал:",
    showMore: "Читать полностью ▾",
    showLess: "Свернуть ▴",
    platformLabel: (platform) => platformLabels.ru[platform],
  },
  en: {
    back: () => "← back to videos",
    meta: "Video",
    watchOn: (platform) => `Watch on ${platform}`,
    mirrorsLabel: "Also:",
    publishedLabel: "Published:",
    durationLabel: "Duration:",
    playAria: "Play video",
    pagerAria: "Other videos",
    channelLabel: "Channel:",
    showMore: "Read more ▾",
    showLess: "Show less ▴",
    platformLabel: (platform) => platformLabels.en[platform],
  },
} satisfies Record<Locale, VideoPageCopy>;
