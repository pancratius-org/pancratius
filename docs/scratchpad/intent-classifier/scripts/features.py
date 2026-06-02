# research-pure: reads src/content DOCX read-only; writes only to the scratch dir.
"""Per-paragraph feature extraction over the book corpus.

Features are grouped by epistemic status (the brief's KNOW / layout-physics /
incidental-noise split):

  PHYSICS (trust)        — wrapping at the real reading column (`wrap.py`).
  DELIBERATE (trust)     — empty-paragraph breaks, headings, `***`, right-align,
                           hard `<w:br/>`, numbered lists. The author meant these.
  CONTENT (discriminate) — length, punctuation, interrogative, dialogue dash,
                           anaphora/parallelism with neighbours (the ambiguous-
                           middle discriminators among short non-wrapping lines).
  NOISE / NEG-CONTROL    — spacing before/after, contextualSpacing, first-line
                           indent, justification, the heuristic's `lineation_group`.
                           Carried ONLY to prove (later) they don't help / hurt.

One row per body paragraph, in document order, with neighbour-derived run context.
Output: `data/features.jsonl` (one JSON object per line) + a per-book manifest.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Literal, NamedTuple, NotRequired, TypedDict

type Source = Literal["book", "poetry"]

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

from pancratius import docx_inspect as di  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import wrap as wrapmod  # noqa: E402

CONTENT = ROOT / "src" / "content"
OUT = Path(__file__).resolve().parents[1] / "data"

_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)
_TERMINAL = ".!?…"


class FeatureRow(TypedDict):
    """One body-paragraph feature record (one JSON object per line in features.jsonl).
    The shape is genuinely dict-flavored — it IS the on-disk serialization — so a
    TypedDict documents the fields without touching the bytes. Fields are grouped by
    the brief's epistemic status (physics / deliberate / content / noise). The
    neighbour- and run-context fields are filled in a second in-place pass, hence
    NotRequired during construction."""

    source: Source
    key: str
    idx: int
    text: str
    # PHYSICS
    fill: float
    wrap_lines: int
    wraps: bool
    char_len: int
    word_count: int
    # DELIBERATE (author-intended structure)
    empty: bool
    br_count: int
    heading: bool
    thematic: bool
    align_right: bool
    numbered: bool
    # CONTENT discriminators
    ends_terminal: bool
    ends_colon: bool
    is_question: bool
    starts_dash: bool
    starts_upper: bool
    first_token: str
    # NOISE / negative controls (incidental styling)
    nc_sp_after: str
    nc_sp_before: str
    nc_contextual: bool
    nc_first_indent: bool
    nc_jc: str
    nc_lineation_group: object
    nc_style: str
    # neighbour + run context (second pass; in-place enriched)
    prev_empty: NotRequired[bool]
    next_empty: NotRequired[bool]
    after_heading: NotRequired[bool]
    after_thematic: NotRequired[bool]
    anaphora_prev: NotRequired[bool]
    anaphora_next: NotRequired[bool]
    run_len: NotRequired[int]
    run_pos: NotRequired[int]


class ManifestEntry(TypedDict):
    source: Source
    key: str
    paragraphs: int


class Target(NamedTuple):
    """One extraction target: a corpus source, its key (book number or poem slug),
    and the DOCX path."""

    source: Source
    key: str
    docx: Path


def _first_token(text: str) -> str:
    m = _WORD.search(text.lower())
    return m.group(0) if m else ""


def _tokens(text: str) -> list[str]:
    return _WORD.findall(text.lower())


def book_dirs() -> list[tuple[int, Path]]:
    out: list[tuple[int, Path]] = []
    for d in sorted((CONTENT / "books").glob("[0-9]*-*")):
        m = re.match(r"(\d+)-", d.name)
        if m and (d / "ru.docx").is_file():
            out.append((int(m.group(1)), d / "ru.docx"))
    return out


def poetry_dirs() -> list[tuple[str, Path]]:
    out: list[tuple[str, Path]] = []
    for d in sorted((CONTENT / "poetry").glob("[0-9]*-*")):
        if (d / "ru.docx").is_file():
            out.append((d.name, d / "ru.docx"))
    return out


def extract(docx: Path, source: Source, key: str) -> list[FeatureRow]:
    """Feature rows for one DOCX, with neighbour/run context resolved."""
    rows = di.read_rows(docx)
    geom = wrapmod.page_geom(docx)
    n = len(rows)
    recs: list[FeatureRow] = []
    for r in rows:
        ws = wrapmod.wrap_stat(r.text, geom)
        toks = _tokens(r.text)
        s = re.sub(r"\s+", " ", r.text).strip()
        recs.append({
            "source": source, "key": key, "idx": r.index,
            "text": s,
            # PHYSICS
            "fill": ws.fill, "wrap_lines": ws.lines, "wraps": ws.wraps,
            "char_len": ws.text_chars, "word_count": len(toks),
            # DELIBERATE (author-intended structure)
            "empty": r.empty,
            "br_count": r.br_count,
            "heading": r.heading,
            "thematic": r.thematic,
            "align_right": r.align in {"right", "end"},
            "numbered": r.numbered,
            # CONTENT discriminators
            "ends_terminal": bool(s) and s[-1] in _TERMINAL,
            "ends_colon": s.endswith(":"),
            "is_question": s.endswith("?"),
            "starts_dash": s[:1] in {"—", "–", "-"},
            "starts_upper": bool(s) and s[0].isupper(),
            "first_token": _first_token(s),
            # NOISE / negative controls (incidental styling)
            "nc_sp_after": r.spacing.get("after", ""),
            "nc_sp_before": r.spacing.get("before", ""),
            "nc_contextual": r.contextual,
            "nc_first_indent": bool(r.indent.get("firstLine")),
            "nc_jc": r.align,
            "nc_lineation_group": r.lineation_group,
            "nc_style": r.style,
        })

    # neighbour + run context (a "segment" = maximal span between deliberate
    # structural breaks: heading / thematic / numbered; empties stay inside as
    # stanza breaks). run_len = count of consecutive non-empty, non-wrapping,
    # content lines around i within the segment.
    def is_break(rec: FeatureRow) -> bool:
        return rec["heading"] or rec["thematic"] or rec["numbered"]

    for i, rec in enumerate(recs):
        prev = recs[i - 1] if i > 0 else None
        nxt = recs[i + 1] if i + 1 < n else None
        rec["prev_empty"] = bool(prev and prev["empty"])
        rec["next_empty"] = bool(nxt and nxt["empty"])
        rec["after_heading"] = bool(prev and prev["heading"])
        rec["after_thematic"] = bool(prev and prev["thematic"])
        # anaphora / parallelism vs nearest non-empty neighbours
        pn = next((recs[j] for j in range(i - 1, -1, -1) if not recs[j]["empty"]), None)
        nn = next((recs[j] for j in range(i + 1, n) if not recs[j]["empty"]), None)
        ft = rec["first_token"]
        rec["anaphora_prev"] = bool(ft and pn and pn["first_token"] == ft)
        rec["anaphora_next"] = bool(ft and nn and nn["first_token"] == ft)

    # run length of consecutive content lines (non-empty) that are short/non-wrapping
    def short_content(rec: FeatureRow) -> bool:
        return (not rec["empty"]) and (not rec["wraps"]) and (not is_break(rec))

    i = 0
    while i < n:
        if short_content(recs[i]) and not recs[i]["empty"]:
            # extend across empties (stanza breaks) but stop at a wrapping/break line
            content_idx: list[int] = []
            k = i
            while k < n:
                if recs[k]["empty"]:
                    k += 1
                    continue
                if short_content(recs[k]):
                    content_idx.append(k)
                    k += 1
                else:
                    break
            rl = len(content_idx)
            for pos, ci in enumerate(content_idx):
                recs[ci]["run_len"] = rl
                recs[ci]["run_pos"] = pos
            i = k
        else:
            recs[i].setdefault("run_len", 0)
            recs[i].setdefault("run_pos", -1)
            i += 1
    for rec in recs:
        rec.setdefault("run_len", 0)
        rec.setdefault("run_pos", -1)
    return recs


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--books", default="all", help="'all' or comma list of numbers")
    ap.add_argument("--poetry", action="store_true", help="also extract poetry/* (verse anchor)")
    ap.add_argument("--out", default=str(OUT / "features.jsonl"))
    args = ap.parse_args(argv)

    bd = book_dirs()
    if args.books != "all":
        want = {int(x) for x in args.books.split(",")}
        bd = [(num, p) for num, p in bd if num in want]
    targets = [Target("book", f"{num:02d}", p) for num, p in bd]
    if args.poetry:
        targets += [Target("poetry", name, p) for name, p in poetry_dirs()]

    OUT.mkdir(parents=True, exist_ok=True)
    outp = Path(args.out)
    total = 0
    manifest: list[ManifestEntry] = []
    with outp.open("w", encoding="utf-8") as f:
        for t in targets:
            recs = extract(t.docx, t.source, t.key)
            for rec in recs:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            total += len(recs)
            manifest.append({"source": t.source, "key": t.key, "paragraphs": len(recs)})
            print(f"  {t.source} {t.key}: {len(recs)} paragraphs")
    (OUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    print(f"wrote {total} rows -> {outp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
