"""Tag localization consistency.

Tags are per-entry, language-bound (like title/description): a Russian entry
carries the normalized canonical tag key, its English translation carries the
English label. The canonical RU↔EN mapping lives in `data/tag-glossary.yaml`,
together with the YouTube playlist id → canonical key map the video scanner
resolves tags through.

This check fails when an entry carries a tag that is NOT a known label for its
locale — which is exactly how Russian leaks onto an English page, or how a
re-cased / drifted tag splinters the per-locale filter into duplicate chips for
one concept — and when a committed video playlist is unmapped or carries a title
other than its key's label, which is how a mis-mapped id would tag new videos
differently from the ones already on the site.

Respects PANCRATIUS_AUDIT_ROOT (fixture tree) and falls back to the repo root.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import yaml

FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---", re.S)


def _root() -> Path:
    env = os.environ.get("PANCRATIUS_AUDIT_ROOT")
    return Path(env).resolve() if env else Path(__file__).resolve().parents[1]


def _section(glossary: dict[str, object], key: str) -> dict[str, str]:
    raw = glossary.get(key)
    return {str(k): str(v) for k, v in raw.items()} if isinstance(raw, dict) else {}


def main() -> int:
    root = _root()
    glossary_path = root / "data" / "tag-glossary.yaml"
    if not glossary_path.exists():
        print(f"FAIL: missing {glossary_path}", file=sys.stderr)
        return 1
    glossary = yaml.safe_load(glossary_path.read_text(encoding="utf-8")) or {}
    ru = _section(glossary, "ru")
    en = _section(glossary, "en")
    playlists = _section(glossary, "playlists")
    valid = {"ru": set(ru), "en": set(en.values())}
    # The label a playlist's canonical key takes on a page of each locale.
    label = {"ru": {k: k for k in ru}, "en": en}

    glossary_bad = [f"ru: {k!r} must label itself, not {v!r}" for k, v in ru.items() if v != k]
    glossary_bad += [f"playlists: {pid} -> {key!r} is not a ru key" for pid, key in playlists.items() if key not in ru]

    bad: list[tuple[str, str, str]] = []
    for md in sorted((root / "src" / "content").rglob("*.md")):
        if md.name not in ("ru.md", "en.md"):
            continue
        match = FRONTMATTER_RE.match(md.read_text(encoding="utf-8"))
        if not match:
            continue
        try:
            fm = yaml.safe_load(match.group(1)) or {}
        except yaml.YAMLError:
            continue
        # The kinds that carry tags / playlists; poems and projects don't.
        if not isinstance(fm, dict) or fm.get("kind") not in ("book", "video", "message"):
            continue
        lang = md.stem
        rel = str(md.relative_to(root))
        for tag in fm.get("tags") or []:
            if str(tag) not in valid[lang]:
                bad.append((rel, lang, repr(str(tag))))
        for p in fm.get("playlists") or []:
            if not isinstance(p, dict):
                continue
            pid, title = str(p.get("id", "")), p.get("title")
            key = playlists.get(pid)
            if key is None:
                bad.append((rel, lang, f"playlist {pid} is not mapped under playlists:"))
            elif title != label[lang].get(key):
                bad.append((rel, lang, f"playlist {pid} title {title!r} should be {label[lang].get(key)!r}"))

    if glossary_bad or bad:
        print(f"FAIL: {len(glossary_bad)} glossary problem(s), {len(bad)} entry problem(s):", file=sys.stderr)
        for line in glossary_bad:
            print(f"  {line}", file=sys.stderr)
        for rel, lang, what in bad[:40]:
            print(f"  {rel} [{lang}]: {what}", file=sys.stderr)
        if len(bad) > 40:
            print(f"  … {len(bad) - 40} more", file=sys.stderr)
        return 1

    print(
        f"PASS: tags consistent with glossary ({len(valid['ru'])} RU keys / {len(valid['en'])} EN labels"
        f" / {len(playlists)} playlists)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
