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
  title_is_untranslated: z.boolean().optional(),
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
    original_filenames: z.array(z.string()).default([]),
    tags: z.array(z.string()).default([]),
    cover: z.string().nullable().optional(),
    abstract: z.string().optional(),
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
    original_filename: z.string().optional(),
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
    original_filename: z.string().optional(),
    cover: z.string().nullable().optional(),
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
    nav_label: z.string().optional(),
    show_in_nav: z.boolean().default(false),
    nav_order: z.number().int().optional(),
  }),
});

export const collections = { books, poetry, projects, pages };
