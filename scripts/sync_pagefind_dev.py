#!/usr/bin/env python3
"""Expose the last built Pagefind index to Astro's dev server.

`astro dev` serves `public/`, not `dist/`, while Pagefind is generated after
`astro build` into `dist/pagefind/`. This copy keeps local search usable after
at least one production build without making Pagefind a committed artefact.
"""

from __future__ import annotations

import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "dist" / "pagefind"
TARGET = ROOT / "public" / "pagefind"


def main() -> None:
    # Never delete the existing TARGET unless we have a SOURCE to replace it
    # with: a missing SOURCE just means no production build has run yet, and
    # wiping TARGET in that case would silently break local dev search.
    if not SOURCE.exists():
        print("pagefind dev sync: dist/pagefind not found; run `npm run build` once to enable local search")
        return
    if TARGET.exists():
        shutil.rmtree(TARGET)
    shutil.copytree(SOURCE, TARGET)
    print(f"pagefind dev sync: copied {SOURCE.relative_to(ROOT)} -> {TARGET.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
