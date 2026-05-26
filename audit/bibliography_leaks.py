"""Ensure catalog/bibliography endmatter is not shipped in reading bodies."""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOOKS = ROOT / "src" / "content" / "books"

BIBLIO_HEADING = re.compile(
    r"^#{1,6}\s+(?:библиография|bibliography|список\s+литературы|литература)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
LEAK_TELL = re.compile(r"<img\b|!\[[^\]]*\]\([^)]+\)|<table\b|litres\.ru|kindbook\.net", re.IGNORECASE)
HEADING = re.compile(r"^#{1,6}\s+\S", re.MULTILINE)


def main() -> int:
    failures: list[str] = []
    for md in sorted(BOOKS.glob("*/*.md")):
        text = md.read_text(encoding="utf-8")
        for m in BIBLIO_HEADING.finditer(text):
            next_heading = HEADING.search(text, m.end())
            end = next_heading.start() if next_heading else len(text)
            line = text[:m.start()].count("\n") + 1
            section = text[m.end():end]
            reason = "catalog payload" if LEAK_TELL.search(section) else "hanging heading"
            failures.append(f"{md.relative_to(ROOT)}:{line}: bibliography/catalog section still in body ({reason})")

    if failures:
        print("FAIL: bibliography leaks", file=sys.stderr)
        for failure in failures[:40]:
            print(f"  {failure}", file=sys.stderr)
        if len(failures) > 40:
            print(f"  ... {len(failures) - 40} more", file=sys.stderr)
        return 1

    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
