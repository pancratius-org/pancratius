import assert from "node:assert/strict";
import { describe, test } from "node:test";

import { decodeEntities } from "../../audit/rules/degradation.ts";

describe("decodeEntities", () => {
  test("decodes the named entities Astro emits", () => {
    assert.equal(decodeEntities("a &amp; b"), "a & b");
    assert.equal(decodeEntities("&lt;tag&gt;"), "<tag>");
    assert.equal(decodeEntities("say &quot;hi&quot;"), 'say "hi"');
    assert.equal(decodeEntities("it&#39;s"), "it's");
    assert.equal(decodeEntities("a&nbsp;b"), "a b");
  });

  test("decodes numeric and hex references (incl. Cyrillic)", () => {
    assert.equal(decodeEntities("&#1056;"), "Р"); // U+0420 CYRILLIC ER
    assert.equal(decodeEntities("&#x420;"), "Р");
    assert.equal(decodeEntities("&#x41;&#66;"), "AB");
  });

  test("decodes each entity exactly once — no double-unescaping", () => {
    // `&amp;lt;` is an escaped `&lt;` — must stay literal `&lt;`, not collapse to `<`.
    assert.equal(decodeEntities("&amp;lt;"), "&lt;");
    assert.equal(decodeEntities("&amp;amp;"), "&amp;");
    assert.equal(decodeEntities("&amp;#1056;"), "&#1056;");
  });

  test("leaves unknown or out-of-range references untouched", () => {
    assert.equal(decodeEntities("&copy; &unknown;"), "&copy; &unknown;");
    assert.equal(decodeEntities("&#x110000;"), "&#x110000;"); // > max code point, no throw
    assert.equal(decodeEntities("plain & text"), "plain & text"); // bare ampersand
  });
});
