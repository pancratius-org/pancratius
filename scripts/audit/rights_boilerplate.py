#!/usr/bin/env -S uv run --quiet
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Zero `all rights reserved` / Russian-equivalent boilerplate phrases in
content/ — both rendered MD bodies and downloadable DOCX. Bounded scrub
applied to both pipelines should leave nothing of this in either."""
from __future__ import annotations

import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
CONTENT = ROOT / "content"

MD_PATTERNS = [
    re.compile(r"(?i)all rights reserved"),
    re.compile(r"(?i)no part of this book may be reproduced"),
    re.compile(r"Все\s+права\s+защищены", re.IGNORECASE),
    re.compile(r"Никакая\s+часть\s+(этой|данной)\s+книги.*воспроизв", re.IGNORECASE),
]
XML_PATTERNS = [
    re.compile(rb"(?i)all rights reserved"),
    re.compile(rb"(?i)no part of this book may be reproduced"),
]


def main() -> int:
    failures: list[str] = []
    for md in CONTENT.rglob("*.md"):
        text = md.read_text(encoding="utf-8")
        for pat in MD_PATTERNS:
            for m in pat.finditer(text):
                line = text[:m.start()].count("\n") + 1
                failures.append(f"{md.relative_to(ROOT)}:{line} {m.group(0)!r}")
    for docx in CONTENT.rglob("*.docx"):
        try:
            with zipfile.ZipFile(docx) as zf:
                xml = zf.read("word/document.xml")
        except (KeyError, zipfile.BadZipFile):
            continue
        for pat in XML_PATTERNS:
            if pat.search(xml):
                failures.append(f"{docx.relative_to(ROOT)} (in word/document.xml)")
                break
    if failures:
        print(f"FAIL: {len(failures)} rights-boilerplate hits", file=sys.stderr)
        for f in failures[:25]:
            print(f"  {f}", file=sys.stderr)
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
