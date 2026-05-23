#!/usr/bin/env -S uv run --quiet
"""PAN007 — every image reference in src/content/ resolves on disk.

Work bundles keep covers as ``./cover.<lang>.<ext>``. Body images live under
``./images/`` and may be converter-imported hashes or human-readable authored
names. Also rejects raw ``<img>`` in work Markdown (use ``![](...)``) and inline
body-image Markdown that doesn't stand on its own line.

Wrapped by the harness as PAN007 (scripts/audit/rules/assets.ts); honours
``PANCRATIUS_AUDIT_ROOT`` so it can run against a fixture. Runs in the project
env (pyyaml is a base dependency) — no PEP-723 header needed.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import yaml


def _audit_root() -> Path:
    env = os.environ.get("PANCRATIUS_AUDIT_ROOT")
    # scripts/audit/python/media_refs.py -> repo root is four levels up.
    return Path(env).resolve() if env else Path(__file__).resolve().parents[3]


ROOT = _audit_root()
CONTENT = ROOT / "src" / "content"
MANIFEST = ROOT / "data" / "conversion-manifest.json"

MD_IMG = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
HTML_IMG = re.compile(r'<img[^>]+src="([^"]+)"', re.IGNORECASE)
BODY_IMG_LINE = re.compile(r"!\[[^\]]*]\(\./images/[^)\s]+(?:\s+\"[^\"]*\")?\)")


def main() -> int:
    referenced = 0
    missing: list[tuple[Path, str]] = []
    raw_img_files: list[Path] = []
    inline_img_lines: list[tuple[Path, int, str]] = []
    for md in CONTENT.rglob("*.md"):
        work_dir = md.parent
        text = md.read_text(encoding="utf-8")
        if text.startswith("---"):
            end = text.find("\n---", 3)
            if end > 0:
                fm = yaml.safe_load(text[4:end]) or {}
                cover = fm.get("cover")
                if cover and isinstance(cover, str):
                    referenced += 1
                    target = (work_dir / cover).resolve()
                    if not target.exists():
                        missing.append((md, cover))
        for m in MD_IMG.finditer(text):
            src = m.group(1).split(' "', 1)[0].strip()
            if src.startswith(("http://", "https://", "data:")):
                continue
            referenced += 1
            target = (work_dir / src).resolve()
            if not target.exists():
                missing.append((md, src))
        for m in HTML_IMG.finditer(text):
            if md.parts[-3] in {"books", "poetry", "projects"}:
                raw_img_files.append(md)
            src = m.group(1)
            if src.startswith(("http://", "https://", "data:")):
                continue
            referenced += 1
            target = (work_dir / src).resolve()
            if not target.exists():
                missing.append((md, src))
        if md.parts[-3] in {"books", "poetry", "projects"}:
            for lineno, line in enumerate(text.splitlines(), start=1):
                if line.lstrip().startswith("|"):
                    continue
                stripped = line.strip()
                if BODY_IMG_LINE.search(stripped) and BODY_IMG_LINE.fullmatch(stripped) is None:
                    inline_img_lines.append((md, lineno, stripped))

    print(f"image refs in content: {referenced} ({referenced - len(missing)} resolved)")
    if MANIFEST.exists():
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        roles = manifest.get("stats", {}).get("role_counts") or {}
        print(f"role counts in manifest: {roles}")

    if missing:
        print(f"FAIL: {len(missing)} unresolved refs", file=sys.stderr)
        for md, ref in missing[:25]:
            print(f"  {md.relative_to(ROOT)} → {ref}", file=sys.stderr)
        return 1
    if raw_img_files:
        print("FAIL: raw <img> tags remain in work Markdown; use ![](...)", file=sys.stderr)
        for md in sorted(set(raw_img_files))[:25]:
            print(f"  {md.relative_to(ROOT)}", file=sys.stderr)
        return 1
    if inline_img_lines:
        print("FAIL: body image Markdown must stand on its own line", file=sys.stderr)
        for md, lineno, line in inline_img_lines[:25]:
            print(f"  {md.relative_to(ROOT)}:{lineno}: {line}", file=sys.stderr)
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
