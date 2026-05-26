"""EN frontmatter uses the current content schema.

An EN title may be a localized title or an honest RU-title fallback; that is an
editorial state, not a schema flag. This audit rejects legacy
`title_is_untranslated` fields and reports title-language
counts for review.
"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CONTENT = ROOT / "src" / "content"


def is_majority_latin(s: str) -> bool:
    lat = sum(1 for c in s if c.isascii() and c.isalpha())
    cyr = sum(1 for c in s if "Ѐ" <= c <= "ӿ")
    if lat + cyr == 0:
        return True
    return lat >= cyr


def main() -> int:
    stale_flags: list[Path] = []
    ru_fallbacks: list[tuple[Path, str]] = []
    checked = 0
    for md in CONTENT.rglob("en.md"):
        text = md.read_text(encoding="utf-8")
        if not text.startswith("---"):
            continue
        end = text.find("\n---", 3)
        if end < 0:
            continue
        fm = yaml.safe_load(text[4:end]) or {}
        title = str(fm.get("title") or "")
        if not title:
            continue
        checked += 1
        if "title_is_untranslated" in fm:
            stale_flags.append(md)
        if not is_majority_latin(title):
            ru_fallbacks.append((md, title))
    print(f"checked {checked} en.md files")
    if stale_flags:
        print(f"FAIL: {len(stale_flags)} EN entries still carry legacy title_is_untranslated", file=sys.stderr)
        for md in stale_flags[:15]:
            print(f"  {md.relative_to(ROOT)}", file=sys.stderr)
        return 1
    if ru_fallbacks:
        print(f"note: {len(ru_fallbacks)} EN titles are RU fallback titles")
        for md, title in ru_fallbacks[:10]:
            print(f"  {md.relative_to(ROOT)} title={title!r}")
    print("PASS: no stale title fallback schema fields")
    return 0


if __name__ == "__main__":
    sys.exit(main())
