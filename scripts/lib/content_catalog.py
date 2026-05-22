from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


KIND_DIRS = {
    "book": "books",
    "poem": "poetry",
    "project": "projects",
}

DIR_KINDS = {v: k for k, v in KIND_DIRS.items()}

_FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?", re.DOTALL)


@dataclass(frozen=True)
class CatalogEntry:
    kind: str
    number: int
    slug: str
    title: str
    lang: str
    description: str
    work_key: str
    work_dir: Path
    md_path: Path
    frontmatter: dict[str, Any]


def split_frontmatter(markdown: str) -> tuple[dict[str, Any], str]:
    match = _FRONTMATTER_RE.match(markdown)
    if not match:
        return {}, markdown
    data = yaml.safe_load(match.group(1)) or {}
    if not isinstance(data, dict):
        data = {}
    return data, markdown[match.end():]


def read_frontmatter(path: Path) -> dict[str, Any]:
    fm, _body = split_frontmatter(path.read_text(encoding="utf-8"))
    return fm


def dump_frontmatter(data: dict[str, Any]) -> str:
    body = yaml.safe_dump(
        data,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
        width=10_000,
    ).strip()
    return f"---\n{body}\n---\n\n"


def scan_catalog(content_root: Path) -> list[CatalogEntry]:
    entries: list[CatalogEntry] = []
    for kind, folder in KIND_DIRS.items():
        base = content_root / folder
        if not base.exists():
            continue
        for md_path in sorted(base.glob("*/*.md")):
            fm = read_frontmatter(md_path)
            if not fm:
                continue
            fm_kind = str(fm.get("kind") or kind)
            if fm_kind != kind:
                continue
            try:
                number = int(fm["number"])
            except (KeyError, TypeError, ValueError):
                continue
            lang = str(fm.get("lang") or md_path.stem)
            entries.append(CatalogEntry(
                kind=kind,
                number=number,
                slug=str(fm.get("slug") or md_path.parent.name),
                title=str(fm.get("title") or ""),
                lang=lang,
                description=str(fm.get("description") or ""),
                work_key=md_path.parent.name,
                work_dir=md_path.parent,
                md_path=md_path,
                frontmatter=fm,
            ))
    return entries


def next_number(entries: list[CatalogEntry], kind: str) -> int:
    numbers = [entry.number for entry in entries if entry.kind == kind]
    return max(numbers, default=0) + 1


def find_work_entries(
    entries: list[CatalogEntry],
    work_ref: str,
    kind: str | None = None,
) -> list[CatalogEntry]:
    matches = [
        entry for entry in entries
        if (kind is None or entry.kind == kind)
        and (entry.work_key == work_ref or entry.slug == work_ref)
    ]
    return sorted(matches, key=lambda entry: (entry.kind, entry.work_key, entry.lang))


def normalize_title_key(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def build_title_index(entries: list[CatalogEntry]) -> dict[str, tuple[str, int | None, str | None]]:
    index: dict[str, tuple[str, int | None, str | None]] = {}
    for entry in entries:
        for key in (entry.title, entry.slug, entry.work_key):
            norm = normalize_title_key(key)
            if norm and norm not in index:
                index[norm] = (entry.work_key, entry.number, entry.kind)
    return index

