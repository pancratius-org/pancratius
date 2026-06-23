"""Post-translation diagnostics — find where a finished ``en.md`` likely went wrong.

Chunked translation fails in characteristic places, and we KNOW where the seams
are because we chose them: re-running the chunker on the source gives the exact
unit indices where two independently-translated chunks meet. Defects cluster
there — a line whose content was dropped and a neighbour duplicated, a recurring
term rendered differently on each side. So every finding is tagged ``at_seam``,
turning a whole-book reread into a focused look at a handful of boundaries.

Detectors (all deterministic, source-vs-translation, no model):
- DUPLICATE_ADJACENT — ``en[i] == en[i+1]`` while the sources differ (the model
  duplicated one line and dropped the other);
- DROPPED_CONTENT — a substantial source line whose translation is suspiciously
  short (content lost);
- RESIDUAL_CYRILLIC — Cyrillic left in the English (some genuine: a letter named
  as a letter, a math symbol — so it is MEDIUM, for review, not a hard fail);
- UNCLOSED_BOLD — an odd number of ``**`` (broken emphasis);
- STRUCTURE_DRIFT — en re-parses to a different unit count than the source.

The output locates each issue precisely so a repair pass (code or a Sonnet agent)
acts on units, not whole books.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from pancratius.translation.text.checks import Finding, Severity
from pancratius.translation.text.chunker import Chunk, plan_chunks
from pancratius.translation.text.config import TranslateConfig
from pancratius.translation.text.document import Document, TextUnit, Translations, UnitId

_CYRILLIC = re.compile(r"[А-Яа-яЁё]")
_CYRILLIC_RUN = re.compile(r"[А-Яа-яЁё]{2,}")


class FindingKind(StrEnum):
    DUPLICATE_ADJACENT = "duplicate_adjacent"
    DROPPED_CONTENT = "dropped_content"
    RESIDUAL_CYRILLIC = "residual_cyrillic"
    UNCLOSED_BOLD = "unclosed_bold"
    STRUCTURE_DRIFT = "structure_drift"


@dataclass(frozen=True, slots=True)
class AuditFinding:
    index: int
    unit_id: UnitId
    kind: FindingKind
    severity: Severity
    at_seam: bool
    detail: str


@dataclass(frozen=True, slots=True)
class BookAudit:
    book_key: str
    source_units: int
    target_units: int
    seam_indices: tuple[int, ...]
    findings: tuple[AuditFinding, ...]

    def at_seam(self) -> tuple[AuditFinding, ...]:
        return tuple(f for f in self.findings if f.at_seam)


def seam_indices(document: Document, config: TranslateConfig) -> set[int]:
    """The source-unit indices at chunk boundaries — the last unit of each chunk
    and the first of the next. Recomputed from the same chunker the run used, so
    these are exactly where two independently-translated chunks were stitched."""
    order = {unit.id: i for i, unit in enumerate(document.units)}
    chunks = plan_chunks(document, config)
    seams: set[int] = set()
    for position, chunk in enumerate(chunks):
        if not chunk.unit_ids:
            continue
        if position > 0:
            seams.add(order[chunk.unit_ids[0]])
        if position < len(chunks) - 1:
            seams.add(order[chunk.unit_ids[-1]])
    return seams


def _cyrillic_severity(text: str) -> Severity | None:
    """Cyrillic in English is usually a miss, but a single letter/symbol named as
    itself is genuine. Treat a multi-letter Cyrillic RUN as suspect (HIGH); a lone
    stray letter as MEDIUM; none as clean."""
    if not _CYRILLIC.search(text):
        return None
    return Severity.HIGH if _CYRILLIC_RUN.search(text) else Severity.MEDIUM


def audit_book(
    source: Document, target: Document, config: TranslateConfig, *, book_key: str
) -> BookAudit:
    seams = seam_indices(source, config)
    s_units, t_units = source.units, target.units
    findings: list[AuditFinding] = []

    if len(s_units) != len(t_units):
        findings.append(
            AuditFinding(
                index=min(len(s_units), len(t_units)),
                unit_id="",
                kind=FindingKind.STRUCTURE_DRIFT,
                severity=Severity.HIGH,
                at_seam=False,
                detail=f"source has {len(s_units)} units, translation has {len(t_units)}",
            )
        )

    n = min(len(s_units), len(t_units))
    for i in range(n):
        src, tgt = s_units[i].source, t_units[i].source
        near_seam = i in seams or (i - 1) in seams or (i + 1) in seams

        if tgt.count("**") % 2:
            findings.append(AuditFinding(i, t_units[i].id, FindingKind.UNCLOSED_BOLD,
                                         Severity.HIGH, near_seam, "odd number of '**'"))

        cyr = _cyrillic_severity(tgt)
        if cyr is not None:
            findings.append(AuditFinding(i, t_units[i].id, FindingKind.RESIDUAL_CYRILLIC,
                                         cyr, near_seam, f"Cyrillic in: {tgt[:60]!r}"))

        # Near-empty translation of a substantial source line = truncation. Kept
        # tight (English is often shorter than Russian, so a loose ratio is noise);
        # the common "dropped line" case shows up as DUPLICATE_ADJACENT instead.
        if len(src) > 100 and len(tgt) < 12:
            findings.append(AuditFinding(i, t_units[i].id, FindingKind.DROPPED_CONTENT,
                                         Severity.HIGH if near_seam else Severity.MEDIUM, near_seam,
                                         f"src {len(src)} chars → tgt {len(tgt)} (content may be lost)"))

        if i + 1 < n and tgt and tgt == t_units[i + 1].source and src != s_units[i + 1].source:
            findings.append(AuditFinding(i, t_units[i].id, FindingKind.DUPLICATE_ADJACENT,
                                         Severity.HIGH, near_seam,
                                         f"same translation as next unit, sources differ: {tgt[:60]!r}"))

    return BookAudit(
        book_key=book_key,
        source_units=len(s_units),
        target_units=len(t_units),
        seam_indices=tuple(sorted(seams)),
        findings=tuple(findings),
    )


# --- seam reconcile (the cross-boundary repair) -------------------------------
@dataclass(frozen=True, slots=True)
class Seam:
    """One chunk boundary: the tail of chunk ``a`` meets the head of chunk ``b``.
    The window is those neighbouring units, the only place a term can be rendered
    two ways by two independent passes."""

    a_index: int
    b_index: int
    window: tuple[TextUnit, ...]


def seam_windows(document: Document, chunks: Sequence[Chunk], *, k: int = 3) -> list[Seam]:
    """A window of the last ``k`` units of each chunk plus the first ``k`` of the
    next — the cross-boundary neighbourhood a reconcile pass needs to see at once."""
    index = document.unit_index()
    seams: list[Seam] = []
    for a, b in zip(chunks, chunks[1:], strict=False):
        tail = [index[uid] for uid in a.unit_ids[-k:]]
        head = [index[uid] for uid in b.unit_ids[:k]]
        if tail and head:
            seams.append(Seam(a.index, b.index, tuple(tail + head)))
    return seams


def inconsistent_term_seams(
    source: Document,
    translations: Translations,
    seams: Sequence[Seam],
    *,
    terms: Sequence[tuple[str, str]],
) -> set[int]:
    """Brief terms rendered two ways across the book, mapped to the seams whose
    window straddles a divergent rendering. For each ``(source, target)`` term, the
    units whose source contains the term split into those whose English contains the
    expected target and those that don't; when BOTH populations exist the term is
    inconsistent, and any seam window holding a unit on the disagreeing side is
    flagged for reconcile. Deterministic, alignment-free, high-signal."""
    if not terms:
        return set()
    window_seam = {unit.id: i for i, seam in enumerate(seams) for unit in seam.window}

    flagged: set[int] = set()
    for src_term, tgt_term in terms:
        if not src_term or not tgt_term:
            continue
        bearing = [u for u in source.units if src_term in u.source]
        if len(bearing) < 2:
            continue  # a single occurrence cannot be inconsistent
        rendered, missing = [], []
        for unit in bearing:
            target = translations.get(unit.id, "")
            (rendered if tgt_term.lower() in target.lower() else missing).append(unit.id)
        if rendered and missing:
            flagged.update(window_seam[uid] for uid in missing if uid in window_seam)
    return flagged


# --- end-of-run digest --------------------------------------------------------
_DIGEST_CAP = 6


def _group_lines(label: str, items: Sequence[str]) -> list[str]:
    if not items:
        return []
    shown = list(items[:_DIGEST_CAP])
    more = len(items) - len(shown)
    lines = [f"    {label} ({len(items)}):"]
    lines += [f"      - {item}" for item in shown]
    if more:
        lines.append(f"      +{more} more")
    return lines


def build_digest(audit: BookAudit, warnings: Sequence[Finding]) -> tuple[str, ...]:
    """A short, grouped, capped summary of what is actually worth a human's eye:
    cross-seam defects + the frontmatter / mixed-script / byte-equal warnings.
    Empty when nothing is actionable, so a clean book prints only its one-liner."""
    seam = [f"{f.kind.value} @ unit {f.index}: {f.detail}" for f in audit.at_seam()]
    by_code: dict[str, list[str]] = defaultdict(list)
    for f in warnings:
        where = f" ({f.unit_id})" if f.unit_id else ""
        by_code[f.code].append(f"{f.message}{where}")

    lines: list[str] = []
    lines += _group_lines("seams", seam)
    lines += _group_lines("frontmatter", by_code.get("frontmatter_cyrillic", []))
    lines += _group_lines("mixed-script", by_code.get("mixed_script", []))
    lines += _group_lines("wordplay/passthrough near seam", by_code.get("byte_equal", []))
    if not lines:
        return ()
    return (f"  diagnostics for {audit.book_key}:", *lines)
