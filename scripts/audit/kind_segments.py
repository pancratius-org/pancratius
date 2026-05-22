#!/usr/bin/env -S uv run --quiet
"""Check work-kind segment maps that feed routes and archive paths."""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent

EXPECTED = {
    "book": "books",
    "poem": "poetry",
    "project": "projects",
}

CHECKS = {
    "src/lib/i18n.ts": {
        "book": r"book:\s*['\"]books['\"]",
        "poem": r"poem:\s*['\"]poetry['\"]",
        "project": r"project:\s*['\"]projects['\"]",
    },
    "src/lib/works.ts": {
        "book": r"book:\s*['\"]books['\"]",
        "poem": r"poem:\s*['\"]poetry['\"]",
        "project": r"project:\s*['\"]projects['\"]",
    },
    "src/lib/body-images.ts": {
        "book": r"book:\s*['\"]books['\"]",
        "poem": r"poem:\s*['\"]poetry['\"]",
        "project": r"project:\s*['\"]projects['\"]",
    },
    "src/lib/public-markdown.ts": {
        "book": r"book:\s*['\"]books['\"]",
        "poem": r"poem:\s*['\"]poetry['\"]",
        "project": r"project:\s*['\"]projects['\"]",
    },
    "scripts/build_slug_map.py": {
        "book": r"['\"]book['\"]:\s*\(['\"]books['\"],\s*['\"]books['\"]\)",
        "poem": r"['\"]poem['\"]:\s*\(['\"]poetry['\"],\s*['\"]poetry['\"]\)",
        "project": r"['\"]project['\"]:\s*\(['\"]projects['\"],\s*['\"]projects['\"]\)",
    },
    "scripts/render_downloads.py": {
        "book": r"['\"]book['\"]:\s*['\"]books['\"]",
        "poem": r"['\"]poem['\"]:\s*['\"]poetry['\"]",
        "project": r"['\"]project['\"]:\s*['\"]projects['\"]",
    },
    "scripts/build_bulk_archives.ts": {
        "book": r"book:\s*['\"]books['\"]",
        "poem": r"poem:\s*['\"]poetry['\"]",
        "project": r"project:\s*['\"]projects['\"]",
    },
}


def main() -> int:
    failures: list[str] = []
    for rel, patterns in CHECKS.items():
        path = ROOT / rel
        if not path.exists():
            failures.append(f"{rel}: missing")
            continue
        text = path.read_text(encoding="utf-8")
        for kind, segment in EXPECTED.items():
            if not re.search(patterns[kind], text):
                failures.append(f"{rel}: {kind!r} must map to {segment!r}")
    if failures:
        print("FAIL: kind segment maps drifted", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        return 1
    print("PASS: kind segment maps agree")
    return 0


if __name__ == "__main__":
    sys.exit(main())
