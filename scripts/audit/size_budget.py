#!/usr/bin/env -S uv run --quiet
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Work-bundle size budgets.

The corpus commits downloadable artifacts beside Markdown. Budget them
separately from source content; otherwise adding the intended downloads looks
like an asset-regression failure.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent

CONTENT_DIR = ROOT / "content"
DIST_DIR = ROOT / "dist"

DOWNLOAD_EXTS = {".docx", ".pdf", ".epub"}

CONTENT_SOURCE_BUDGET_MB = 700
CONTENT_DOWNLOAD_BUDGET_MB = 850
# Why: Beget free tier has a 1 GB ceiling. Headroom for static assets +
# Pagefind index + per-work downloads + the single all-md.zip bulk archive.
# Bulk PDF/EPUB archives are off-host (see scripts/build_bulk_archives.py).
DIST_BUDGET_MB = 900


def _du(path: Path) -> int:
    total = 0
    for p in path.rglob("*"):
        if p.is_file():
            total += p.stat().st_size
    return total


def _du_matching(path: Path, *, release: bool) -> int:
    total = 0
    for p in path.rglob("*"):
        if not p.is_file():
            continue
        is_download = p.suffix.lower() in DOWNLOAD_EXTS
        if is_download == release:
            total += p.stat().st_size
    return total


def main() -> int:
    failures: list[str] = []
    if CONTENT_DIR.exists():
        source_mb = _du_matching(CONTENT_DIR, release=False) / (1 << 20)
        download_mb = _du_matching(CONTENT_DIR, release=True) / (1 << 20)
        total_mb = source_mb + download_mb
        print(
            f"content/ source: {source_mb:.1f} MB "
            f"(budget {CONTENT_SOURCE_BUDGET_MB} MB)"
        )
        print(
            f"content/ download artifacts: {download_mb:.1f} MB "
            f"(budget {CONTENT_DOWNLOAD_BUDGET_MB} MB)"
        )
        print(f"content/ total: {total_mb:.1f} MB")
        if source_mb > CONTENT_SOURCE_BUDGET_MB:
            failures.append(
                f"content source exceeds budget by "
                f"{source_mb - CONTENT_SOURCE_BUDGET_MB:.1f} MB"
            )
        if download_mb > CONTENT_DOWNLOAD_BUDGET_MB:
            failures.append(
                f"content download artifacts exceed budget by "
                f"{download_mb - CONTENT_DOWNLOAD_BUDGET_MB:.1f} MB"
            )
    if (DIST_DIR / "index.html").exists():
        size_mb = _du(DIST_DIR) / (1 << 20)
        print(f"dist/: {size_mb:.1f} MB (budget {DIST_BUDGET_MB} MB)")
        if size_mb > DIST_BUDGET_MB:
            failures.append(f"dist exceeds budget by {size_mb - DIST_BUDGET_MB:.1f} MB")
    else:
        print("dist/ has no index.html (skipping; run `npm run build` first)")
    if failures:
        print(f"FAIL: {len(failures)} budget checks", file=sys.stderr)
        for f in failures:
            print(f"  {f}", file=sys.stderr)
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
