#!/usr/bin/env -S uv run --quiet
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml>=6.0"]
# ///
"""EN frontmatter `title` is majority Latin OR carries `title_is_untranslated:
true`. Fails on any EN entry whose title is Cyrillic-majority without the
flag set."""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent.parent
CONTENT = ROOT / "content"


def is_majority_latin(s: str) -> bool:
    lat = sum(1 for c in s if c.isascii() and c.isalpha())
    cyr = sum(1 for c in s if "Ѐ" <= c <= "ӿ")
    if lat + cyr == 0:
        return True
    return lat >= cyr


def main() -> int:
    failures: list[tuple[Path, str]] = []
    checked = 0
    for md in CONTENT.rglob("en.md"):
        text = md.read_text(encoding="utf-8")
        if not text.startswith("---"):
            continue
        end = text.find("\n---", 3)
        if end < 0:
            continue
        fm = yaml.safe_load(text[4:end]) or {}
        title = str(fm.get("title") or "")
        if not title:
            continue
        checked += 1
        flagged = bool(fm.get("title_is_untranslated"))
        if not is_majority_latin(title) and not flagged:
            failures.append((md, title))
    print(f"checked {checked} en.md files")
    if failures:
        print(f"FAIL: {len(failures)} EN titles are Russian and not flagged untranslated", file=sys.stderr)
        for md, t in failures[:15]:
            print(f"  {md.relative_to(ROOT)} title={t!r}", file=sys.stderr)
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
