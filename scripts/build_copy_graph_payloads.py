#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# ///

"""Copy the public conceptosphere graph payloads into ``public/data/``.

The source-of-truth artefacts live in ``data/`` (committed alongside the
generators that produce them). They are not web-public from there — Astro
serves files from ``public/``. ``public/data/*-graph.json`` is therefore a
derived copy refreshed before every build.

Embedding intermediates (``conceptosphere-embed.json`` and the cache folder)
are explicitly not copied: they are recommendation inputs, not UI payloads.

Idempotent. Safe to run on every dev/build cycle.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT / "data"
DST_DIR = ROOT / "public" / "data"

PAYLOADS = (
    "pancratius-concepts-graph.json",
    "pancratius-books-graph.json",
)


def main() -> int:
    DST_DIR.mkdir(parents=True, exist_ok=True)
    missing: list[str] = []
    for name in PAYLOADS:
        src = SRC_DIR / name
        if not src.exists():
            missing.append(name)
            continue
        dst = DST_DIR / name
        # Only copy when bytes differ — avoids gratuitous mtime churn.
        if dst.exists() and dst.read_bytes() == src.read_bytes():
            continue
        shutil.copy2(src, dst)
        print(f"copied {name} -> public/data/", file=sys.stderr)
    if missing:
        print(
            "conceptosphere payloads missing in data/: "
            + ", ".join(missing)
            + " — run `uv run pancratius data graph generate` to regenerate",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
