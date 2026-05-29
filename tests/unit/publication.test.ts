import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { join, relative } from "node:path";
import { describe, test } from "node:test";

import { markdownToPlainText } from "../../src/lib/publication/plain-text.ts";
import {
  isPoemTitleLine,
  poemFirstStanza,
  poemPreviewLines,
} from "../../src/lib/publication/poetry.ts";
import {
  publicWorkMarkdownAssetPaths,
  renderPublicWorkMarkdown,
} from "../../src/lib/publication/public-markdown.ts";
import { parseMarkdownDocument } from "../../src/lib/publication/source.ts";

const ORIGIN = "https://example.test";

type CurrentCorpusMarkdown = {
  bundleKey: string;
  file: string;
  kind: "book" | "poem";
};

function currentWorkMarkdownFiles(): CurrentCorpusMarkdown[] {
  const entries: CurrentCorpusMarkdown[] = [];
  for (const root of [
    { kind: "book" as const, segment: "books" },
    { kind: "poem" as const, segment: "poetry" },
  ]) {
    const rootDir = join(process.cwd(), "src", "content", root.segment);
    collectMarkdownFiles(rootDir, root.kind, rootDir, entries);
  }
  return entries;
}

function collectMarkdownFiles(
  dir: string,
  kind: "book" | "poem",
  rootDir: string,
  entries: CurrentCorpusMarkdown[],
): void {
  for (const dirent of readdirSync(dir, { withFileTypes: true })) {
    const file = join(dir, dirent.name);
    if (dirent.isDirectory()) {
      collectMarkdownFiles(file, kind, rootDir, entries);
      continue;
    }
    if (!dirent.isFile() || !dirent.name.endsWith(".md")) continue;
    const bundleKey = relative(rootDir, file).split(/[\\/]/)[0] ?? "";
    entries.push({ bundleKey, file, kind });
  }
}

describe("parseMarkdownDocument", () => {
  test("parses mapping frontmatter and returns the Markdown body", () => {
    const parsed = parseMarkdownDocument("---\ntitle: Test\nnumber: 7\n---\n\n# Body\n");
    assert.deepEqual(parsed.frontmatter, { title: "Test", number: 7 });
    assert.equal(parsed.body, "# Body\n");
  });

  test("treats non-frontmatter documents as body-only unless frontmatter is required", () => {
    assert.deepEqual(parseMarkdownDocument("# Body\n"), {
      frontmatter: {},
      body: "# Body\n",
    });
    assert.throws(
      () => parseMarkdownDocument("# Body\n", { frontmatter: "required" }),
      /missing frontmatter/,
    );
  });

  test("requires the closing frontmatter fence to be its own line", () => {
    assert.throws(
      () => parseMarkdownDocument("---\ntitle: Test\n--- not a fence\n# Body\n"),
      /unclosed frontmatter/,
    );
  });
});

describe("renderPublicWorkMarkdown", () => {
  test("reports the public asset paths needed by local image refs", () => {
    const paths = publicWorkMarkdownAssetPaths(
      [
        "![Alt](./images/pic.jpg)",
        "![Other](images/other.jpg \"Caption\")",
        '<img src="./images/raw.jpg" alt="Raw">',
        "![Root](/assets/books/work-1/images/root.jpg)",
        "![Remote](https://cdn.example.test/image.jpg)",
      ].join("\n"),
      { kind: "book", bundleKey: "work-1" },
    );

    assert.deepEqual(paths, [
      "assets/books/work-1/images/other.jpg",
      "assets/books/work-1/images/pic.jpg",
      "assets/books/work-1/images/raw.jpg",
      "assets/books/work-1/images/root.jpg",
    ]);
  });

  test("rewrites work-local image refs to fully-qualified publication URLs", () => {
    const rendered = renderPublicWorkMarkdown(
      "---\ntitle: Test\n---\n\n![Alt](./images/pic.jpg)\n![Other](images/other.jpg \"Caption\")\n",
      { origin: ORIGIN, work: { kind: "book", bundleKey: "work-1" } },
    );

    assert.equal(
      rendered,
      [
        "![Alt](https://example.test/assets/books/work-1/images/pic.jpg)",
        "![Other](https://example.test/assets/books/work-1/images/other.jpg \"Caption\")",
        "",
      ].join("\n"),
    );
    assert.doesNotMatch(rendered, /!\[[^\]]*]\((?!https?:\/\/)/);
  });

  test("uses the explicit origin for root-relative image refs", () => {
    const rendered = renderPublicWorkMarkdown(
      "![Alt](/assets/books/work-1/images/pic.jpg)\n",
      { origin: "https://example.test/subpath", work: { kind: "book", bundleKey: "work-1" } },
    );
    assert.equal(rendered, "![Alt](https://example.test/assets/books/work-1/images/pic.jpg)\n");
  });

  test("refuses unsupported image refs instead of publishing broken URLs", () => {
    assert.throws(
      () => renderPublicWorkMarkdown(
        "![Alt](./cover.ru.jpg)\n",
        { origin: ORIGIN, work: { kind: "book", bundleKey: "work-1" } },
      ),
      /Unsupported local public Markdown image path/,
    );
    assert.throws(
      () => renderPublicWorkMarkdown(
        "![Alt](data:image\/png;base64,abc)\n",
        { origin: ORIGIN, work: { kind: "book", bundleKey: "work-1" } },
      ),
      /Unsupported public Markdown image URL scheme/,
    );
  });
});

describe("renderPublicWorkMarkdown raw HTML policy", () => {
  test("refuses unsupported raw HTML instead of stripping it", () => {
    assert.throws(
      () => renderPublicWorkMarkdown(
        "<aside>foo</aside>\n",
        { origin: ORIGIN, work: { kind: "book", bundleKey: "work-1" } },
      ),
      /Unsupported raw HTML "<aside>".*book\/work-1/,
    );
  });

  test("refuses unsafe HTML anchor href schemes before rendering Markdown links", () => {
    for (const href of ["javascript:alert(1)", "java&#115;cript:alert(1)"]) {
      assert.throws(
        () => renderPublicWorkMarkdown(
          `<a href="${href}">x</a>\n`,
          { origin: ORIGIN, work: { kind: "book", bundleKey: "work-1" } },
        ),
        /unsupported href URL scheme "javascript"/,
      );
    }
  });

  test("renders canonical work HTML wrappers and inline publication tags", () => {
    const rendered = renderPublicWorkMarkdown(
      [
        '<div class="verse-block">Line <strong>one</strong><br><span dir="rtl">RTL text</span></div>',
        '<blockquote class="epigraph"><p>Quote <em>text</em><br>next '
          + '<a href="https://example.test/ref?x=1&amp;y=2">link</a></p><footer>Source</footer></blockquote>',
        '<p class="signature">Name</p>',
        '<img src="./images/pic.jpg" alt="Image &amp; sign">',
      ].join("\n\n"),
      { origin: ORIGIN, work: { kind: "book", bundleKey: "work-1" } },
    );

    assert.equal(
      rendered,
      [
        "Line **one**  ",
        "RTL text",
        "",
        "",
        "> Quote *text*",
        "> next [link](https://example.test/ref?x=1&y=2)",
        "> — Source",
        "",
        "",
        "Name",
        "",
        "",
        "![Image & sign](https://example.test/assets/books/work-1/images/pic.jpg)",
        "",
      ].join("\n"),
    );
  });

  test("refuses raw HTML comments, declarations, and processing instructions", () => {
    for (const raw of ["<!-- hidden -->", "<!doctype html>", "<?xml version=\"1.0\"?>"]) {
      assert.throws(
        () => renderPublicWorkMarkdown(
          `${raw}\n`,
          { origin: ORIGIN, work: { kind: "book", bundleKey: "work-1" } },
        ),
        /Unsupported raw HTML/,
      );
    }
  });

  test("does not treat ordinary comparison operators as raw HTML", () => {
    assert.equal(
      renderPublicWorkMarkdown(
        "The comparison 2 < 3 and 5 > 4 stays textual.\n",
        { origin: ORIGIN, work: { kind: "book", bundleKey: "work-1" } },
      ),
      "The comparison 2 < 3 and 5 > 4 stays textual.\n",
    );
  });
});

describe("renderPublicWorkMarkdown lineation and corpus smoke", () => {
  test("infers poem hard breaks from kind", () => {
    assert.equal(
      renderPublicWorkMarkdown(
        "First line\nSecond line\n\nNext stanza\n",
        { origin: ORIGIN, work: { kind: "poem", bundleKey: "poem-1" } },
      ),
      "First line  \nSecond line\n\nNext stanza\n",
    );
    assert.equal(
      renderPublicWorkMarkdown(
        '<div class="verse-block"><p>Line one<br>Line two</p></div>',
        { origin: ORIGIN, work: { kind: "book", bundleKey: "book-1" } },
      ),
      "Line one  \nLine two\n",
    );
  });

  test("renders the current book and poem corpus as public Markdown", () => {
    const failures: string[] = [];
    for (const entry of currentWorkMarkdownFiles()) {
      try {
        renderPublicWorkMarkdown(
          readFileSync(entry.file, "utf-8"),
          { origin: ORIGIN, work: { kind: entry.kind, bundleKey: entry.bundleKey } },
        );
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        failures.push(`${relative(process.cwd(), entry.file)}: ${message}`);
      }
    }

    assert.deepEqual(failures, []);
  });
});

describe("markdownToPlainText", () => {
  test("turns common Markdown syntax into readable plain text", () => {
    const flat = markdownToPlainText("# Heading\n\n![Cover](cover.jpg)\n\n- **Bold** [link](https://example.test)\n");
    assert.equal(flat, "Heading\n\nCover\n\nBold link\n");
  });
});

describe("poetry excerpts", () => {
  test("skips a repeated title line in previews", () => {
    const preview = poemPreviewLines("Заголовок...\n\nПервая\nВторая\nТретья\n", "Заголовок", 2);
    assert.equal(preview, "Первая\nВторая");
  });

  test("returns the first stanza after the preview start", () => {
    const stanza = poemFirstStanza("Title\n\nLine one\nLine two\n\nLine three\n", "Title");
    assert.equal(stanza, "Line one\nLine two");
  });

  test("shares title-line comparison across poetry previews", () => {
    assert.equal(isPoemTitleLine("Заголовок...", "Заголовок"), true);
    assert.equal(isPoemTitleLine("Аз есмь Христос, и Бог во мне живёт,", "Аз есмь Христос"), false);
  });
});
