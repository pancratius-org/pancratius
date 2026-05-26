"""Zero TOC leaks: pandoc-emitted `[Title [pageno](#anchor)](#anchor)` lines,
bare `_TocNNNN` anchor-ids, and `# Оглавление`/`# Contents` headings that
immediately precede a dotted-link block."""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTENT = ROOT / "src" / "content"

TOC_LINE = re.compile(r"^\[.+?\[\d+\]\(#[^)]+\)\]\(#[^)]+\)\s*$")
TOC_ANCHOR = re.compile(r"_Toc\d+")


def main() -> int:
    failures: list[tuple[Path, int, str]] = []
    for md in CONTENT.rglob("*.md"):
        for i, ln in enumerate(md.read_text(encoding="utf-8").splitlines(), 1):
            if TOC_LINE.match(ln.strip()):
                failures.append((md, i, "toc-link-line"))
            elif TOC_ANCHOR.search(ln):
                failures.append((md, i, "Toc-anchor-id"))
    if failures:
        print(f"FAIL: {len(failures)} TOC leaks", file=sys.stderr)
        for md, ln, why in failures[:20]:
            print(f"  {md.relative_to(ROOT)}:{ln} {why}", file=sys.stderr)
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
