# research-only: from-scratch block-grain gold builder (prose vs lineated).
"""Build a fresh, page-grounded gold for the LINEATION decision (prose vs lineated) —
NOT inherited from the old per-line anchors (those were labeled under the wrong ontology
and CSS). Verse is out of scope here: it has no docx signal and is a downstream editorial
pass; the structural gold is two classes only.

Three steps:
  frame    — enumerate every hard-bounded BODY run across all books on the faithful IR
             substrate (ir_view: <w:br> split correctly, real boundary skeleton). Score a
             cheap AMBIGUITY proxy and bucket each run into a stratum. Print the corpus
             distribution. Writes frame.jsonl.
  sample   — stratified deterministic draw (oversample `contested`), capped region size.
             Writes sample.json.
  package  — for each sampled region render the preview composite (docx page = truth,
             beside prose + lineated candidates, real site CSS) and a legible per-line
             structure listing with (idx,sub) keys for the reader panel. Writes
             reader_pkg.json + per-region structure text + composite PNGs.

The ambiguity proxy is for SAMPLING ONLY — it never labels truth. Truth comes from a
human/panel reading the docx page.

Run:
  uv run --with pillow python gold_build.py frame
  uv run --with pillow python gold_build.py sample --n 18 --seed 7
  uv run --with pillow python gold_build.py package
"""
from __future__ import annotations

import argparse
import json
import random
import re
import statistics as stat
import sys
from collections import Counter
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import astro_preview as ap  # noqa: E402
import features as feat  # noqa: E402
import ir_view as iv  # noqa: E402
from ir_view import LineKey  # noqa: E402

DATA_ROOT = Path(__file__).resolve().parents[1] / "data"


@dataclass(frozen=True)
class GoldPaths:
    """Every output path for one gold dataset, derived from its subdir name (pilot =
    gold_block; a scale run = gold_block2). Threaded explicitly through frame/sample/
    package so a scale run can never overwrite the pilot via a rebound module global."""

    root: Path

    @classmethod
    def for_dataset(cls, name: str) -> GoldPaths:
        root = DATA_ROOT / name
        (root / "png").mkdir(parents=True, exist_ok=True)
        return cls(root)

    @property
    def png(self) -> Path:
        return self.root / "png"

    @property
    def frame(self) -> Path:
        return self.root / "frame.jsonl"

    @property
    def sample(self) -> Path:
        return self.root / "sample.json"

    @property
    def reader_pkg(self) -> Path:
        return self.root / "reader_pkg.json"

    @property
    def reader_brief(self) -> Path:
        return self.root / "reader_brief.txt"


class Stratum(StrEnum):
    """Sampling stratum from the cheap ambiguity proxy (SAMPLING ONLY — never truth).
    `StrEnum` so it serializes into frame.jsonl as its lowercase string and iterates in
    a stable report order."""

    # Defined in report order (frame's per-stratum summary iterates the enum).
    WRAP_PROSE = "wrap_prose"  # lines genuinely wrap at the column → almost certainly prose
    HARDBREAK = "hardbreak"   # explicit <w:br> → almost certainly lineated
    MID_GAP = "mid_gap"     # short non-wrapping lines WITH a stanza gap → leans lineated
    MID_FLAT = "mid_flat"   # short non-wrapping lines, no gap → leans prose
    TINY = "tiny"           # < 2 lines — too small to label
    TOC = "toc"             # table-of-contents (dotted leaders / trailing page numbers) — skip


class RunFeats(NamedTuple):
    """Cheap per-run physics summary. Spread into a frame row via `._asdict()`, so its
    field names ARE the frame.jsonl column names."""

    n_body: int
    n_lines: int
    mean_fill: float
    wrap_frac: float
    short_frac: float
    has_gap: bool
    n_br: int


class Structure(NamedTuple):
    """The reader-facing per-line listing and the body lines to label. Every body line in
    the core is polled — no line is pre-labeled by rule. (`<w:br>` is not a reliable lineated
    signal: short stanzas are, but full-width-yet-non-wrapping prose lines are not. It stays
    a feature for the distilled model, never a gold shortcut.)"""

    listing: str
    polled: list[LineKey]


READER_BRIEF = """You adjudicate LINEATION for a spiritual book. The author pressed Enter
at the END OF EVERY LINE, so the raw line breaks do NOT reveal his intent — a flowing
prose paragraph and a deliberately lineated passage look identical in the source. Your job
is to recover, for each BODY line, which of TWO classes it belongs to:

  prose     — a flowing prose paragraph: sentences run on and would naturally WRAP; the
              break is just the author's Enter habit. Narrative and dialogue are prose even
              when the lines are short. This is the SAFE DEFAULT — when unsure, choose prose.
  lineated  — the break is INTENDED structure: verse, litany, invocation, prayer, a vow, an
              enumerated/parallel sequence, an aphoristic broken couplet — where joining the
              lines into a paragraph would DAMAGE how it reads.

(There is no third "verse" label here — verse is a later editorial styling layer. Decide
only prose vs lineated.)

EVIDENCE you are given per region:
  1. A composite IMAGE: the DOCX PAGE on the left (LibreOffice — the AUTHORITY for how the
     author's lines actually sit on the page: stanza gaps, short lines, indentation), and
     beside it the SAME text rendered two ways — as PROSE (lines joined into paragraphs)
     and as LINEATED (each line kept, stanza gaps). Look at which rendering reads TRUE.
  2. A per-line STRUCTURE listing: each body line keyed (idx.sub) with its text, whether it
     WRAPS at the real reading column, and any emphasis. Hard structural markers
     (heading / *** / image / blank / right-aligned / blockquote) are shown as separators —
     a run never crosses one; you never label them.

Decide PER LINE. A run can be all-prose, all-lineated, OR split (e.g. a prose lead-in then
a lineated stanza) — put the boundary where the reading actually changes. Output, for each
body (idx,sub): the label and a 0–1 confidence. Be conservative: over-lineating ordinary
prose is the costly error."""

MAX_REGION = 26       # cap body paras per region so the page/labeling stays manageable
MAX_TILES_PER_RUN = 6  # cap tiles drawn from one long run so a giant run can't explode the sample


def _book_map() -> dict[str, Path]:
    return {f"{n:02d}": p for n, p in feat.book_dirs()}


def _run_feats(paras: list[iv.Para], lo: int, hi: int) -> RunFeats:
    body = [paras[k] for k in range(lo, hi + 1) if paras[k].role == iv.ROLE_BODY]
    lines = [ln for p in body for ln in p.lines]
    fills = [ln.fill for ln in lines]
    wraps = [ln.wraps for ln in lines]
    has_gap = any(paras[k].role == iv.ROLE_EMPTY for k in range(lo, hi + 1))
    mean_fill = stat.fmean(fills) if fills else 0.0
    wrap_frac = (sum(wraps) / len(wraps)) if wraps else 0.0
    short_frac = (sum(f < 0.5 for f in fills) / len(fills)) if fills else 0.0
    return RunFeats(n_body=len(body), n_lines=len(lines), mean_fill=round(mean_fill, 3),
                    wrap_frac=round(wrap_frac, 3), short_frac=round(short_frac, 3),
                    has_gap=has_gap, n_br=sum(p.br_count for p in body))


def _stratum(f: RunFeats) -> Stratum:
    """Physics resolves only two corners; honest names for the rest.
      wrap_prose — lines genuinely WRAP at the column → almost certainly prose.
      hardbreak  — explicit <w:br> (shift+enter) → almost certainly lineated.
      mid_gap / mid_flat — short non-wrapping lines (the AMBIGUOUS MAJORITY, where prose
        and lineated look identical by physics). Split by stanza-gap presence: a blank
        inside the run is a real lineation cue, so mid_gap leans lineated, mid_flat leans
        prose (narrative/dialogue). NEITHER is decidable by physics — that's the point."""
    if f.n_lines < 2:
        return Stratum.TINY
    if f.n_br > 0:
        return Stratum.HARDBREAK
    if f.wrap_frac >= 0.5:
        return Stratum.WRAP_PROSE
    return Stratum.MID_GAP if f.has_gap else Stratum.MID_FLAT


# A table of contents frames as a body run of "Title … <page-number>" lines. Route it to a
# TOC stratum that sampling skips. Signal: a dotted leader, or a title ending in a bare page
# number and not starting with a digit (so numbered "1. …" lines are spared). Needs a
# majority of a ≥4-line run, so an incidental trailing number doesn't trip it.
_TOC_LEADER = re.compile(r"\.{3,}\s*\d{1,4}$")
# a title (has a letter) ending in a space-separated short integer = a "Title … <page>" entry.
# Catches "ПРЕДИСЛОВИЕ 5", "Глава 1 8", "1. Начало — Образ и Подобие 18". Prose/verse lines
# end in words or punctuation, not a bare page number, so they don't match.
_TOC_PAGE = re.compile(r"[^\W\d_].*\s\d{1,4}$")


def _toc_line(text: str) -> bool:
    t = text.strip()
    return bool(_TOC_LEADER.search(t) or _TOC_PAGE.search(t))


def _looks_toc(body_line_texts: list[str]) -> bool:
    if len(body_line_texts) < 4:
        return False
    return sum(_toc_line(t) for t in body_line_texts) / len(body_line_texts) >= 0.6


def frame(paths: GoldPaths) -> int:
    rows = []
    for book, docx in sorted(_book_map().items()):
        paras = iv.read_view(docx)
        for lo, hi in iv.segments(paras, soft_boundaries=False):
            f = _run_feats(paras, lo, hi)
            if f.n_lines == 0:
                continue
            body_lines = [ln.text for p in paras[lo:hi + 1] if p.role == iv.ROLE_BODY
                          for ln in p.lines]
            stratum = Stratum.TOC if _looks_toc(body_lines) else _stratum(f)
            txt = " ".join(p.text for p in paras[lo:hi + 1] if p.role == iv.ROLE_BODY)
            rows.append({"book": book, "lo": lo, "hi": hi, "stratum": stratum,
                         "sample": txt[:70], **f._asdict()})
    paths.frame.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n")
    c = Counter(r["stratum"] for r in rows)
    nbooks = len({r["book"] for r in rows})
    print(f"frame: {len(rows)} runs over {nbooks} books -> frame.jsonl")
    for s in Stratum:
        rs = [r for r in rows if r["stratum"] == s]
        if rs:
            mf = stat.fmean(r["mean_fill"] for r in rs)
            wf = stat.fmean(r["wrap_frac"] for r in rs)
            nl = stat.median(r["n_lines"] for r in rs)
            print(f"  {s:<15} {c[s]:>5}  mean_fill≈{mf:.2f} wrap_frac≈{wf:.2f} med_lines={nl}")
    return 0


def sample(paths: GoldPaths, n: int, seed: int) -> int:
    rows = [json.loads(line) for line in paths.frame.read_text().splitlines() if line]
    # Keep every run ≥2 body paras (TOC runs excluded); a run longer than MAX_REGION is
    # tiled into several regions at package time, not dropped. Log how many will tile.
    elig = [r for r in rows if r["n_body"] >= 2 and r["stratum"] != Stratum.TOC]
    long = [r for r in elig if r["n_body"] > MAX_REGION]
    lc = Counter(r["stratum"] for r in long)
    print(f"  {len(long)}/{len(elig)} runs exceed {MAX_REGION} body paras → TILED at package: {dict(lc)}")
    by: dict[str, list] = {}
    for r in elig:
        by.setdefault(r["stratum"], []).append(r)
    rng = random.Random(seed)
    # Weight the prose-risk bands where a model might OVER-lineate genuine prose — wrap_prose
    # (clear prose + any wrongly-broken wrapping line) and mid_flat (short narrative/dialogue)
    # — alongside the lineation-leaning mid_gap and a hardbreak slice.
    quota: dict[Stratum, float] = {
        Stratum.WRAP_PROSE: 0.30, Stratum.MID_FLAT: 0.30,
        Stratum.MID_GAP: 0.25, Stratum.HARDBREAK: 0.15,
    }
    picks = []
    for s, frac in quota.items():
        pool = by.get(s, [])
        rng.shuffle(pool)
        # spread across books: prefer one run per book before doubling up
        seen_books: set[str] = set()
        k = max(1, round(n * frac))
        chosen = []
        for r in pool:
            if len(chosen) >= k:
                break
            if r["book"] not in seen_books:
                chosen.append(r)
                seen_books.add(r["book"])
        for r in pool:  # backfill if a stratum is thin on distinct books
            if len(chosen) >= k:
                break
            if r not in chosen:
                chosen.append(r)
        picks.extend(chosen)
    rng.shuffle(picks)
    for i, r in enumerate(picks):
        r["rid"] = f"g{i:02d}_b{r['book']}"
    paths.sample.write_text(json.dumps(picks, ensure_ascii=False, indent=1))
    print(f"sample: {len(picks)} regions, {len({r['book'] for r in picks})} books")
    print("  strata:", dict(Counter(r["stratum"] for r in picks)))
    return 0


_HARD_CTX = {
    iv.ROLE_HEADING: "heading", iv.ROLE_THEMATIC: "***", iv.ROLE_IMAGE: "image",
    iv.ROLE_EMPTY: "blank", iv.ROLE_TABLE: "table", iv.ROLE_LIST: "list",
    iv.ROLE_SIGNATURE: "right-aligned", iv.ROLE_EPIGRAPH: "right-aligned",
    iv.ROLE_BLOCKQUOTE: "blockquote", iv.ROLE_OTHER: "other-block",
    # inferred soft roles collapsed to a neutral marker (don't leak the harness guess)
    iv.ROLE_PSEUDO_HEADER: "bold-line", iv.ROLE_SPEAKER_LABEL: "bold-line",
}


def _structure(region: list[iv.Para], core_lo: int, core_hi: int) -> Structure:
    """Per-line listing for the reader + the body lines to label. Only body paragraphs inside
    [core_lo, core_hi] are keyed; paragraphs outside the core (the ±1 pad, and a neighbouring
    tile's lines) show as `(context)` and are never keyed, so tiles partition a long run with
    no duplicate keys. `ln.md` carries inline emphasis so the reader sees partial bold/italic."""
    out: list[str] = []
    polled: list[LineKey] = []
    for p in region:
        if p.role == iv.ROLE_BODY:
            keyed = core_lo <= p.index <= core_hi
            for li, ln in enumerate(p.lines):
                w = "WRAPS " if ln.wraps else "nowrap"
                ctx = "" if keyed else "   (context, not voted)"
                out.append(f"  {p.index}.{li}  {w} | {ln.md or ln.text}{ctx}")
                if keyed:
                    polled.append(LineKey(p.index, li))
        elif p.role in (iv.ROLE_LIST, iv.ROLE_TABLE) and p.lines:
            out.append(f"  ---- [{_HARD_CTX.get(p.role, 'block')}]")    # list/table text as context
            for ln in p.lines:
                out.append(f"          {ln.md or ln.text}")
        else:
            mark = _HARD_CTX.get(p.role, "block")
            t = (" " + p.text[:80]) if p.text else ""
            out.append(f"  ---- [{mark}]{t}")
    return Structure("\n".join(out), polled)


def _tiles(body_pos: list[int], lo: int, hi: int) -> list[tuple[int, int]]:
    """Split a run's body-paragraph indices into adjacent, non-overlapping tiles of at most
    MAX_REGION body paras, so a long run is covered by several labelable regions rather than
    one window. A giant run is capped to MAX_TILES_PER_RUN tiles spread evenly across it, so
    one huge run can't explode the sample into hundreds of regions. Each tile's ir-range is
    [first body, last body] of its chunk."""
    if len(body_pos) <= MAX_REGION:
        return [(lo, hi)]
    chunks = [body_pos[j:j + MAX_REGION] for j in range(0, len(body_pos), MAX_REGION)]
    if len(chunks) > MAX_TILES_PER_RUN:
        step = len(chunks) / MAX_TILES_PER_RUN
        chunks = [chunks[int(i * step)] for i in range(MAX_TILES_PER_RUN)]
    return [(c[0], c[-1]) for c in chunks]


def _package_region(paths: GoldPaths, book: str, paras: list[iv.Para], tlo: int, thi: int,
                    rid: str, stratum: str, tiled: bool, force: bool) -> dict:
    """Package one region (= a whole short run, or one tile of a long run). Keys only the
    core [tlo, thi] body lines; renders a ±1-padded region for context. Cached by composite
    unless `force` (after a renderer change the old screenshots are stale and must refresh)."""
    from PIL import Image
    comp_path = paths.png / f"{rid}_compare.png"
    idxs = [p.index for p in paras]
    plo, phi = max(min(idxs), tlo - 1), min(max(idxs), thi + 1)
    region = ap._region(paras, plo, phi)
    src_lo, src_hi = ap._src_span(region)
    structure = _structure(region, tlo, thi)
    entry = {"rid": rid, "book": book, "stratum": stratum, "ir_lo": tlo, "ir_hi": thi,
             "tiled": tiled, "composite": str(comp_path), "structure": structure.listing,
             "keys": [list(k) for k in structure.polled], "n_keys": len(structure.polled)}
    if comp_path.exists() and not force:   # resumable: don't re-render
        return entry
    docx_pages = [p for p in ap._render_docx(book, src_lo, src_hi, rid, paths.png) if p.exists()]
    if not docx_pages:
        raise RuntimeError(f"render-slice produced no PNG for src[{src_lo}:{src_hi}]")
    panels = [ap._label(ap._vstack(docx_pages), f"DOCX #{book} src[{src_lo}:{src_hi}]")]
    for name in ("prose", "lineated"):
        cls: ap.RenderClass = name
        tm: dict[int, ap.RenderClass] = {p.index: cls for p in region if p.role == iv.ROLE_BODY}
        png = ap._render_html(name, ap._body_html(region, tm), 680, rid, name, paths.png)
        panels.append(ap._label(Image.open(png).convert("RGB"), name.upper()))
    ap._hcat(panels).save(comp_path)
    return entry


def package(paths: GoldPaths, force: bool = False) -> int:
    picks = json.loads(paths.sample.read_text())
    bm = _book_map()
    pkg: list[dict] = []
    failed: list[dict] = []
    for r in picks:
        book = r["book"]
        paras = iv.read_view(bm[book])   # a book that won't load is a real bug → fails loudly
        body_pos = [p.index for p in paras if r["lo"] <= p.index <= r["hi"] and p.role == iv.ROLE_BODY]
        tiles = _tiles(body_pos, r["lo"], r["hi"])
        for j, (tlo, thi) in enumerate(tiles):
            rid = r["rid"] if len(tiles) == 1 else f"{r['rid']}_t{j}"
            cached = (paths.png / f"{rid}_compare.png").exists() and not force
            try:
                entry = _package_region(paths, book, paras, tlo, thi, rid, r["stratum"],
                                        len(tiles) > 1, force)
                pkg.append(entry)
                print(f"  {rid}: {r['stratum']:<10} ir[{tlo}..{thi}] {entry['n_keys']} polled"
                      + ("  (cached)" if cached else ""))
            except (ap.RegionUnlocatable, RuntimeError, OSError) as e:
                # Skip a region that can't be located/rendered (e.g. stale indices after a
                # book's DOCX was replaced); keep the batch going. A genuine bug (KeyError,
                # etc.) is NOT caught here — it propagates and fails loudly.
                failed.append({"rid": rid, "book": book, "err": str(e)[:160]})
                print(f"  {rid}: SKIPPED — {str(e)[:120]}")
    ap._purge_lo_locks()
    if failed:
        print(f"  ({len(failed)} skipped: {[f['rid'] for f in failed]})")
    paths.reader_pkg.write_text(json.dumps(pkg, ensure_ascii=False, indent=1))
    paths.reader_brief.write_text(READER_BRIEF)
    print(f"packaged {len(pkg)} regions -> reader_pkg.json (+ reader_brief.txt)")
    return 0


if __name__ == "__main__":
    apx = argparse.ArgumentParser()
    apx.add_argument("--set", dest="dataset", required=True,
                     help="data subdir, REQUIRED so a run can't default into the pilot "
                          "(pilot=gold_block; scale=gold_block2)")
    sub = apx.add_subparsers(dest="cmd", required=True)
    sub.add_parser("frame")
    sp = sub.add_parser("sample")
    sp.add_argument("--n", type=int, default=40)
    sp.add_argument("--seed", type=int, default=7)
    pk = sub.add_parser("package")
    pk.add_argument("--force", action="store_true", help="re-render even if a composite is cached")
    args = apx.parse_args()
    # Build the dataset paths from --set and thread them in, so a scale run can never
    # overwrite the pilot and the preview helper's output dir is passed, not mutated.
    paths = GoldPaths.for_dataset(args.dataset)
    match args.cmd:
        case "frame":
            raise SystemExit(frame(paths))
        case "sample":
            raise SystemExit(sample(paths, args.n, args.seed))
        case "package":
            raise SystemExit(package(paths, args.force))
