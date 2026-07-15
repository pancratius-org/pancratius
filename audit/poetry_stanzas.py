"""Verify poetry Markdown preserves DOCX stanza boundaries.

The audit derives stanza policy independently over the same pagination-safe
Pandoc projection used by the converter. This catches grouping regressions
without reintroducing a second interpretation of Word breaks.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pancratius import docx_pandoc, docx_source
from pancratius.poem_chrome import is_signoff_line

ROOT = Path(os.environ.get("PANCRATIUS_AUDIT_ROOT", Path(__file__).resolve().parents[1])).resolve()
CONTENT = ROOT / "src" / "content" / "poetry"


@dataclass(frozen=True, slots=True)
class PoemMeta:
    number: int
    title: str
    slug: str


def _is_strong_only(block: dict[str, Any]) -> bool:
    return block.get("t") == "Para" and len(block.get("c") or []) == 1 and block["c"][0].get("t") == "Strong"


def _title_key(s: str) -> str:
    """A loose comparison key for a poem title: case-folded, with a trailing style
    note, markup characters, and punctuation removed. Note-tolerant so a self-sufficient
    DOCX title line ("Весна (в духе Есенина)") keys equal to the frontmatter title."""
    s = s.casefold()
    s = re.sub(r"\s*\(\s*в\s+(?:духе|стиле)\b[^)]*\)", "", s)  # "(в духе Есенина)"
    s = re.sub(r"[*_`«»“”„\"'…]", "", s)                       # markup / quote chars
    s = re.sub(r"[^\w\s]", " ", s)                              # other punctuation
    return re.sub(r"\s+", " ", s).strip()


def expected_groups(docx: Path, title: str) -> list[int]:
    source = docx_source.read(docx)
    with tempfile.TemporaryDirectory(prefix="poetry-stanzas-") as temp_dir:
        ast, _ = docx_pandoc.run_json(source, Path(temp_dir))
    blocks = docx_pandoc.strip_source_anchors(ast.get("blocks") or [])
    key = _title_key(title)
    groups: list[list[str]] = []
    current: list[str] = []
    seen = False

    def flush() -> None:
        nonlocal current
        if current:
            groups.append(current)
            current = []

    for block in blocks:
        if block.get("t") != "Para":
            flush()
            continue
        inlines = block.get("c") or []
        if not inlines:
            flush()
            continue
        lines = [
            line
            for raw in docx_pandoc.inline_text(
                inlines,
                soft_break=docx_pandoc.SoftBreakRendering.LINE,
            ).split("\n")
            if (line := raw.strip()) and not is_signoff_line(line)
        ]
        if not lines:
            flush()
            continue
        # One rule, shared with the converter: a leading fully-bold paragraph whose key
        # matches the frontmatter title is the title — drop it. An incipit's first line
        # is plain verse (not Strong), so it is kept.
        if not seen and _is_strong_only(block) and _title_key("\n".join(lines)) == key:
            seen = True
            continue
        seen = True
        if len(lines) == 1 and lines[0] == "***":
            flush()
            groups.append(lines)
        elif len(lines) > 1:
            flush()
            groups.append(lines)
        else:
            current.append(lines[0])
    flush()
    return [len(g) for g in groups]


def actual_groups(md: Path) -> list[int]:
    text = md.read_text(encoding="utf-8")
    body = text.split("---", 2)[2].strip()
    groups = [
        g for g in re.split(r"\n\s*\n", body)
        if g.strip() and not re.fullmatch(r"!\[[^\]]*]\([^)]+\)", g.strip())
    ]
    return [len([ln for ln in g.splitlines() if ln.strip()]) for g in groups]


def source_docx(number: int) -> Path:
    matches = sorted(CONTENT.glob(f"{number:02d}-*/ru.docx"))
    matches = [m for m in matches if not m.name.startswith(".~")]
    if not matches:
        raise FileNotFoundError(f"committed poetry DOCX not found for #{number}")
    return matches[0]


def _committed_poem_meta() -> list[PoemMeta]:
    """Metadata for every committed poem, sorted by number.

    Title and number are the only inputs the importer needs to reproduce a poem's
    body deterministically; we read them from the committed frontmatter so the
    ``--from-ir`` pass imports each poem with the SAME title/number the live
    importer would use, then compares the freshly converted body to the DOCX
    stanza oracle."""
    meta: list[PoemMeta] = []
    for md in sorted(CONTENT.glob("*/ru.md")):
        text = md.read_text(encoding="utf-8")
        m = re.search(r"^number:\s*(\d+)\s*$", text, re.M)
        if not m:
            raise ValueError(f"{md}: missing number")
        number = int(m.group(1))
        tm = re.search(r"^title:\s*(.+?)\s*$", text, re.M)
        title = tm.group(1).strip().strip("'\"") if tm else ""
        sm = re.search(r"^slug:\s*(.+?)\s*$", text, re.M)
        slug = sm.group(1).strip().strip("'\"") if sm else md.parent.name
        meta.append(PoemMeta(number, title, slug))
    return sorted(meta, key=lambda poem: poem.number)


def actual_groups_from_ir() -> int:
    """Stanza oracle run against FRESH importer output, not committed content.

    Imports every committed poem DOCX through the live importer
    (``import_docx.import_work`` -> ``pancratius.docx_conversion.convert_single_docx``)
    into a throwaway content root, then asserts the
    converted body's stanza line-counts equal the DOCX ``poetry_stanzas`` oracle.
    This validates the IR conversion directly rather than relying on the committed
    (GFM-era) markdown the default mode reads.

    Returns a process exit code (0 = all poems match)."""
    import contextlib
    import io
    import tempfile

    # Imported lazily/here so the audit's default committed-content mode stays a
    # pure stdlib reader (no importer wiring) and works without scripts on path.
    from pancratius import import_docx

    failures: list[str] = []
    checked = 0
    for poem in _committed_poem_meta():
        docx = source_docx(poem.number)
        with tempfile.TemporaryDirectory(prefix="poetry-ir-") as td:
            content_root = Path(td) / "src" / "content"
            request = import_docx.ImportRequest.for_new_work(
                docx=docx,
                kind="poem",
                lang="ru",
                number=poem.number,
                slug=poem.slug,
                title=poem.title,
                out_content=content_root,
            )
            with contextlib.redirect_stdout(io.StringIO()):
                report = import_docx.import_work(request)
            if report.refused:
                failures.append(f"poem #{poem.number:02d} {poem.slug}: IR import refused")
                continue
            work_key = (
                poem.slug
                if re.match(r"^\d{1,4}-", poem.slug)
                else f"{poem.number:02d}-{poem.slug}"
            )
            got = actual_groups(content_root / "poetry" / work_key / "ru.md")
        exp = expected_groups(docx, poem.title)
        checked += 1
        if exp != got:
            failures.append(
                f"poem #{poem.number:02d} {poem.slug}: expected stanza line-counts {exp}, got {got}"
            )
    if failures:
        print("FAIL: poetry stanza mismatches (IR import path)")
        for failure in failures[:30]:
            print(" ", failure)
        if len(failures) > 30:
            print(f"  ... {len(failures) - 30} more")
        return 1
    print(f"checked {checked} poems via IR import path; stanza boundaries match DOCX")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--from-ir",
        action="store_true",
        help="Import each committed poem DOCX through the live importer (IR path) into a "
        "temp tree and run the stanza oracle on that fresh output, instead of "
        "reading the committed (GFM-era) markdown.",
    )
    args = ap.parse_args(argv)

    # The oracle re-derives stanza structure from the source DOCX through pandoc.
    # CI ships no pandoc by contract — it builds the site, never renders documents
    # (.github/workflows, docs/downloads.md) — so the oracle is uncomputable there and
    # there is nothing to compare against: skip, as the pandoc-backed pytest paths do.
    # The check still gates locally, where importing a poem (which needs pandoc) is the
    # only thing that can introduce a stanza regression in the first place.
    if shutil.which("pandoc") is None:
        print("SKIP: pandoc unavailable — stanza oracle needs it (CI ships no pandoc)")
        return 0

    if args.from_ir:
        return actual_groups_from_ir()

    failures: list[str] = []
    checked = 0
    for md in sorted(CONTENT.glob("*/ru.md")):
        text = md.read_text(encoding="utf-8")
        m = re.search(r"^number:\s*(\d+)\s*$", text, re.M)
        if not m:
            failures.append(f"{md}: missing number")
            continue
        number = int(m.group(1))
        tm = re.search(r"^title:\s*(.+?)\s*$", text, re.M)
        title = tm.group(1).strip().strip("'\"") if tm else ""
        exp = expected_groups(source_docx(number), title)
        got = actual_groups(md)
        checked += 1
        if exp != got:
            failures.append(
                f"poem #{number:02d} {md.parent.name}: expected stanza line-counts {exp}, got {got}"
            )
    if failures:
        print("FAIL: poetry stanza mismatches")
        for failure in failures[:30]:
            print(" ", failure)
        if len(failures) > 30:
            print(f"  ... {len(failures) - 30} more")
        return 1
    print(f"checked {checked} poems; stanza boundaries match DOCX")
    return 0


if __name__ == "__main__":
    sys.exit(main())
