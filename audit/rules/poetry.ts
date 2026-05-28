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
