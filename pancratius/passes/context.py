# import-pure: no filesystem mutation
"""Typed pass context values shared by the import pass pipeline."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from pancratius import ir
from pancratius.content_catalog import IndexHit
from pancratius.intent_inference.policies import RegisterPolicy, RulesOnlyRegisterPolicy
from pancratius.locales import Locale


@dataclass(frozen=True)
class BibliographyLookup:
    """Catalog title lookup used by the bibliography lift pass."""

    by_title: Mapping[str, IndexHit] = field(default_factory=dict)


@dataclass(frozen=True)
class LineationCorrections:
    """Editorial lineation corrections keyed by source `w:p` ordinal."""

    by_ordinal: Mapping[int, ir.LineationRegister] = field(default_factory=dict)


@dataclass(frozen=True)
class ScripturePins:
    """Unmarked-canon scripture pins keyed by source `w:p` ordinal."""

    by_ordinal: Mapping[int, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Context:
    """Pass parameters and capabilities injected by the composition point."""

    lang: Locale
    demote_levels: int = 1
    bibliography: BibliographyLookup = field(default_factory=BibliographyLookup)
    register_policy: RegisterPolicy = field(default_factory=RulesOnlyRegisterPolicy)
    lineation: LineationCorrections = field(default_factory=LineationCorrections)
    scripture: ScripturePins = field(default_factory=ScripturePins)
    diagnostics: ir.DiagnosticSink = field(default_factory=list)
