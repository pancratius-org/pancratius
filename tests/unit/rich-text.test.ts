import assert from "node:assert/strict";
import { describe, test } from "node:test";

import { richParagraphs, shouldCollapse, type TextSegment } from "../../src/lib/rich-text.ts";

const texts = (segs: TextSegment[]) => segs.map(s => s.text).join("");
// `assert.ok` narrows away undefined without a non-null assertion.
function present<T>(v: T): NonNullable<T> {
  assert.ok(v != null);
  return v;
}
const firstPara = (text: string) => present(richParagraphs(text)[0]);

describe("richParagraphs", () => {
  test("splits on blank lines and drops empty paragraphs", () => {
    const paras = richParagraphs("Один.\n\n\nДва.\n\n   \n\nТри.");
    assert.deepEqual(paras.map(texts), ["Один.", "Два.", "Три."]);
  });

  test("keeps single newlines inside a paragraph as text", () => {
    const paras = richParagraphs("строка один\nстрока два");
    assert.equal(paras.length, 1);
    assert.equal(texts(present(paras[0])), "строка один\nстрока два");
  });

  test("linkifies a bare URL into a link segment", () => {
    assert.deepEqual(firstPara("читай тут https://example.com/x спасибо"), [
      { kind: "text", text: "читай тут " },
      { kind: "link", href: "https://example.com/x", text: "https://example.com/x" },
      { kind: "text", text: " спасибо" },
    ]);
  });

  test("does not swallow trailing sentence punctuation into the link", () => {
    const segs = firstPara("см. https://example.com/p.");
    const link = segs.find(s => s.kind === "link");
    assert.equal(link?.kind === "link" ? link.href : null, "https://example.com/p");
    assert.equal(texts(segs).endsWith("."), true);
  });

  test("keeps a balanced trailing ) that belongs to the URL", () => {
    const segs = firstPara("see https://en.wikipedia.org/wiki/Foo_(bar) now");
    const link = segs.find(s => s.kind === "link");
    assert.equal(link?.kind === "link" ? link.href : null, "https://en.wikipedia.org/wiki/Foo_(bar)");
  });

  test("drops an unbalanced trailing ) (parenthesized URL)", () => {
    const segs = firstPara("(https://example.com/x)");
    const link = segs.find(s => s.kind === "link");
    assert.equal(link?.kind === "link" ? link.href : null, "https://example.com/x");
    assert.equal(texts(segs).endsWith(")"), true);
  });

  test("multiple URLs in one paragraph", () => {
    const segs = firstPara("a https://x.io b http://y.io c");
    assert.deepEqual(segs.filter(s => s.kind === "link").map(s => s.text), ["https://x.io", "http://y.io"]);
  });

  test("plain paragraph with no URL is a single text segment", () => {
    assert.deepEqual(firstPara("просто текст без ссылок"), [{ kind: "text", text: "просто текст без ссылок" }]);
  });

  test("empty / whitespace-only input yields no paragraphs", () => {
    assert.deepEqual(richParagraphs(""), []);
    assert.deepEqual(richParagraphs("   \n\n  \t "), []);
  });
});

describe("shouldCollapse", () => {
  test("short text stays open", () => {
    assert.equal(shouldCollapse("Одна короткая мысль."), false);
  });
  test("long text collapses (char threshold)", () => {
    assert.equal(shouldCollapse("слово ".repeat(120)), true);
  });
  test("many short lines collapse (line threshold)", () => {
    assert.equal(shouldCollapse(Array.from({ length: 11 }, () => "x").join("\n")), true);
  });
});
