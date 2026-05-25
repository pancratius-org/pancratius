#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["pyyaml>=6"]
# ///

"""Emit ``data/slug-map.json`` from the content collections.

The sitemap integration in ``astro.config.ts`` cannot import from
``astro:content`` (Astro's config runs in a different context), so the
``(kind, number)`` ↔ per-language-slug pairing is precomputed here as a
build-pipeline artefact. Run before ``astro build``.

Output shape::

    {
      "generated_at": "2026-05-17T22:57:39Z",
      "works": [
        {
          "kind": "book",
          "number": 1,
          "languages": {
            "ru": {"slug": "01-evangelie-tsarstviya",
                   "url":  "/books/01-evangelie-tsarstviya/"},
            "en": {"slug": "01-evangelie-tsarstviya",
                   "url":  "/en/books/01-evangelie-tsarstviya/"}
          }
        },
        ...
      ],
      "pages": [
        {"slug": "about", "languages": {"ru": "/about/"}},
        ...
      ]
    }
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from lib.kinds import SEGMENT_OF  # noqa: E402  (after sys.path bootstrap)
from lib.locales import DEFAULT_LOCALE, LOCALES  # noqa: E402  (after sys.path bootstrap)

REPO_ROOT = _SCRIPT_DIR.parent
CONTENT = REPO_ROOT / "src" / "content"
OUTPUT = REPO_ROOT / "data" / "slug-map.json"

# kind → (folder under src/content/, structural-noun URL segment). The folder
# name and the URL segment are the same value, so both come from SEGMENT_OF.
KIND_DIRS: dict[str, tuple[str, str]] = {
    kind: (segment, segment) for kind, segment in SEGMENT_OF.items()
}


@dataclass(slots=True)
class WorkEntry:
    kind: str
    number: int
    languages: dict[str, dict[str, str]]


def _read_frontmatter(md: Path) -> dict[str, Any]:
    text = md.read_text(encoding="utf-8")
    if not text.startswith("---"):
        raise ValueError(f"{md}: missing frontmatter")
    _, fm, _ = text.split("---", 2)
    data = yaml.safe_load(fm)
    if not isinstance(data, dict):
        raise ValueError(f"{md}: frontmatter is not a mapping")
    return data


def _url(segment: str, slug: str, lang: str) -> str:
    if lang == DEFAULT_LOCALE:
        return f"/{segment}/{slug}/"
    return f"/{lang}/{segment}/{slug}/"


def _page_url(slug: str, lang: str) -> str:
    if lang == DEFAULT_LOCALE:
        return f"/{slug}/"
    return f"/{lang}/{slug}/"


def _collect_works() -> tuple[list[WorkEntry], list[str]]:
    bucket: dict[tuple[str, int], dict[str, dict[str, str]]] = {}
    cross_refs: list[tuple[Path, str, int]] = []  # (md_path, target_kind, target_number)
    for kind, (folder, segment) in KIND_DIRS.items():
        root = CONTENT / folder
        if not root.exists():
            continue
        for md in sorted(root.glob("*/*.md")):
            lang = md.stem
            if lang not in LOCALES:
                continue
            fm = _read_frontmatter(md)
            if fm.get("kind") != kind:
                raise ValueError(f"{md}: kind {fm.get('kind')!r} ≠ collection {kind!r}")
            number = fm.get("number")
            slug = fm.get("slug")
            if not isinstance(number, int) or not isinstance(slug, str):
                raise ValueError(f"{md}: missing number/slug")
            key = (kind, number)
            bucket.setdefault(key, {})[lang] = {
                "slug": slug,
                "url":  _url(segment, slug, lang),
            }
            for ref in fm.get("cross_refs") or []:
                tgt = ref.get("target") if isinstance(ref, dict) else None
                if not isinstance(tgt, dict):
                    continue
                t_kind = tgt.get("kind")
                t_num = tgt.get("number")
                if isinstance(t_kind, str) and isinstance(t_num, int):
                    cross_refs.append((md, t_kind, t_num))
    known: set[tuple[str, int]] = set(bucket.keys())
    errors: list[str] = []
    for md, t_kind, t_num in cross_refs:
        if (t_kind, t_num) not in known:
            errors.append(
                f"{md.relative_to(REPO_ROOT)}: cross_refs target ({t_kind} #{t_num}) does not exist"
            )
    works = [
        WorkEntry(kind=k, number=n, languages=langs)
        for (k, n), langs in sorted(bucket.items(), key=lambda x: (x[0][0], x[0][1]))
    ]
    return works, errors


def _collect_pages() -> list[dict[str, Any]]:
    root = CONTENT / "pages"
    if not root.exists():
        return []
    bucket: dict[str, dict[str, str]] = {}
    for md in sorted(root.glob("*/*.md")):
        lang = md.stem
        if lang not in LOCALES:
            continue
        fm = _read_frontmatter(md)
        slug = fm.get("slug")
        if not isinstance(slug, str):
            raise ValueError(f"{md}: missing slug")
        bucket.setdefault(slug, {})[lang] = _page_url(slug, lang)
    return [
        {"slug": slug, "languages": langs}
        for slug, langs in sorted(bucket.items())
    ]


def generate_slug_map() -> int:
    works, cross_ref_errors = _collect_works()
    if cross_ref_errors:
        for err in cross_ref_errors:
            print(f"error: {err}", file=sys.stderr)
        print(
            f"slug-map: {len(cross_ref_errors)} dangling cross_refs target(s); aborting.",
            file=sys.stderr,
        )
        return 2
    pages = _collect_pages()
    payload = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "works": [
            {"kind": w.kind, "number": w.number, "languages": w.languages}
            for w in works
        ],
        "pages": pages,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    by_kind: dict[str, int] = {}
    for w in works:
        by_kind[w.kind] = by_kind.get(w.kind, 0) + 1
    summary = ", ".join(f"{k}={n}" for k, n in sorted(by_kind.items()))
    print(f"slug-map: {OUTPUT.relative_to(REPO_ROOT)}  works({summary})  pages={len(pages)}")
    return 0


def main() -> int:
    return generate_slug_map()


if __name__ == "__main__":
    sys.exit(main())
