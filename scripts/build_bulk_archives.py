#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["pyyaml>=6"]
# ///

"""Package committed release artefacts into bulk archives for ``/downloads/``.

Per ``docs/downloads.md``, the production site ships **one** bulk archive:

  - ``all-md.zip`` — every ``<lang>.md`` from every work. Audience: LLM
    training, mirror sites, archival ingests. Markdown is the canonical,
    text-first surface; PDF/EPUB are presentation renderings of the same
    content and are served per-work on each book's page.

PDF and EPUB bulk archives can still be built off-host (for example, for
GitHub Releases or an Internet Archive upload) by passing ``--formats``
explicitly, e.g. ``--formats md,pdf,epub``. They are not built by default,
because they duplicate ~317 MB of bytes already served per-work and the
production host has a 1 GB ceiling.

Each entry inside a zip is keyed as ``<kind>/<lang>/<slug>.<ext>`` so the
tree unzips cleanly. A manifest is written to ``data/bulk-archives.json``
for the ``/downloads/`` index page to consume.

"Built" here means **packaging** already-committed bytes. The script does
not run pandoc or typst; if a work has no committed sibling artefact for a
format, that work is silently omitted from the bundle.

Two-phase pipeline because ``astro build`` wipes ``dist/``:

  uv run scripts/build_bulk_archives.py            # default: builds all-md.zip
    Writes the zip(s) to .cache/bulk-archives/ and data/bulk-archives.json
    so the /downloads/ page can render size + sha256 at astro-build time.

  uv run scripts/build_bulk_archives.py --publish
    Copies the cached zips into dist/downloads/ after astro build.

  uv run scripts/build_bulk_archives.py --formats md,pdf,epub
    Off-host build that also packages bulk PDF + EPUB. Intended for
    release/archive uploads, not the Beget deploy.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
CONTENT   = REPO_ROOT / "content"
DIST      = REPO_ROOT / "dist"
CACHE_DIR = REPO_ROOT / ".cache" / "bulk-archives"
MANIFEST  = REPO_ROOT / "data" / "bulk-archives.json"

KIND_DIRS = {"book": "books", "poem": "poetry", "project": "projects"}
LANGS = ("ru", "en")
ALL_FORMATS = ("md", "pdf", "epub")
DEFAULT_FORMATS = ("md",)


@dataclass(slots=True)
class Entry:
    kind: str
    lang: str
    slug: str
    path: Path


def _slug_for(md: Path) -> str | None:
    text = md.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return None
    _, fm, _ = text.split("---", 2)
    data = yaml.safe_load(fm)
    return data.get("slug") if isinstance(data, dict) else None


def _iter_entries(fmt: str) -> Iterable[Entry]:
    for kind, folder_name in KIND_DIRS.items():
        root = CONTENT / folder_name
        if not root.exists():
            continue
        for work_dir in sorted(root.iterdir()):
            if not work_dir.is_dir():
                continue
            for lang in LANGS:
                md = work_dir / f"{lang}.md"
                if not md.exists():
                    continue
                slug = _slug_for(md)
                if not slug:
                    continue
                src = work_dir / f"{lang}.{fmt}"
                if not src.exists():
                    continue
                yield Entry(kind=kind, lang=lang, slug=slug, path=src)


def _sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _human_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024
    return f"{n:.1f} TB"


def _build_archive(fmt: str) -> dict[str, object] | None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    out_path = CACHE_DIR / f"all-{fmt}.zip"
    entries = list(_iter_entries(fmt))
    if not entries:
        return None

    # For .md the contents are text and benefit from DEFLATE. For .pdf/.epub
    # the bytes are already compressed; ZIP_STORED produces a smaller archive
    # in practice and finishes faster.
    method = zipfile.ZIP_DEFLATED if fmt == "md" else zipfile.ZIP_STORED
    with zipfile.ZipFile(out_path, "w", method) as zf:
        for e in entries:
            arcname = f"{KIND_DIRS[e.kind]}/{e.lang}/{e.slug}.{fmt}"
            zf.write(e.path, arcname=arcname)
    size = out_path.stat().st_size
    return {
        "name":   f"all-{fmt}.zip",
        "format": fmt,
        "url":    f"/downloads/all-{fmt}.zip",
        "size":   size,
        "size_human": _human_bytes(size),
        "sha256": _sha256_of(out_path),
        "items":  len(entries),
    }


def _build_manifest(formats: tuple[str, ...]) -> int:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    archives: list[dict[str, object]] = []
    for fmt in formats:
        info = _build_archive(fmt)
        if info is None:
            continue
        archives.append(info)
        print(f"  bundled  {info['name']:18}  {info['size_human']:>10}  ({info['items']} items)")
    payload = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "archives": archives,
    }
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\nmanifest: {MANIFEST.relative_to(REPO_ROOT)}  ({len(archives)} archives)")
    return 0


def _publish_to_dist() -> int:
    if not DIST.exists():
        print("build_bulk_archives --publish: dist/ missing — run after astro build", file=sys.stderr)
        return 1
    if not MANIFEST.exists():
        print("build_bulk_archives --publish: data/bulk-archives.json missing — run without --publish first", file=sys.stderr)
        return 1
    # Why: publish only what the manifest declares. A stale `.cache/` from a
    # prior off-host `--formats md,pdf,epub` run must not leak large archives
    # into a default deploy.
    declared = json.loads(MANIFEST.read_text(encoding="utf-8")).get("archives") or []
    names = {a.get("name") for a in declared if isinstance(a, dict)}
    out_dir = DIST / "downloads"
    out_dir.mkdir(parents=True, exist_ok=True)
    copied = 0
    for name in sorted(names):
        if not name:
            continue
        src = CACHE_DIR / name
        if not src.is_file():
            print(f"  missing in .cache/: {name} — re-run without --publish first", file=sys.stderr)
            return 1
        dst = out_dir / name
        shutil.copyfile(src, dst)
        copied += 1
        print(f"  published  {dst.relative_to(REPO_ROOT)}  ({_human_bytes(dst.stat().st_size)})")
    print(f"\npublished {copied} archive(s) to {out_dir.relative_to(REPO_ROOT)}")
    return 0


def _parse_formats(raw: str) -> tuple[str, ...]:
    requested = tuple(s.strip().lower() for s in raw.split(",") if s.strip())
    unknown = [f for f in requested if f not in ALL_FORMATS]
    if unknown:
        raise SystemExit(
            f"unknown format(s): {', '.join(unknown)}. "
            f"valid: {', '.join(ALL_FORMATS)}"
        )
    return requested or DEFAULT_FORMATS


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--publish", action="store_true",
                        help="copy cached zips into dist/downloads/ (post-astro-build phase)")
    parser.add_argument("--formats", default=",".join(DEFAULT_FORMATS),
                        help=(
                            "comma-separated list of formats to bundle "
                            f"(default: {','.join(DEFAULT_FORMATS)}; "
                            f"available: {','.join(ALL_FORMATS)}). "
                            "Off-host builds may pass `md,pdf,epub` for full archives."
                        ))
    args = parser.parse_args()
    if args.publish:
        return _publish_to_dist()
    return _build_manifest(_parse_formats(args.formats))


if __name__ == "__main__":
    sys.exit(main())
