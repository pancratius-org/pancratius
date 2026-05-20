import { defineCollection } from "astro:content";
import { glob } from "astro/loaders";
import { z } from "astro/zod";

const lang = z.enum(["ru", "en"]);
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
    const m = entry.match(/^(.+?)\/(ru|en)\.md$/);
    if (!m) throw new Error(`Unexpected ${kind} path: ${entry}`);
    return `${m[1]}--${m[2]}`;
  };

const books = defineCollection({
  loader: glob({
    pattern: "**/*.md",
    base: "./content/books",
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
    base: "./content/poetry",
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
    base: "./content/projects",
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
    base: "./content/pages",
    generateId: ({ entry }) => {
      const m = entry.match(/^(.+?)\/(ru|en)\.md$/);
      if (!m) throw new Error(`Unexpected page path: ${entry}`);
      return `${m[1]}--${m[2]}`;
    },
  }),
  schema: z.object({
    slug: asciiSlug,
    lang,
    title: z.string().min(1),
    description: z.string().min(1),
    eyebrow: z.string().optional(),
    sub: z.string().optional(),
    // Optional portrait block — currently used on /about/ to render a
    // left-rail figure beside the bio. Other pages can opt in by
    // supplying these fields.
    portrait: z
      .object({
        src: z.string().min(1),
        alt: z.string().min(1),
        caption: z.string().optional(),
        meta: z.string().optional(),
      })
      .optional(),
    // Optional structured facts list — rendered as a 2-column <dl> at the
    // foot of the body. Used by /about/ for vital statistics.
    facts: z
      .array(z.object({ label: z.string().min(1), value: z.string().min(1) }))
      .optional(),
    // Optional channels widget — used by /support/ to render donation
    // methods as a hairline-divided column with copy-to-clipboard
    // affordances. Each entry is a row; `kind` selects how the value is
    // rendered (image for `qr`, mono text + copy for `card` / `text`,
    // anchor + copy for `link`).
    channels: z
      .array(
        z.object({
          kind: z.enum(["qr", "card", "link", "text"]),
          label: z.string().min(1),
          value: z.string().min(1),
          caption: z.string().optional(),
        })
      )
      .optional(),
  }),
});

export const collections = { books, poetry, projects, pages };
