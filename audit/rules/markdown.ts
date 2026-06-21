import type { Rule, RuleContext } from "../lib/rule.ts";
import type { Finding } from "../lib/finding.ts";
import { runPythonCheck } from "../lib/python.ts";

export const pan006bMarkdownStructure: Rule = {
  id: "PAN006B-markdown-structure",
  title: "PAN006B: content Markdown avoids setext and lineated headings",
  tier: "core",
  run(ctx: RuleContext): Finding[] {
    return runPythonCheck(ctx, {
      id: "PAN006B-markdown-structure",
      category: "content-formatting",
      severity: "fatal",
      script: "markdown_structure.py",
      contract:
        "Content Markdown uses canonical `***` thematic breaks after frontmatter and never emits heading or divider markers inside lineated wrappers (content-model.md).",
      why: "A body `---` / `===` line can turn the preceding paragraph or hard-break verse stanza into a setext heading, polluting ToCs, anchors, the document outline, and assistive navigation.",
      repair:
        "Normalize source divider paragraphs through the importer to `ThematicBreak` / `***`; use blank stanza gaps for spacer lines inside lineated wrappers. Re-check with `uv run python audit/markdown_structure.py`.",
      doNotFixBy:
        "Filtering long headings out of the Table of Contents or hiding generated anchors; the committed Markdown structure itself must be canonical.",
    });
  },
};
