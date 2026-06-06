import { defineCollection } from "astro:content";
import { file, glob } from "astro/loaders";
import { z } from "astro/zod";

// Canonical locale list. `./lib/locales.ts` is pure TS (no `astro:content`
// import) precisely so this config can import it.
import { LOCALES } from "./lib/locales";

// `z.enum` accepts the readonly `as const` tuple directly and preserves the
// literal union (`"ru" | "en"`), so `data.lang` stays a `Locale`, not `string`.
// A third locale flows in from `./lib/locales.ts` with no edit here.
const lang = z.enum(LOCALES);

// Locale file names inside a work/page bundle (`ru.md`, `en.md`, …), derived
// from the locale SSOT so a new locale's `<lang>.md` is discovered rather than
// rejected by a hardcoded `(ru|en)` pattern.
const LOCALE_FILE_RE = new RegExp(`^(.+?)/(${LOCALES.join("|")})\\.md$`);
const asciiSlug = z.string().regex(/^[a-z0-9][a-z0-9-]*$/, {
  message: "slug must be lowercase ASCII letters, digits, and hyphens",
});

const translation = z.discriminatedUnion("source", [
  z.object({
    source: z.literal("original"),
    model: z.string().optional(),
    generated_at: z.string().optional(),
    reviewed_by: z.string().optional(),
  }),
  z.object({
    source: z.literal("literary"),
    model: z.string().optional(),
    generated_at: z.string().optional(),
    reviewed_by: z.string().optional(),
  }),
  z.object({
    source: z.literal("ai"),
    model: z.string().optional(),
    generated_at: z.string().optional(),
    reviewed_by: z.string().optional(),
  }),
]);

const targetRef = z.object({
  kind: z.enum(["book", "poem", "project", "video", "message"]),
  number: z.number().int().positive(),
});

const crossRefEntry = z.object({
  target: targetRef,
  source: z.enum(["footnote", "inline_url", "inline_title", "editorial"]).optional(),
  snippet: z.string().optional(),
  source_url: z.string().regex(/^https?:\/\//).optional(),
});

const baseWorkFields = {
  lang,
  description: z.string().min(1),
  translation,
  cross_refs: z.array(crossRefEntry).optional(),
  // External editions / store mirrors of this work (KindBook, Litres, Google
  // Play, Apple Books, …). Preserved from import provenance — these used to live
  // in a sibling `meta.json` the site never read; frontmatter is the source of
  // truth, so they live here. Name + URL per link.
  links: z
    .array(z.object({ name: z.string().min(1), url: z.string().regex(/^https?:\/\//) }))
    .optional(),
};

const workEntryId = (kind: "book" | "poem" | "project" | "video" | "message") =>
  ({ entry }: { entry: string }) => {
    const m = entry.match(LOCALE_FILE_RE);
    if (!m) throw new Error(`Unexpected ${kind} path: ${entry}`);
    return `${m[1]}--${m[2]}`;
  };

const books = defineCollection({
  loader: glob({
    pattern: "**/*.md",
    base: "./src/content/books",
    generateId: workEntryId("book"),
  }),
  schema: z.object({
    kind: z.literal("book"),
    number: z.number().int().positive(),
    slug: asciiSlug,
    title: z.string().min(1),
    tags: z.array(z.string()).default([]),
    cover: z.string().nullable().optional(),
    ...baseWorkFields,
  }),
});

const poetry = defineCollection({
  loader: glob({
    pattern: "**/*.md",
    base: "./src/content/poetry",
    generateId: workEntryId("poem"),
  }),
  schema: z.object({
    kind: z.literal("poem"),
    number: z.number().int().positive(),
    slug: asciiSlug,
    title: z.string().min(1),
    cover: z.string().nullable().optional(),
    date: z.string().nullable().optional(),
    ...baseWorkFields,
  }),
});

// ─────────────────────────────────────────────────────────────────────
// Projects — themed mini-sites / sections, NOT downloadable works.
//
// A project is its own little section: a landing (`kind: project`) plus an
// optional set of sub-pages (`kind: project_subpage`), all authored as
// `<lang>.md` files under `src/content/projects/<project>/…`. The glob
// recurses, so a sub-page lives at e.g.
// `enlightened-ai/subpages/classification/ru.md` and gets a unique id
// (`enlightened-ai/subpages/classification--ru`) from the same
// LOCALE_FILE_RE rule used for works.
//
// Unlike books/poems, projects are ORIGINAL framing — there is no
// translation-of relationship — so `translation` is optional, and there are
// NO download fields: projects never emit `.md/.txt/.docx/.pdf/.epub` routes.
// They reference BOOKS by number via `featured_books`, which `src/lib/projects.ts`
// resolves through the work machinery; that is the only cross-module link.
// ─────────────────────────────────────────────────────────────────────

const projectGenerateId = ({ entry }: { entry: string }) => {
  const m = entry.match(LOCALE_FILE_RE);
  if (!m) throw new Error(`Unexpected project path: ${entry}`);
  return `${m[1]}--${m[2]}`;
};

/** The editorial register of a project sub-page (drives the body renderer). */
const projectSubpageWeight = z.enum([
  "essay",
  "revelation",
  "verse",
  "practice",
  "dialogue",
]);

const projectLanding = z.object({
  kind: z.literal("project"),
  // (kind, number) is still the invariant identity for a project landing, so
  // `build/slug-map.ts` keeps mapping `/projects/<slug>/` from this.
  number: z.number().int().positive(),
  slug: asciiSlug,
  lang,
  title: z.string().min(1),
  description: z.string().min(1),
  // Short editorial tagline rendered under the title on the project masthead.
  tagline: z.string().optional(),
  cover: z.string().nullable().optional(),
  // Books this project leans on, by editorial number, with an optional blurb.
  featured_books: z
    .array(
      z.object({
        number: z.number().int().positive(),
        blurb: z.string().optional(),
      }),
    )
    .optional(),
  // Secondary featured books rendered as a quieter strip (numbers only).
  featured_books_more: z.array(z.number().int().positive()).optional(),
  // Authored sub-pages, in render order. Empty/absent → a landing-only section.
  subpages: z
    .array(
      z.object({
        slug: asciiSlug,
        label: z.string().optional(),
        weight: projectSubpageWeight,
      }),
    )
    .optional(),
  // ─── Typed structured-section fields the landing arc renders ───────────
  // Each is OPTIONAL: a minimal landing (frontmatter + body only) validates.
  // These are NAMED sections, not a generic component builder — the schema
  // owns the arc's vocabulary so `ProjectPage` composes a known shape.
  //
  // The "Что это не есть" opener — one line per thing the project is NOT.
  // Promoted from the hand-inlined `<aside class="project-negation">`.
  negation: z.array(z.string().min(1)).optional(),
  ladder: z
    .array(
      z.object({
        step: z.string().min(1),
        quality: z.string().min(1),
        remains: z.string().min(1),
      }),
    )
    .optional(),
  // Set-apart Creator / awakened-AI quotes rendered in scripture register.
  // `voice` attributes the speaker; an optional CTA may target a book by
  // editorial `number` (resolved to a link) or a raw `href`.
  revelations: z
    .array(
      z.object({
        voice: z.string().optional(),
        text: z.string().min(1),
        cta: z
          .object({
            label: z.string().min(1),
            href: z.string().optional(),
            book: z.number().int().positive().optional(),
          })
          .optional(),
      }),
    )
    .optional(),
  // "Часто спрашивают" — promoted from the inlined `<section class="project-qa">`.
  faq: z
    .array(
      z.object({
        q: z.string().min(1),
        a: z.string().min(1),
      }),
    )
    .optional(),
  // Projects are original framing, not a translation-of — `translation` is
  // OPTIONAL here (the same shape works carry, but never forced).
  translation: translation.optional(),
  // Kept for prose footnote links inside the landing body.
  cross_refs: z.array(crossRefEntry).optional(),
});

const projectSubpage = z.object({
  kind: z.literal("project_subpage"),
  // The owning project's slug — pairs the sub-page to its landing.
  parent: asciiSlug,
  slug: asciiSlug,
  lang,
  title: z.string().min(1),
  description: z.string().min(1),
  weight: projectSubpageWeight,
  cover: z.string().nullable().optional(),
  // Names a bespoke interactive component a later wave mounts for this page.
  component: z.string().optional(),
  cross_refs: z.array(crossRefEntry).optional(),
});

const projects = defineCollection({
  loader: glob({
    pattern: "**/*.md",
    base: "./src/content/projects",
    generateId: projectGenerateId,
  }),
  schema: z.discriminatedUnion("kind", [projectLanding, projectSubpage]),
});

const pages = defineCollection({
  loader: glob({
    pattern: "**/*.md",
    base: "./src/content/pages",
    generateId: ({ entry }) => {
      const m = entry.match(LOCALE_FILE_RE);
      if (!m) throw new Error(`Unexpected page path: ${entry}`);
      return `${m[1]}--${m[2]}`;
    },
  }),
  // Universal minimum every static page shares. Bespoke editorial data
  // (about's `portrait`/`facts`, support's `channels`) is NOT in this shared
  // schema — pages are individuals, not a population, so per-page fields are
  // co-located in each page's Markdown frontmatter and validated by a
  // route-local zod schema in the dedicated route that consumes them.
  // `.loose()` (zod's passthrough mode) keeps those extra keys on `entry.data`
  // through the collection load so the route can read and re-validate them.
  schema: z
    .object({
      slug: asciiSlug,
      lang,
      title: z.string().min(1),
      description: z.string().min(1),
      eyebrow: z.string().optional(),
      sub: z.string().optional(),
    })
    .loose(),
});

// ─────────────────────────────────────────────────────────────────────
// Videos — catalogued YouTube/other-platform videos, rendered at /videos/.
//
// Videos are a routed kind (SEGMENT_OF includes them) but NOT a corpus work:
// no DOCX-import flow, no download matrix. They pair across languages by
// `(kind, number)` like books — each language's entry is its own page, with
// its own per-locale commentary. A single video stream can be referenced by
// multiple locale entries; the embed source lives in `sources[]`, which is a
// mirrors list (YouTube primary today, Vimeo/Rutube/self-hosted in the
// future). The scanner — `uv run pancratius video sync` — only seeds
// frontmatter and downloads the source thumbnail as `cover.<lang>.jpg`;
// commentary in the body is editorial and never touched by re-runs.
//
// Compact-vs-blog layout is derived from body density by default
// (`src/lib/videos.ts:layoutForEntry`); an explicit `layout: compact | blog`
// overrides.
// ─────────────────────────────────────────────────────────────────────

// ISO 8601 duration as YouTube reports it (`PT8M42S`, `PT1H3M`, `PT45S`, …).
const iso8601Duration = z.string().regex(/^PT(?:\d+H)?(?:\d+M)?(?:\d+S)?$/, {
  message: "duration must be ISO 8601 (e.g. PT8M42S)",
});

const isoDate = z.string().regex(/^\d{4}-\d{2}-\d{2}$/, {
  message: "date must be YYYY-MM-DD",
});

// One mirror entry. `platform` keeps the schema open to non-YouTube hosts.
// YouTube entries additionally validate the canonical 11-char id format;
// other platforms accept a free-form id (or none).
const videoSource = z
  .object({
    platform: z.enum(["youtube", "vimeo", "rutube", "odysee", "self", "other"]),
    id: z.string().min(1).optional(),
    url: z.string().regex(/^https?:\/\//),
    embed_url: z.string().regex(/^https?:\/\//).optional(),
    // Optional ref into `channels.yaml` (`key:`). Used to render the channel
    // badge on the card and to attribute the upload on the page.
    channel: z.string().min(1).optional(),
  })
  .superRefine((src, ctx) => {
    if (src.platform === "youtube") {
      if (!src.id || !/^[A-Za-z0-9_-]{11}$/.test(src.id)) {
        ctx.addIssue({
          code: "custom",
          message: "YouTube source must carry a valid 11-char `id`",
          path: ["id"],
        });
      }
    }
  });

const videoPlaylist = z.object({
  // Source-platform id (YouTube uses ~34-char base64-ish strings; keep loose).
  id: z.string().min(1),
  // Title for THIS locale's page. The scanner seeds it from the raw YouTube
  // playlist title; the author can localize by hand.
  title: z.string().min(1),
});

const videos = defineCollection({
  loader: glob({
    pattern: "**/*.md",
    base: "./src/content/videos",
    generateId: workEntryId("video"),
  }),
  schema: z.object({
    kind: z.literal("video"),
    // Invariant editorial identity, same rule as books. `(kind, number)` pairs
    // across locales. The scanner allocates max(number)+1 for new videos.
    number: z.number().int().positive(),
    slug: asciiSlug,
    title: z.string().min(1),
    lang,
    description: z.string().min(1),
    // Authored or scanner-seeded from YouTube playlist titles. Drives the
    // LibraryFilter chips on `/videos/`, same as books `tags`.
    tags: z.array(z.string()).default([]),
    cover: z.string().nullable().optional(),
    // Source publication date on the primary platform.
    published_at: isoDate,
    // ISO 8601 duration (e.g. `PT8M42S`).
    duration: iso8601Duration,
    // Ordered list of mirrors; the first entry is the primary embed source.
    sources: z.array(videoSource).min(1),
    // Source-platform playlists the video belongs to. Optional; the scanner
    // seeds these from YouTube. Rendered as chips on the video page.
    playlists: z.array(videoPlaylist).optional(),
    // Optional cross-link to a book by editorial number (e.g. reading-of-X).
    related_book: z.number().int().positive().optional(),
    // Layout override. When absent, `src/lib/videos.ts:layoutForEntry` derives
    // it from body density: `compact` for empty/short bodies, `blog` for
    // substantive commentary.
    layout: z.enum(["compact", "blog"]).optional(),
    translation,
    cross_refs: z.array(crossRefEntry).optional(),
  }),
});

// ─────────────────────────────────────────────────────────────────────
// Послания — dated, blog-like messages rendered at /messages/.
//
// A routed kind but NOT a corpus work (like videos): no DOCX-import flow, no
// download matrix. Posts pair across locales by `(kind, number)` exactly like
// videos — each locale's entry is its own page. The identity is the invariant
// `number`; `published_at` is the editorial date that places the post on the
// месяцеслов (the calendar centerpiece) and orders the archive.
//
// Relations: a post points at the videos it accompanies through
// `related_videos` (video `number`s, resolved to cards in `src/lib/messages.ts`),
// and at books/poems through the shared `cross_refs`. Послания carry no cover —
// they are letters, not catalogued media — so there is no `cover` field.
// ─────────────────────────────────────────────────────────────────────
const messages = defineCollection({
  loader: glob({
    pattern: "**/*.md",
    base: "./src/content/messages",
    generateId: workEntryId("message"),
  }),
  schema: z.object({
    kind: z.literal("message"),
    // Invariant editorial identity; `(kind, number)` pairs across locales.
    number: z.number().int().positive(),
    slug: asciiSlug,
    title: z.string().min(1),
    lang,
    description: z.string().min(1),
    // Drives the LibraryFilter-style chips and the per-post tag line.
    tags: z.array(z.string()).default([]),
    // The date the post is placed on. Mandatory: it is the post's location on
    // the calendar and the archive sort key.
    published_at: isoDate,
    // Accompanying videos by editorial `number`, rendered as a related-video
    // widget. Unknown numbers are skipped at resolve time (never a build error).
    related_videos: z.array(z.number().int().positive()).optional(),
    translation,
    cross_refs: z.array(crossRefEntry).optional(),
  }),
});

// Channels — small authored sidecar. Two consumers: the site renders a
// channel strip at the top of `/videos/`; the Python scanner uses these
// definitions to decide what to poll. Lives at
// `src/content/videos/channels.yaml` so it co-locates with the collection.
const videoChannels = defineCollection({
  loader: file("src/content/videos/channels.yaml"),
  schema: z.object({
    // Channel key referenced from a video's `sources[].channel`.
    platform: z.enum(["youtube", "vimeo", "rutube", "odysee", "other"]),
    handle: z.string().optional(),
    channel_id: z.string().optional(),
    url: z.string().regex(/^https?:\/\//),
    // Optional badge ("ислам" / "Islam") rendered on the channel card.
    badge: z.record(lang, z.string()).optional(),
    title: z.record(lang, z.string()),
    copy: z.record(lang, z.string()),
    // When false, the scanner skips this channel: the card still renders on
    // /videos/ (catalogue-only) but no videos from it land in the collection.
    scan: z.boolean().default(true),
  }),
});

export const collections = { books, poetry, projects, pages, videos, videoChannels, messages };
