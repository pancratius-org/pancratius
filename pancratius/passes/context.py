# import-pure: no filesystem mutation
"""Typed pass context values shared by the import pass pipeline."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from pancratius import ir
from pancratius.content_catalog import IndexHit
from pancratius.locales import Locale

if TYPE_CHECKING:
    from pancratius.passes.register import RegisterModel


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
class RulesOnlyRegister:
    """Use deterministic register rules without a trained model."""


@dataclass(frozen=True)
class ModelBackedRegister:
    """Use the trained register model where available."""

    model: RegisterModel


type RegisterClassifier = RulesOnlyRegister | ModelBackedRegister


@dataclass(frozen=True)
class Context:
    """Pass parameters and capabilities injected by the composition point."""

    lang: Locale
    demote_levels: int = 1
    bibliography: BibliographyLookup = field(default_factory=BibliographyLookup)
    register_classifier: RegisterClassifier = field(default_factory=RulesOnlyRegister)
    lineation: LineationCorrections = field(default_factory=LineationCorrections)
    scripture: ScripturePins = field(default_factory=ScripturePins)
    diagnostics: ir.DiagnosticSink = field(default_factory=list)
