import { defineCollection } from "astro:content";
import { glob } from "astro/loaders";
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
  kind: z.enum(["book", "poem", "project"]),
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
  cover_is_placeholder: z.boolean().optional(),
  cross_refs: z.array(crossRefEntry).optional(),
};

const workEntryId = (kind: "book" | "poem" | "project") =>
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

const projects = defineCollection({
  loader: glob({
    pattern: "**/*.md",
    base: "./src/content/projects",
    generateId: workEntryId("project"),
  }),
  schema: z.object({
    kind: z.literal("project"),
    number: z.number().int().positive(),
    slug: asciiSlug,
    title: z.string().min(1),
    cover: z.string().nullable().optional(),
    // Short editorial tagline rendered under the title on the project
    // masthead. One sentence; sets the position-paper register before
    // the body opens. Optional — projects without a tagline fall back
    // to no sub-line and let the negation opener carry.
    tagline: z.string().optional(),
    ...baseWorkFields,
  }),
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

export const collections = { books, poetry, projects, pages };
