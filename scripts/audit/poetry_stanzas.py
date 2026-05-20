#!/usr/bin/env -S uv run --quiet
"""Verify poetry Markdown preserves DOCX stanza boundaries.

The converter reads poem DOCX through Pandoc's `docx+empty_paragraphs` AST.
This audit repeats the structural read independently enough to catch the
regression where empty Word paragraphs were collapsed and poems became one
giant stanza.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
CONTENT = ROOT / "content" / "poetry"
LEGACY = ROOT / "legacy" / "poetry"


def _inlines_to_text(inlines: list[dict[str, Any]]) -> str:
    out: list[str] = []
    for item in inlines:
        typ = item.get("t")
        val = item.get("c")
        if typ == "Str":
            out.append(str(val))
        elif typ == "Space":
            out.append(" ")
        elif typ in {"SoftBreak", "LineBreak"}:
            out.append("\n")
        elif typ in {"Strong", "Emph", "Underline", "SmallCaps", "Strikeout"}:
            out.append(_inlines_to_text(val or []))
        elif typ == "Quoted":
            out.append(_inlines_to_text(val[1]))
        elif typ == "Code":
            out.append(str(val[1]))
        elif typ == "Link":
            out.append(_inlines_to_text(val[1]))
        elif typ == "Image":
            out.append("[image]")
        elif typ == "Span":
            out.append(_inlines_to_text(val[1]))
        elif isinstance(val, list):
            out.append(_inlines_to_text(val))
    return "".join(out)


def _is_strong_only(block: dict[str, Any]) -> bool:
    return block.get("t") == "Para" and len(block.get("c") or []) == 1 and block["c"][0].get("t") == "Strong"


def _count_breaks(inlines: list[dict[str, Any]]) -> int:
    total = 0
    for item in inlines:
        typ = item.get("t")
        val = item.get("c")
        if typ in {"SoftBreak", "LineBreak"}:
            total += 1
        elif typ in {"Strong", "Emph", "Underline", "SmallCaps", "Strikeout"}:
            total += _count_breaks(val or [])
        elif typ == "Quoted":
            total += _count_breaks(val[1])
        elif typ == "Link":
            total += _count_breaks(val[1])
        elif typ == "Span":
            total += _count_breaks(val[1])
        elif isinstance(val, list):
            total += _count_breaks(val)
    return total


def _title_key(s: str) -> str:
    s = re.sub(r"^[#>*_`\s-]+|[*_`\s-]+$", "", s.strip())
    s = s.replace("…", "...")
    s = re.sub(r"[.,;:!?]+$", "", s)
    return re.sub(r"\s+", " ", s).casefold().strip()


def _source_duplicate_title(blocks: list[dict[str, Any]], title: str) -> bool:
    key = _title_key(title)
    if not key:
        return False
    nonempty: list[dict[str, Any]] = []
    for block in blocks:
        if block.get("t") != "Para":
            continue
        text = _inlines_to_text(block.get("c") or [])
        if text.strip():
            nonempty.append(block)
        if len(nonempty) >= 2:
            break
    if not nonempty:
        return False
    first = nonempty[0]
    first_inlines = first.get("c") or []
    if _title_key(_inlines_to_text(first_inlines)) != key:
        return False
    second_breaks = _count_breaks(nonempty[1].get("c") or []) if len(nonempty) > 1 else 0
    second_text = _inlines_to_text(nonempty[1].get("c") or []) if len(nonempty) > 1 else ""
    second_is_section = bool(re.match(r"^(?:[IVXLCDM]+\.|[А-ЯA-Z]\.)\s+\S", second_text.strip(), re.I))
    return _is_strong_only(first) or _count_breaks(first_inlines) > 0 or second_breaks > 0 or second_is_section


def expected_groups(docx: Path, title: str) -> list[int]:
    proc = subprocess.run(
        ["pandoc", "--from", "docx+empty_paragraphs", "--to", "json", str(docx)],
        capture_output=True,
        text=True,
        check=True,
    )
    blocks = json.loads(proc.stdout).get("blocks") or []
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
        lines = [ln.strip() for ln in _inlines_to_text(inlines).split("\n") if ln.strip()]
        if not lines:
            flush()
            continue
        if not seen and _is_strong_only(block):
            flush()
            groups.append(lines)
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
    if groups and _source_duplicate_title(blocks, title) and len(groups[0]) == 1 and _title_key(groups[0][0]) == _title_key(title):
        groups = groups[1:]
    return [len(g) for g in groups]


def actual_groups(md: Path) -> list[int]:
    text = md.read_text(encoding="utf-8")
    body = text.split("---", 2)[2].strip()
    groups = [g for g in re.split(r"\n\s*\n", body) if g.strip()]
    return [len([ln for ln in g.splitlines() if ln.strip()]) for g in groups]


def source_docx(number: int) -> Path:
    matches = sorted(LEGACY.glob(f"{number:02d}. */*.docx"))
    matches = [m for m in matches if not m.name.startswith(".~")]
    if not matches:
        raise FileNotFoundError(f"legacy poetry DOCX not found for #{number}")
    return matches[0]


def main() -> int:
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
