# research-pure EXCEPT the one deliberate boundary crossing: writes production sidecars.
"""Project the adjudicated truth into the production correction sidecars.

A correction is a human/override label that CONTRADICTS the importer's live verdict for its
ordinal. The truth stays single-store (`labels.jsonl`); the sidecar
(`src/content/books/<book>/lineation.<lang>.json`) is its committed projection the importer
honors — labels and sidecars move together, like docx and md.

Only the `prose` direction is exported: the importer can suppress false lineation today but
cannot force lineation yet, and an unappliable sidecar entry would fail every import of that
book. The `lineated`-direction contradictions are REPORTED (they are E3's main discovery class,
and converter-rule RCA comes before correction labels for them anyway).

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
from .annotations import LabelSource, LineLabel, load_labels
from .identity import BookId, Label

_EXPORTABLE_SOURCES = frozenset({LabelSource.HUMAN, LabelSource.OVERRIDE})


@lru_cache(maxsize=None)
def _decisions(lang: str, book_id: BookId) -> dict[int, bool]:
    from pancratius.docx_inspect import lineation_decisions

    return lineation_decisions(paths.book_docx(book_id, lang))


@lru_cache(maxsize=None)
def _row_texts(lang: str, book_id: BookId) -> dict[int, str]:
    from pancratius.docx_inspect import read_rows

    return {r.index: r.text for r in read_rows(paths.book_docx(book_id, lang))}


@dataclass(frozen=True)
class ExportReport:
    written: dict[Path, int]        # sidecar path → entries written
    n_prose_corrections: int
    n_lineated_pending: int         # contradictions the importer cannot apply yet
    n_conflicting_ordinals: int     # sub-lines of one ordinal disagree — skipped, surfaced


def contradictions() -> tuple[dict[tuple[str, BookId], dict[int, Label]], int]:
    """Human/override truth vs the live importer verdict, reduced per ordinal. Returns the
    per-(lang, book) contradiction map and the count of ordinals whose sub-line labels
    conflict (skipped — one `w:p` has one register; conflicting truth needs re-adjudication)."""
    by_ordinal: dict[tuple[str, BookId, int], set[Label]] = defaultdict(set)
    for g in load_labels().labels:
        if g.source not in _EXPORTABLE_SOURCES:
            continue
        by_ordinal[(g.id.lang, g.id.book_id, g.id.src_ordinal)].add(g.label)

    out: dict[tuple[str, BookId], dict[int, Label]] = defaultdict(dict)
    conflicts = 0
    for (lang, book_id, ordinal), truths in sorted(by_ordinal.items()):
        if len(truths) > 1:
            conflicts += 1
            continue
        (truth,) = truths
        hit = _decisions(lang, book_id).get(ordinal)
        if hit is None:
            continue                     # uncovered: a ledger entry, not a contradiction
        if ("lineated" if hit else "prose") != truth:
            out[(lang, book_id)][ordinal] = truth
    return dict(out), conflicts


def export() -> ExportReport:
    """Write one sidecar per (book, lang) with prose-direction corrections (merging over any
    existing sidecar is deliberate non-behavior: the truth store is the source, the sidecar a
    full projection — every run rewrites it whole)."""
    contra, conflicts = contradictions()
    written: dict[Path, int] = {}
    n_prose = n_lineated = 0
    for (lang, book_id), per_ordinal in sorted(contra.items()):
        entries = {}
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
            path = overrides_path(paths.book_docx(book_id, lang))
            path.write_text(json.dumps(entries, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")
            written[path] = len(entries)
    return ExportReport(written=written, n_prose_corrections=n_prose,
                        n_lineated_pending=n_lineated, n_conflicting_ordinals=conflicts)


if __name__ == "__main__":
    report = export()
    for path, n in sorted(report.written.items()):
        print(f"wrote {n} correction(s) → {path.relative_to(paths.REPO_ROOT)}")
    print(f"prose corrections: {report.n_prose_corrections}; "
          f"lineated pending (unappliable today): {report.n_lineated_pending}; "
          f"conflicting ordinals skipped: {report.n_conflicting_ordinals}")
