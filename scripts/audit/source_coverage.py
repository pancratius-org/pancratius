#!/usr/bin/env -S uv run --quiet
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Every `legacy/**/*.docx` maps to a generated work source or is in an
explicit allowlist (drafts, pre-cleanup variants)."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parent.parent.parent
CONTENT = ROOT / "src" / "content"
LEGACY = ROOT / "legacy"
MANIFEST = ROOT / "data" / "conversion-manifest.json"

# Why: pre-cleanup variants are draft sources superseded by `-clean` variants;
# they're intentionally not converted.
ALLOWED_SKIP_SUFFIXES = ("-pre-cleanup.docx", "-pre-cleanup-v2.docx")
ALLOWED_SKIP_NAMES: set[str] = set()


def _legacy_rel_from_source_url(raw: str) -> str | None:
    if not raw:
        return None
    raw = unquote(raw.replace("\\", "/").lstrip("/"))
    if raw.startswith("legacy/") and raw.endswith(".docx"):
        return raw
    # Legacy data usually stores URLs like `/books/ru/file.docx`. Map them
    # into the repository path without making content frontmatter carry this
    # provenance detail.
    if raw.endswith(".docx") and raw.startswith(("books/", "poetry/", "projects/")):
        return "legacy/" + raw
    return None


def _collect_used_sources() -> tuple[set[str], set[str]]:
    used_paths: set[str] = set()
    used_names: set[str] = set()
    if MANIFEST.exists():
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        for work_entry in (manifest.get("by_work") or {}).values():
            if not isinstance(work_entry, dict):
                continue
            sources = work_entry.get("sources")
            if isinstance(sources, dict):
                for entries in sources.values():
                    if not isinstance(entries, list):
                        continue
                    for entry in entries:
                        if isinstance(entry, dict):
                            path = entry.get("path")
                            filename = entry.get("filename")
                            if isinstance(path, str):
                                used_paths.add(path)
                            if isinstance(filename, str):
                                used_names.add(filename)
                        elif isinstance(entry, str):
                            rel = _legacy_rel_from_source_url(entry)
                            if rel:
                                used_paths.add(rel)
                            else:
                                used_names.add(entry)

    # Source provenance lives in the central manifest only — the former in-bundle
    # meta.json fallback was removed with the move to manifest-only provenance
    # (this audit is local-only and retires with legacy/).
    return used_paths, used_names


def main() -> int:
    used_paths, used_names = _collect_used_sources()
    unmapped: list[Path] = []
    for docx in LEGACY.rglob("*.docx"):
        if docx.name.startswith("~$"):
            continue
        if docx.name.endswith(ALLOWED_SKIP_SUFFIXES):
            continue
        if docx.name in ALLOWED_SKIP_NAMES:
            continue
        rel = docx.relative_to(ROOT).as_posix()
        if rel in used_paths:
            continue
        # Legacy projects all use `source.docx`; if running against older
        # provenance without path-level manifest sources, check the matching
        # project bundle instead of treating the shared basename as globally
        # meaningful.
        if docx.parent.parent.name == "projects":
            slug = docx.parent.name
            if (CONTENT / "projects" / slug).is_dir():
                continue
        if docx.name in used_names:
            continue
        unmapped.append(docx)
    if unmapped:
        print(f"FAIL: {len(unmapped)} unmapped source docx files", file=sys.stderr)
        for p in unmapped:
            print(f"  {p.relative_to(ROOT)}", file=sys.stderr)
        return 1
    print(f"PASS: every source docx maps to generated provenance (modulo {len(ALLOWED_SKIP_NAMES)} explicit skip + draft suffixes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
