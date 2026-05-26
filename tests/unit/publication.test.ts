import assert from "node:assert/strict";
import { describe, test } from "node:test";

import { markdownToPlainText } from "../../src/lib/publication/plain-text.ts";
import {
  isPoemTitleLine,
  poemFirstStanza,
  poemPreviewLines,
} from "../../src/lib/publication/poetry.ts";
import { renderPublicWorkMarkdown } from "../../src/lib/publication/public-markdown.ts";
import { parseMarkdownDocument } from "../../src/lib/publication/source.ts";

const ORIGIN = "https://example.test";

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
