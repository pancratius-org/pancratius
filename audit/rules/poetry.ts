import type { Rule, RuleContext } from "../lib/rule.ts";
import type { Finding } from "../lib/finding.ts";
import { runPythonCheck } from "../lib/python.ts";

export const pan006bPoetryStanzas: Rule = {
  id: "PAN006B-poetry-stanzas",
  title: "PAN006B: poetry Markdown must preserve DOCX stanza structure",
  tier: "core",
  run(ctx: RuleContext): Finding[] {
    return runPythonCheck(ctx, {
      id: "PAN006B-poetry-stanzas",
      category: "conversion-fidelity",
      severity: "fatal",
      script: "poetry_stanzas.py",
      contract:
        "Converted poetry Markdown matches the DOCX stanza structure: empty Word paragraphs and multi-line source paragraphs define stanza breaks (content-model.md).",
      why: "A stanza-boundary mismatch is verse data loss — the poem's lineation is content, not formatting.",
      repair:
        "Fix the committed poem body or the importer path that produced it, then verify with `uv run python audit/poetry_stanzas.py`.",
      doNotFixBy:
        "Silencing the audit or weakening stanza comparison; title duplicates and stanza collapses are corpus bugs.",
    });
  },
};

export const pan006bLineationBreaks: Rule = {
  id: "PAN006B-lineation-breaks",
  title: "PAN006B: generated lineation keeps its two-space hard breaks",
  tier: "core",
  run(ctx: RuleContext): Finding[] {
    return runPythonCheck(ctx, {
      id: "PAN006B-lineation-breaks",
      category: "conversion-fidelity",
      severity: "fatal",
      script: "lineation_breaks.py",
      contract:
        "Generated work Markdown encodes lineation as CommonMark two-trailing-space hard breaks: every non-final line of a lineated wrapper stanza (books/projects) and of a poem-body stanza ends with exactly two trailing spaces (content-model.md / decisions.md).",
      why: "A trimmed trailing-space break is SILENT lineation loss — the renderer reflows the line into the next and nothing else fails. A whitespace-trimming formatter/editor/git-filter would erase the whole verse encoding undetected.",
      repair:
        "Restore the two-space breaks by regenerating the affected body, and verify no formatter strips `.md` trailing whitespace (.editorconfig must carry `[*.md] trim_trailing_whitespace = false`). Re-check with `uv run python audit/lineation_breaks.py`.",
      doNotFixBy:
        "Silencing the audit, or switching verse back to raw-newline + CSS pre-line; lineation must be encoded in the Markdown, not inferred by CSS.",
    });
  },
};
