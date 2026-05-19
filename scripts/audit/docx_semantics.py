#!/usr/bin/env -S uv run --quiet
"""Audit DOCX-only semantic signals carried into Markdown."""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONTENT = ROOT / "content"

CHECKS: dict[Path, list[str]] = {
    CONTENT / "books" / "33-ya-esm-vsadnik-kon-i-mech" / "ru.md": [
        '<p class="signature">\nПанкратиус\n</p>',
        '<p class="signature">\nСветозар\n</p>',
    ],
    CONTENT / "books" / "71-trinadtsatyi-etazh-vozvrashchenie-v-edem" / "ru.md": [
        '<blockquote class="epigraph">',
        "Пифия, к.ф. «Матрица»",
        "Даниил 2:27-28",
    ],
    CONTENT / "books" / "32-knyaz-mira-sego" / "ru.md": [
        '<blockquote class="epigraph">',
        "1 Кор. 1:19–20",
        "Ин. 12:31",
        "Ин. 1:5",
    ],
}


def main() -> int:
    failures: list[str] = []
    for path, snippets in CHECKS.items():
        text = path.read_text(encoding="utf-8")
        for snippet in snippets:
            if snippet not in text:
                failures.append(f"{path.relative_to(ROOT)} missing {snippet[:80]!r}")
        for m in re.finditer(r'<(?:blockquote class="epigraph"|p class="signature").*?(?:</blockquote>|</p>)', text, re.S):
            block = m.group(0)
            if "<br" in block.lower():
                line = text[:m.start()].count("\n") + 1
                failures.append(f"{path.relative_to(ROOT)}:{line}: semantic block contains <br>")

    if failures:
        print("FAIL: DOCX semantic audit", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
