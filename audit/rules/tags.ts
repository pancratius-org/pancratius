import type { Rule, RuleContext } from "../lib/rule.ts";
import type { Finding } from "../lib/finding.ts";
import { runPythonCheck } from "../lib/python.ts";

export const pan006cTagLocalization: Rule = {
  id: "PAN006C-tag-localization",
  title: "PAN006C: content tags match the canonical per-locale glossary",
  tier: "core",
  run(ctx: RuleContext): Finding[] {
    return runPythonCheck(ctx, {
      id: "PAN006C-tag-localization",
      category: "content-i18n",
      severity: "fatal",
      script: "tag_consistency.py",
      contract:
        "Tags are per-entry and language-bound: a Russian entry carries the normalized canonical tag key, its English translation carries the English label (data/tag-glossary.json). Video playlist titles used as tags follow the same rule.",
      why: "An unglossaried or wrongly-cased tag leaks Russian onto an English page and splinters the per-locale filter into duplicate chips for one concept.",
      repair:
        "Add the canonical RU key + EN label to data/tag-glossary.json, then normalize the entry's tags/playlist titles to match. Re-check with `uv run python audit/tag_consistency.py`.",
      doNotFixBy:
        "Whitelisting a drifted label in the glossary or filtering the duplicate chip out of the UI; the committed frontmatter tags must be the canonical glossary labels.",
    });
  },
};
