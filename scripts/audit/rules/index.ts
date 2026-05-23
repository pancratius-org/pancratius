// The rule registry — the one explicit list of every rule the harness runs.
// Adding a rule is two lines: import it, append it. No auto-discovery, no plugin
// framework (docs/audit-harness.md → "Implementation Shape"): the list is
// greppable and a future agent can read it top to bottom.

import type { Rule } from "../lib/rule.ts";

import { pan001PathBoundary } from "./paths.ts";
import { rule as pan002, pan003Locales, pan003Kinds } from "./locales.ts";
import {
  pan004CorpusCollections,
  pan004BulkArchiveKinds,
  pan004DuplicateIdentity,
} from "./projects.ts";
import { pan007AssetRefs } from "./assets.ts";
import { pan012CiSeparation } from "./ownership.ts";
import { pan016SourceLanguage, pan016UiFramework } from "./stack.ts";

export const RULES: readonly Rule[] = [
  pan001PathBoundary,
  pan002,
  pan003Locales,
  pan003Kinds,
  pan004CorpusCollections,
  pan004BulkArchiveKinds,
  pan004DuplicateIdentity,
  pan007AssetRefs,
  pan012CiSeparation,
  pan016SourceLanguage,
  pan016UiFramework,
];
