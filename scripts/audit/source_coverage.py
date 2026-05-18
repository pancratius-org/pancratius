#!/usr/bin/env -S uv run --quiet
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml>=6.0"]
# ///
"""Every `legacy/**/*.docx` maps to a content/ folder or is in an explicit
allowlist (drafts, pre-cleanup variants)."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent.parent
CONTENT = ROOT / "content"
LEGACY = ROOT / "legacy"
MANIFEST = ROOT / "data" / "conversion-manifest.json"

# Why: pre-cleanup variants are draft sources superseded by `-clean` variants;
# they're intentionally not converted.
ALLOWED_SKIP_SUFFIXES = ("-pre-cleanup.docx", "-pre-cleanup-v2.docx")
ALLOWED_SKIP_NAMES: set[str] = set()


def _collect_originals() -> set[str]:
    used: set[str] = set()
    for md in CONTENT.rglob("*.md"):
        text = md.read_text(encoding="utf-8")
        if not text.startswith("---"):
            continue
        end = text.find("\n---", 3)
        if end < 0:
            continue
        fm = yaml.safe_load(text[4:end]) or {}
        for k in ("original_filename", "original_filenames"):
            v = fm.get(k)
            if isinstance(v, str):
                used.add(v)
            elif isinstance(v, list):
                used.update(str(x) for x in v)
    if MANIFEST.exists():
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        for work_entry in (manifest.get("by_work") or {}).values():
            if not isinstance(work_entry, dict):
                continue
            for entry in work_entry.get("images") or []:
                f = entry.get("original_filename")
                if f and not f.startswith(("book-cover-", "poem-cover", "project-cover", "cover:")):
                    used.add(f)
    return used


def main() -> int:
    used = _collect_originals()
    unmapped: list[Path] = []
    for docx in LEGACY.rglob("*.docx"):
        if docx.name.startswith("~$"):
            continue
        if docx.name.endswith(ALLOWED_SKIP_SUFFIXES):
            continue
        if docx.name in ALLOWED_SKIP_NAMES:
            continue
        if docx.name in used:
            continue
        # projects/<slug>/source.docx is referenced via the project frontmatter
        # `original_filename: source.docx` which is ambiguous across projects;
        # explicitly check that a content/projects/<slug>/ exists.
        if docx.parent.parent.name == "projects":
            slug = docx.parent.name
            if (CONTENT / "projects" / slug).is_dir():
                continue
        unmapped.append(docx)
    if unmapped:
        print(f"FAIL: {len(unmapped)} unmapped source docx files", file=sys.stderr)
        for p in unmapped:
            print(f"  {p.relative_to(ROOT)}", file=sys.stderr)
        return 1
    print(f"PASS: every source docx maps to content/ (modulo {len(ALLOWED_SKIP_NAMES)} explicit skip + draft suffixes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
