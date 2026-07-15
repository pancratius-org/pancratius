"""Render each sampled flip-run (canonical LibreOffice render, dpi=150) with a
margin of one ordinal on each side, into /tmp/bare-run-renders/. Prints a TSV
manifest (idx, lang, book, ord_lo, ord_hi, n_lines, stratum, png paths) the rater
walks to classify each run by reading the PNG(s).
"""

from __future__ import annotations

import json
from pathlib import Path

from lineation_core import paths
from lineation_core.teacher.render import libreoffice_pages

EXP_DIR = Path(__file__).resolve().parent.parent
OUT = Path("/tmp/bare-run-renders")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    sample = json.loads((EXP_DIR / "sample.json").read_text())["sample"]
    render = libreoffice_pages(dpi=150)
    manifest = []
    for i, r in enumerate(sample):
        b, lang = r["book"], r["lang"]
        lo, hi = r["ord_lo"] - 1, r["ord_hi"] + 1
        stem = OUT / f"r{i:02d}_{lang}_{b}_{r['ord_lo']}-{r['ord_hi']}.png"
        pngs = render(paths.book_docx(b, lang), lo, hi, stem)
        manifest.append(
            {
                "idx": i,
                "lang": lang,
                "book": b,
                "ord_lo": r["ord_lo"],
                "ord_hi": r["ord_hi"],
                "n_lines": r["n_lines"],
                "mean_len": r["mean_len"],
                "max_len": r["max_len"],
                "stratum": r["stratum"],
                "pngs": [str(p) for p in pngs],
            }
        )
        print(f"r{i:02d} {lang} {b} {r['ord_lo']}-{r['ord_hi']} n={r['n_lines']} "
              f"strat={r['stratum']} -> {[p.name for p in pngs]}", flush=True)
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
