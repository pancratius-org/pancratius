#!/usr/bin/env -S uv run --quiet
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Zero `**\\**`, lone `\\` lines, surviving `<u>`, `<span class="anchor">`,
`[]{#…}` anchor refs, `<span class="smallcaps">` survivors."""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
CONTENT = ROOT / "src" / "content"

PATTERNS: dict[str, re.Pattern[str]] = {
    "bold-backslash": re.compile(r"\*\*\\\*\*"),
    "lone-backslash-line": re.compile(r"^\s*\\\s*$", re.MULTILINE),
    "u-tag": re.compile(r"</?u>"),
    "anchor-span": re.compile(r'<span\s+class="anchor"'),
    "smallcaps-span": re.compile(r'<span\s+class="smallcaps"'),
    "underline-span": re.compile(r'<span\s+class="underline"'),
    "pandoc-anchor-ref": re.compile(r"\[\]\{#[^}]+\}"),
    "escaped-thematic-break": re.compile(r"^\s*(?:\\\*\s*){3}\s*$", re.MULTILINE),
}


def main() -> int:
    counts: dict[str, list[tuple[Path, int]]] = {k: [] for k in PATTERNS}
    for md in CONTENT.rglob("*.md"):
        text = md.read_text(encoding="utf-8")
        for name, pat in PATTERNS.items():
            for m in pat.finditer(text):
                line = text[:m.start()].count("\n") + 1
                counts[name].append((md, line))
    total = sum(len(v) for v in counts.values())
    for name, hits in counts.items():
        if hits:
            print(f"{name}: {len(hits)} (first: {hits[0][0].relative_to(ROOT)}:{hits[0][1]})", file=sys.stderr)
    if total:
        print(f"FAIL: {total} formatting artifacts", file=sys.stderr)
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
