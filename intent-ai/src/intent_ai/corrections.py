# research-pure EXCEPT the one deliberate boundary crossing: writes production sidecars.
"""Project the adjudicated truth into the production correction sidecars.

A correction is a non-holdout human/override label that CONTRADICTS the importer's OWN verdict
(the sidecar-free baseline — diffing against the corrected verdict would erase the projection's
own domain). The truth stays single-store (`labels.jsonl`); the sidecar
(`src/content/books/<book>/lineation.<lang>.json`) is its committed TOTAL projection: every run
rewrites every in-scope sidecar, deleting one whose corrections are gone, so a re-adjudicated or
retracted label propagates and the export is idempotent.

Two classes are withheld, both surfaced in the report:
  - `holdout` labels — eval-only truth; exporting one would patch the system with an eval item's
    own answer and make that eval circular. A holdout correction becomes exportable only after
    the eval that froze it is scored (E4 is score-once) or retired.
  - the `lineated` direction — the importer can suppress false lineation but cannot force
    lineation yet; an unappliable entry would fail every import of that book. These are E3's
    main discovery class, and converter-rule RCA comes before correction labels there anyway.

This is the ONE place research truth crosses into production content; everything else in this
package reads production sources strictly read-only."""
from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from pancratius import docx_source, docx_structure
from pancratius.lineation_overrides import overrides_path
from pancratius.locales import Locale

from . import paths, producer
from .annotations import LabelSource, load_labels
from .identity import BookId, Label
from .truth import (
    IncompleteParagraphTruth,
    JoinedTruth,
    MixedParagraphTruth,
    UnanimousParagraphTruth,
    join_truth,
    reduce_paragraph_truth,
)

_EXPORTABLE_SOURCES = frozenset({LabelSource.HUMAN, LabelSource.OVERRIDE})


@cache
def _source(lang: Locale, book_id: BookId) -> docx_source.DocxSourceDocument:
    return docx_source.read(paths.book_docx(book_id, lang))


@cache
def _lineation_fingerprint(
    lang: Locale,
    book_id: BookId,
    ordinal: int,
) -> docx_source.LineationFingerprint:
    paragraph = _source(lang, book_id).paragraph(docx_source.ParagraphOrdinal(ordinal))
    fingerprint = paragraph.adjudication_fingerprint(docx_source.SourceAdjudicationKind.LINEATION)
    assert isinstance(fingerprint, docx_source.LineationFingerprint)
    return fingerprint

def _joined_truth() -> JoinedTruth:
    """Bind the complete active truth before any production projection."""
    labelset = load_labels()
    book_keys = {label.id.book_key for label in labelset.labels}
    records = {
        key: producer.read_lines(paths.book_docx(key.book_id, key.lang), key.lang, key.book_id)
        for key in book_keys
    }
    return join_truth(records, labelset)


@cache
def _baseline_decisions(lang: Locale, book_id: BookId) -> dict[int, bool]:
    """The importer's own verdict, sidecar IGNORED — the diff baseline."""
    return docx_structure.fold_decisions(
        _source(lang, book_id),
        lang=lang,
        apply_overrides=False,
    )



@cache
def _line_counts(lang: Locale, book_id: BookId) -> dict[int, int]:
    return {
        int(paragraph.ordinal): sum(bool(line) for line in paragraph.content.line_segments)
        for paragraph in _source(lang, book_id).paragraphs
    }


@dataclass(frozen=True)
class ExportReport:
    written: dict[Path, int]        # sidecar path → entries written
    deleted: tuple[Path, ...]       # sidecars removed (their corrections are gone)
    n_prose_corrections: int
    n_lineated_pending: int         # contradictions the importer cannot apply yet
    n_holdout_withheld: int         # eval-only truth, exportable only post-E4
    n_conflicting_ordinals: int     # sub-lines of one ordinal disagree — skipped, surfaced
    n_incomplete_ordinals: int      # not every sibling line has truth — unsafe to lower
    n_uncovered_truth: int          # exportable truth absent from importer coverage


def contradictions() -> tuple[
    dict[tuple[Locale, BookId], dict[int, Label]], int, int, int, int
]:
    """Exportable truth vs the sidecar-free importer baseline, reduced per ordinal. Returns the
    per-(lang, book) contradiction map, the count of ordinals whose sub-line labels conflict
    (skipped — one `w:p` has one register; conflicting truth needs re-adjudication), and the
    count of holdout-withheld contradictions."""
    by_ordinal: dict[tuple[Locale, BookId, int], dict[int, Label]] = defaultdict(dict)
    holdout: dict[tuple[Locale, BookId, int], dict[int, Label]] = defaultdict(dict)
    for binding in _joined_truth().entries:
        g = binding.truth
        if g.source not in _EXPORTABLE_SOURCES:
            continue
        key = (g.id.lang, g.id.book_id, g.id.src_ordinal)
        if g.holdout:
            holdout[key][g.id.sub] = g.label
            continue
        by_ordinal[key][g.id.sub] = g.label

    out: dict[tuple[Locale, BookId], dict[int, Label]] = defaultdict(dict)
    conflicts = 0
    n_holdout = 0
    uncovered = 0
    incomplete = 0
    for (lang, book_id, ordinal), by_sub in sorted(by_ordinal.items()):
        expected = _line_counts(lang, book_id).get(ordinal, 0)
        if expected <= 0:
            uncovered += 1
            continue
        match reduce_paragraph_truth(by_sub, expected_subs=expected):
            case IncompleteParagraphTruth():
                incomplete += 1
                continue
            case MixedParagraphTruth():
                conflicts += 1
                continue
            case UnanimousParagraphTruth(label=truth):
                pass
        hit = _baseline_decisions(lang, book_id).get(ordinal)
        if hit is None:
            uncovered += 1
            continue
        if ("lineated" if hit else "prose") != truth:
            out[(lang, book_id)][ordinal] = truth
    for (lang, book_id, ordinal), by_sub in sorted(holdout.items()):
        expected = _line_counts(lang, book_id).get(ordinal, 0)
        if expected <= 0:
            continue
        reduced = reduce_paragraph_truth(by_sub, expected_subs=expected)
        if not isinstance(reduced, UnanimousParagraphTruth):
            continue
        truth = reduced.label
        hit = _baseline_decisions(lang, book_id).get(ordinal)
        if hit is not None and ("lineated" if hit else "prose") != truth:
            n_holdout += 1
    return dict(out), conflicts, incomplete, n_holdout, uncovered


def export() -> ExportReport:
    """Rewrite the sidecars as the total projection of the exportable truth: one file per
    (book, lang) with prose-direction corrections; an existing sidecar whose corrections are
    gone is DELETED, never left stale."""
    contra, conflicts, incomplete, n_holdout, uncovered = contradictions()
    desired: dict[Path, dict[str, dict[str, str]]] = {}
    n_prose = n_lineated = 0
    for (lang, book_id), per_ordinal in sorted(contra.items()):
        entries: dict[str, dict[str, str]] = {}
        for ordinal, truth in sorted(per_ordinal.items()):
            if truth == "lineated":
                n_lineated += 1
                continue
            n_prose += 1
            entries[str(ordinal)] = {
                "register": truth,
                "lineation_fingerprint": _lineation_fingerprint(lang, book_id, ordinal).value,
            }
        if entries:
            desired[overrides_path(paths.book_docx(book_id, lang))] = entries

    written: dict[Path, int] = {}
    for path, entries in desired.items():
        path.write_text(json.dumps(entries, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
        written[path] = len(entries)
    stale = [p for p in paths.BOOKS.glob("*/lineation.*.json") if p not in desired]
    for p in stale:
        p.unlink()
    return ExportReport(written=written, deleted=tuple(sorted(stale)),
                        n_prose_corrections=n_prose, n_lineated_pending=n_lineated,
                        n_holdout_withheld=n_holdout, n_conflicting_ordinals=conflicts,
                        n_incomplete_ordinals=incomplete,
                        n_uncovered_truth=uncovered)


if __name__ == "__main__":
    report = export()
    for path, n in sorted(report.written.items()):
        print(f"wrote {n} correction(s) → {path.relative_to(paths.REPO_ROOT)}")
    for path in report.deleted:
        print(f"deleted stale sidecar {path.relative_to(paths.REPO_ROOT)}")
    print(f"prose corrections: {report.n_prose_corrections}; "
          f"lineated pending (unappliable today): {report.n_lineated_pending}; "
          f"holdout withheld (post-E4): {report.n_holdout_withheld}; "
          f"conflicting ordinals skipped: {report.n_conflicting_ordinals}; "
          f"incomplete ordinals skipped: {report.n_incomplete_ordinals}; "
          f"uncovered truth: {report.n_uncovered_truth}")
