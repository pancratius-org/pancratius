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
from functools import lru_cache
from pathlib import Path

from pancratius.lineation_overrides import overrides_path, paragraph_sha

from . import paths
from .annotations import LabelSource, load_labels
from .identity import BookId, Label

_EXPORTABLE_SOURCES = frozenset({LabelSource.HUMAN, LabelSource.OVERRIDE})


@lru_cache(maxsize=None)
def _baseline_decisions(lang: str, book_id: BookId) -> dict[int, bool]:
    """The importer's own verdict, sidecar IGNORED — the diff baseline."""
    from pancratius.docx_inspect import lineation_decisions

    return lineation_decisions(paths.book_docx(book_id, lang), apply_overrides=False)


@lru_cache(maxsize=None)
def _row_texts(lang: str, book_id: BookId) -> dict[int, str]:
    from pancratius.docx_inspect import read_rows

    return {r.index: r.text for r in read_rows(paths.book_docx(book_id, lang))}


@dataclass(frozen=True)
class ExportReport:
    written: dict[Path, int]        # sidecar path → entries written
    deleted: tuple[Path, ...]       # sidecars removed (their corrections are gone)
    n_prose_corrections: int
    n_lineated_pending: int         # contradictions the importer cannot apply yet
    n_holdout_withheld: int         # eval-only truth, exportable only post-E4
    n_conflicting_ordinals: int     # sub-lines of one ordinal disagree — skipped, surfaced


def contradictions() -> tuple[dict[tuple[str, BookId], dict[int, Label]], int, int]:
    """Exportable truth vs the sidecar-free importer baseline, reduced per ordinal. Returns the
    per-(lang, book) contradiction map, the count of ordinals whose sub-line labels conflict
    (skipped — one `w:p` has one register; conflicting truth needs re-adjudication), and the
    count of holdout-withheld contradictions."""
    by_ordinal: dict[tuple[str, BookId, int], set[Label]] = defaultdict(set)
    holdout_ordinals: set[tuple[str, BookId, int]] = set()
    for g in load_labels().labels:
        if g.source not in _EXPORTABLE_SOURCES:
            continue
        key = (g.id.lang, g.id.book_id, g.id.src_ordinal)
        if g.holdout:
            holdout_ordinals.add(key)
            continue
        by_ordinal[key].add(g.label)

    out: dict[tuple[str, BookId], dict[int, Label]] = defaultdict(dict)
    conflicts = 0
    n_holdout = 0
    for (lang, book_id, ordinal), truths in sorted(by_ordinal.items()):
        if len(truths) > 1:
            conflicts += 1
            continue
        (truth,) = truths
        hit = _baseline_decisions(lang, book_id).get(ordinal)
        if hit is None:
            continue                     # uncovered: a ledger entry, not a contradiction
        if ("lineated" if hit else "prose") != truth:
            out[(lang, book_id)][ordinal] = truth
    for lang, book_id, ordinal in sorted(holdout_ordinals):
        hit = _baseline_decisions(lang, book_id).get(ordinal)
        if hit is not None:              # count only what WOULD export, so the report is real
            n_holdout += 1
    return dict(out), conflicts, n_holdout


def export() -> ExportReport:
    """Rewrite the sidecars as the total projection of the exportable truth: one file per
    (book, lang) with prose-direction corrections; an existing sidecar whose corrections are
    gone is DELETED, never left stale."""
    contra, conflicts, n_holdout = contradictions()
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
                "text_sha": paragraph_sha(_row_texts(lang, book_id)[ordinal]),
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
                        n_holdout_withheld=n_holdout, n_conflicting_ordinals=conflicts)


if __name__ == "__main__":
    report = export()
    for path, n in sorted(report.written.items()):
        print(f"wrote {n} correction(s) → {path.relative_to(paths.REPO_ROOT)}")
    for path in report.deleted:
        print(f"deleted stale sidecar {path.relative_to(paths.REPO_ROOT)}")
    print(f"prose corrections: {report.n_prose_corrections}; "
          f"lineated pending (unappliable today): {report.n_lineated_pending}; "
          f"holdout withheld (post-E4): {report.n_holdout_withheld}; "
          f"conflicting ordinals skipped: {report.n_conflicting_ordinals}")
