#!/usr/bin/env -S uv run --quiet
"""Ensure public Markdown exports use work-scoped original asset URLs.

This runs after ``npm run build``. It scans generated Markdown files and
``dist/downloads/all-md.zip`` for local image URLs outside ``/assets/``.

Wrapped by the harness as PAN008 (scripts/audit/rules/downloads.ts); honours
``PANCRATIUS_AUDIT_ROOT`` so it can run against a fixture. Runs in the project
env — no PEP-723 header needed.
"""
from __future__ import annotations

import os
import re
import sys
import zipfile
from pathlib import Path
from urllib.parse import unquote, urlparse


def _audit_root() -> Path:
    env = os.environ.get("PANCRATIUS_AUDIT_ROOT")
    # scripts/audit/python/download_asset_urls.py -> repo root is four levels up.
    return Path(env).resolve() if env else Path(__file__).resolve().parents[3]


ROOT = _audit_root()
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
