#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["pyyaml>=6"]
# ///

"""Post-build asset + attribute pass for converter-emitted body images.

Two jobs, both fixes for the legacy converter's raw-HTML ``<img>`` output:

1. Copy each work-bundle ``images/`` folder into the matching
   ``dist/<segment>/<slug>/images/`` location for every locale present.
   Astro's image pipeline only processes the ``![]()`` markdown form, not
   raw ``<img>`` tags, so without this the rendered HTML 404s.

2. Walk every rendered HTML file in ``dist/`` and add ``alt=""``,
   ``loading="lazy"``, and ``decoding="async"`` to any ``<img>`` lacking
   them. Only safe defaults — never overwrites converter-set attributes.

Long-term: the converter should be rewritten to emit Astro-compatible image
references so this script disappears.
"""

from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
CONTENT   = REPO_ROOT / "content"
DIST      = REPO_ROOT / "dist"

KIND_DIRS: dict[str, str] = {
    "books":    "books",
    "poetry":   "poetry",
    "projects": "projects",
}


def _read_frontmatter(md: Path) -> dict[str, object]:
    text = md.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return {}
    _, fm, _ = text.split("---", 2)
    data = yaml.safe_load(fm)
    return data if isinstance(data, dict) else {}


def _slug_for(md: Path) -> str | None:
    fm = _read_frontmatter(md)
    slug = fm.get("slug")
    return slug if isinstance(slug, str) else None


IMG_RE = re.compile(r"<img\b([^>]*?)/?>", re.IGNORECASE)
ATTR_RE = re.compile(r"\b([a-zA-Z_-]+)\s*=", re.IGNORECASE)


def _normalize_img_attrs(html: str) -> tuple[str, int]:
    """Insert alt="", loading="lazy", decoding="async" where missing."""
    changes = 0

    def repl(match: re.Match[str]) -> str:
        nonlocal changes
        attrs = match.group(1)
        present = {a.group(1).lower() for a in ATTR_RE.finditer(attrs)}
        added: list[str] = []
        if "alt" not in present:
            added.append('alt=""')
        if "loading" not in present:
            added.append('loading="lazy"')
        if "decoding" not in present:
            added.append('decoding="async"')
        if not added:
            return match.group(0)
        changes += 1
        joined = " ".join(added)
        # Preserve original attribute spacing/closure style.
        body = attrs.rstrip()
        sep = "" if body == "" or body.endswith(" ") else " "
        return f"<img{body}{sep}{joined}>"

    new = IMG_RE.sub(repl, html)
    return new, changes


def main() -> int:
    if not DIST.exists():
        print(f"build_copy_body_images: dist/ does not exist; nothing to do", file=sys.stderr)
        return 0

    # Part 1: copy work-bundle images/ folders into dist.
    copied_files = 0
    copied_works = 0
    for folder_name, url_segment in KIND_DIRS.items():
        root = CONTENT / folder_name
        if not root.exists():
            continue
        for work_dir in sorted(root.iterdir()):
            if not work_dir.is_dir():
                continue
            images_dir = work_dir / "images"
            if not images_dir.exists():
                continue
            for md in sorted(work_dir.glob("[re][un].md")):
                lang = md.stem
                if lang not in ("ru", "en"):
                    continue
                slug = _slug_for(md)
                if not slug:
                    continue
                dist_dir = (
                    DIST / url_segment / slug / "images"
                    if lang == "ru"
                    else DIST / "en" / url_segment / slug / "images"
                )
                dist_dir.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(images_dir, dist_dir, dirs_exist_ok=True)
                files_here = sum(1 for _ in dist_dir.rglob("*") if _.is_file())
                copied_files += files_here
                copied_works += 1

    # Part 2: harden <img> tags in every rendered HTML file.
    html_files = 0
    img_patches = 0
    for html_path in DIST.rglob("*.html"):
        original = html_path.read_text(encoding="utf-8")
        updated, n = _normalize_img_attrs(original)
        if n > 0:
            html_path.write_text(updated, encoding="utf-8")
            html_files += 1
            img_patches += n

    print(
        f"build_copy_body_images: "
        f"images copied for {copied_works} work-locales ({copied_files} files); "
        f"img attrs normalized in {html_files} HTML files ({img_patches} tags)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
