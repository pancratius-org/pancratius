import type { Rule, RuleContext } from "../lib/rule.ts";
import type { Finding } from "../lib/finding.ts";
import { runPythonCheck } from "../lib/python.ts";

export const pan006cTagLocalization: Rule = {
  id: "PAN006C-tag-localization",
  title: "PAN006C: content tags match the canonical per-locale glossary",
  tier: "core",
  async run(ctx: RuleContext): Promise<Finding[]> {
    return runPythonCheck(ctx, {
      id: "PAN006C-tag-localization",
      category: "content-i18n",
      severity: "fatal",
      script: "tag_consistency.py",
      contract:
        "Tags are per-entry and language-bound: a Russian entry carries the normalized canonical tag key, its English translation carries the English label (data/tag-glossary.yaml). A video's playlists are identified by YouTube id: each id maps to a canonical key under `playlists:`, and the committed playlist title is that key's label for the locale.",
      why: "An unglossaried or wrongly-cased tag leaks Russian onto an English page and splinters the per-locale filter into duplicate chips for one concept.",
      repair:
        "Map the playlist id to its canonical RU key under `playlists:` in data/tag-glossary.yaml; add a new RU key + EN label only for a new concept. Then normalize the entry's tags and playlist titles to the glossary labels. Re-check with `uv run python audit/tag_consistency.py`.",
      doNotFixBy:
        "Whitelisting a drifted label in the glossary or filtering the duplicate chip out of the UI; the committed frontmatter tags must be the canonical glossary labels.",
    });
  },
};
