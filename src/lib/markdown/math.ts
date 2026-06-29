import { fileURLToPath } from "node:url";
import temml from "temml";
import type { MdastPluginDefinition } from "satteri";

/**
 * Render `$$…$$` math to MathML at build time.
 *
 * Sätteri parses dollar-math into `math` (display) and `inlineMath` (inline) MDAST
 * nodes whose `value` is the raw TeX; this replaces each with Temml's MathML so the
 * page ships a real `<math>` element — no client-side KaTeX, no image. Working from
 * the MDAST node (not the lowered `<pre><code class="language-math">` HTML) keeps the
 * one delimiter `$$…$$` covering both display and inline, which is why the site keeps
 * `singleDollarTextMath: false` (a lone `$` stays literal currency).
 *
 * `throwOnError` makes a malformed formula fail the build with its file and source,
 * rather than emit a red error box into a published page.
 */
function toMathml(latex: string, displayMode: boolean, fileURL: URL | undefined): string {
  try {
    return temml.renderToString(latex, { displayMode, throwOnError: true });
  } catch (error) {
    const where = fileURL ? fileURLToPath(fileURL) : "markdown source";
    const detail = error instanceof Error ? error.message : String(error);
    throw new Error(`Invalid math \`${latex}\` in ${where}: ${detail}`);
  }
}

export const temmlMathPlugin: MdastPluginDefinition = {
  name: "temml-math",
  // Replacing the math node with an `html` node keeps display math a top-level
  // `<math display="block">` and inline math inside its paragraph; returning
  // `{ rawHtml }` instead would wrap both in a stray `<p>`.
  math: (node, ctx) => {
    ctx.replaceNode(node, { type: "html", value: toMathml(node.value, true, ctx.fileURL) });
  },
  inlineMath: (node, ctx) => {
    ctx.replaceNode(node, { type: "html", value: toMathml(node.value, false, ctx.fileURL) });
  },
};
