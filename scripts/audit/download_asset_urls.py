#!/usr/bin/env -S uv run --quiet
"""Ensure public Markdown exports use work-scoped original asset URLs.

This runs after ``npm run build``. It scans generated Markdown files and
``dist/downloads/all-md.zip`` for local image URLs outside ``/assets/``.
"""
from __future__ import annotations

import re
import sys
import zipfile
from pathlib import Path
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parent.parent.parent
DIST = ROOT / "dist"
ARCHIVE = DIST / "downloads" / "all-md.zip"

MARKDOWN_IMAGE = re.compile(r"!\[[^\]]*]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")


def _bad_image_url(raw_url: str) -> str | None:
    url = raw_url.strip().strip("<>")
    parsed = urlparse(url)
    path = unquote(parsed.path if parsed.scheme in {"http", "https"} else url)
    normalized = "/" + path.lstrip("./")
    if "/images/" not in normalized:
        return None
    if normalized.startswith("/assets/"):
        return None
    return url


def _check_text(label: str, text: str) -> list[str]:
    matches = sorted({
        bad
        for match in MARKDOWN_IMAGE.finditer(text)
        if (bad := _bad_image_url(match.group(1))) is not None
    })
    return [f"{label}: {m}" for m in matches]


def main() -> int:
    if not DIST.exists():
        print("FAIL: dist/ missing; run npm run build first", file=sys.stderr)
        return 1

    failures: list[str] = []
    for md in sorted(DIST.rglob("*.md")):
        failures.extend(_check_text(str(md.relative_to(ROOT)), md.read_text(encoding="utf-8")))

    if not ARCHIVE.exists():
        print(f"FAIL: {ARCHIVE.relative_to(ROOT)} missing", file=sys.stderr)
        return 1

    with zipfile.ZipFile(ARCHIVE) as zf:
        for name in sorted(zf.namelist()):
            if not name.endswith(".md"):
                continue
            text = zf.read(name).decode("utf-8", errors="replace")
            failures.extend(_check_text(f"{ARCHIVE.relative_to(ROOT)}:{name}", text))

    if failures:
        print("FAIL: non-asset image URLs found in public Markdown", file=sys.stderr)
        for failure in failures[:50]:
            print(f"  {failure}", file=sys.stderr)
        if len(failures) > 50:
            print(f"  ... and {len(failures) - 50} more", file=sys.stderr)
        return 1

    print("PASS: markdown image URLs use /assets/... for local images")
    return 0


if __name__ == "__main__":
    sys.exit(main())
