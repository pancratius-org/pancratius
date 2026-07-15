"""Corpus-wide population scan for the SUSTAINED bare-run false-fold experiment.

Isolates the flips caused SPECIFICALLY by the bare-run clause by recomputing the
importer's lineation decisions twice per book: once with the clause live
(_BARE_RUN_MIN_LINES = 6) and once with it disabled (_BARE_RUN_MIN_LINES = inf).
The set difference (lineated-on minus lineated-off) is exactly the rule's added
folds. Each such ordinal whose pre-clause decision was prose is a det=prose ->
lineated FLIP. Maximal consecutive-ordinal blocks become RUNS.

Cross-validated against the committed recon snapshot (store.load_recon_rows,
PRE-FIX det): on every tested book the toggle flips == recon-prose flips exactly,
so the toggle method is the authoritative, fully-reproducible definition.

Writes population.json in the experiment dir. Slow: runs the importer 2x/book.
"""

from __future__ import annotations

import json
import math
import statistics
import time
from pathlib import Path

import pancratius.passes.lineation as L
from pancratius import docx_inspect as di
from lineation_core import paths, store

EXP_DIR = Path(__file__).resolve().parent.parent


def lineated_pct_lookup() -> dict[tuple[str, str], float]:
    """(book_id, lang) -> pre-fix lineated_pct from the recon-postmerge scorecard."""
    sc = EXP_DIR.parent / "2026-06-14-recon-postmerge" / "scorecard.json"
    data = json.loads(sc.read_text())
    out: dict[tuple[str, str], float] = {}
    for b in data["books"]:
        out[(str(b["book_id"]), b["lang"])] = b["lineated_pct"]
    return out


def decisions_with_clause(docx: Path, *, live: bool) -> dict[int, bool]:
    L._BARE_RUN_MIN_LINES = 6 if live else 10**9
    try:
        return di.lineation_decisions(docx)
    finally:
        L._BARE_RUN_MIN_LINES = 6


def group_runs(flipped: set[int], ordinals: list[int]) -> list[list[int]]:
    """Maximal blocks of flipped ordinals that are consecutive in the book's real
    ordinal sequence (non-flipped real ordinals between them break the run)."""
    runs: list[list[int]] = []
    cur: list[int] = []
    for o in ordinals:
        if o in flipped:
            cur.append(o)
        else:
            if cur:
                runs.append(cur)
            cur = []
    if cur:
        runs.append(cur)
    return runs


def main() -> None:
    pct = lineated_pct_lookup()
    population: list[dict] = []
    book_summ: list[dict] = []
    recon_mismatch: list[dict] = []
    t0 = time.time()
    for lang in ("ru", "en"):
        for b in paths.corpus_books(lang):
            docx = paths.book_docx(b, lang)
            dec_on = decisions_with_clause(docx, live=True)
            dec_off = decisions_with_clause(docx, live=False)
            # flip = clause turned this ordinal lineated AND it was prose without it
            flipped = {
                o for o, v in dec_on.items() if v and not dec_off.get(o, False)
            }
            prows = {p.index: p for p in di.read_rows(docx)}
            # text length per ordinal (display line; bare-run rows are br_count==0)
            ordinals = sorted(prows)
            runs = group_runs(flipped, ordinals)

            # cross-check vs committed recon snapshot
            try:
                rrows = store.load_recon_rows(b, lang)
                pre = {r["id"][2]: r["det"] for r in rrows}
                recon_flips = {
                    o for o, v in dec_on.items() if v and pre.get(o) == "prose"
                }
                if recon_flips != flipped:
                    recon_mismatch.append(
                        {
                            "book": b,
                            "lang": lang,
                            "toggle_only": sorted(flipped - recon_flips)[:20],
                            "recon_only": sorted(recon_flips - flipped)[:20],
                            "n_toggle": len(flipped),
                            "n_recon": len(recon_flips),
                        }
                    )
            except Exception as e:  # noqa: BLE001
                recon_mismatch.append({"book": b, "lang": lang, "error": str(e)})

            for run in runs:
                lengths = [len(prows[o].text) for o in run]
                population.append(
                    {
                        "book": b,
                        "lang": lang,
                        "ord_lo": run[0],
                        "ord_hi": run[-1],
                        "n_lines": len(run),
                        "mean_len": round(statistics.fmean(lengths), 2),
                        "max_len": max(lengths),
                        "min_len": min(lengths),
                        "book_lineated_pct": pct.get((b, lang)),
                        "ords": run,
                    }
                )
            book_summ.append(
                {
                    "book": b,
                    "lang": lang,
                    "n_flip_ords": len(flipped),
                    "n_runs": len(runs),
                    "book_lineated_pct": pct.get((b, lang)),
                }
            )
            print(
                f"{lang} {b}: flips={len(flipped):5d} runs={len(runs):4d}  "
                f"({time.time()-t0:.0f}s)",
                flush=True,
            )

    total_lines = sum(r["n_lines"] for r in population)
    out = {
        "method": "toggle _BARE_RUN_MIN_LINES 6<->inf; flip = lineated-on & prose-off",
        "n_runs": len(population),
        "n_lines": total_lines,
        "recon_crosscheck_mismatches": recon_mismatch,
        "books": book_summ,
        "runs": population,
    }
    (EXP_DIR / "population.json").write_text(json.dumps(out, indent=2, ensure_ascii=False))
    print(
        f"\nTOTAL runs={len(population)} lines={total_lines} "
        f"recon_mismatches={len(recon_mismatch)} ({time.time()-t0:.0f}s)"
    )


if __name__ == "__main__":
    main()
