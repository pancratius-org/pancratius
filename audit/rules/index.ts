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
import { pan010DocxIntegrity, pan025TranslatedDocxTransfer } from "./docx.ts";
import { pan006bMarkdownStructure } from "./markdown.ts";
import { pan006cTagLocalization } from "./tags.ts";
import { pan006bPoetryStanzas, pan006bLineationBreaks } from "./poetry.ts";
import { pan008PublicMarkdownAssets } from "./downloads.ts";
import { pan014InternalLinks } from "./crawl.ts";
import { pan012CiSeparation } from "./ownership.ts";
import { pan016SourceLanguage, pan016UiFramework } from "./stack.ts";
import {
  pan017ImportWorkKinds,
  pan018WriterOnlyMutation,
  pan019CliVerifyBoundary,
  pan024CliTargetFlags,
} from "./imports.ts";
import { pan020TypographyRoleDrift } from "./typography.ts";
import { pan026QuoteDirection } from "./quotes.ts";
import { pan027Terminology } from "./terminology.ts";
import { pan021ConceptosphereI18n } from "./conceptosphere.ts";
import { pan022ConceptosphereDegradation } from "./degradation.ts";
import { contentQualityRules } from "./content_quality.ts";
import { pan023TypeDomainPy, pan023TypeDomainTs } from "./type_domain.ts";
import { pan028VideoHook } from "./videos.ts";

export const RULES: readonly Rule[] = [
  // Fatal core (run on `npm run audit:repo`):
  pan001PathBoundary,
  pan002,
  pan003Locales,
  pan003Kinds,
  pan004CorpusCollections,
  pan004BulkArchiveKinds,
  pan004DuplicateIdentity,
  pan006bMarkdownStructure,
  pan006cTagLocalization,
  pan006bPoetryStanzas,
  pan006bLineationBreaks,
  pan007AssetRefs,
  pan010DocxIntegrity,
  pan025TranslatedDocxTransfer,
  pan012CiSeparation,
  pan016SourceLanguage,
  pan016UiFramework,
  pan017ImportWorkKinds,
  pan018WriterOnlyMutation,
  pan019CliVerifyBoundary,
  pan024CliTargetFlags,
  pan020TypographyRoleDrift,
  pan026QuoteDirection,
  pan027Terminology,
  pan028VideoHook,
  pan021ConceptosphereI18n,
  // Post-build tier (need dist/; run only on `npm run audit:post-build`):
  pan008PublicMarkdownAssets,
  pan014InternalLinks,
  pan022ConceptosphereDegradation,
  // Non-blocking heuristics (run only on `npm run audit:agent`):
  pan023TypeDomainTs,
  pan023TypeDomainPy,
  ...contentQualityRules,
];
