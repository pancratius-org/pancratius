"""Tag localization consistency.

Tags are per-entry, language-bound (like title/description): a Russian entry
carries the normalized canonical tag key, its English translation carries the
English label. The canonical RU↔EN mapping lives in `data/tag-glossary.json`.

This check fails when an entry (or a video playlist title used as a tag) carries
a tag that is NOT a known label for its locale — which is exactly how Russian
leaks onto an English page, or how a re-cased / drifted tag splinters the
per-locale filter into duplicate chips for one concept.

Respects PANCRATIUS_AUDIT_ROOT (fixture tree) and falls back to the repo root.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import yaml

FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---", re.S)


def _root() -> Path:
    env = os.environ.get("PANCRATIUS_AUDIT_ROOT")
    return Path(env).resolve() if env else Path(__file__).resolve().parents[1]


def main() -> int:
    root = _root()
    glossary_path = root / "data" / "tag-glossary.json"
    if not glossary_path.exists():
        print(f"FAIL: missing {glossary_path}", file=sys.stderr)
        return 1
    glossary = json.loads(glossary_path.read_text(encoding="utf-8"))
    valid = {
        "ru": set(glossary.get("ru", {}).keys()),
        "en": set(glossary.get("en", {}).values()),
    }

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
        if not isinstance(fm, dict) or fm.get("kind") not in ("book", "video", "message"):
            continue
        allowed = valid[md.stem]
        used = list(fm.get("tags") or [])
        used += [p["title"] for p in (fm.get("playlists") or []) if isinstance(p, dict) and p.get("title")]
        for tag in used:
            if tag not in allowed:
                bad.append((str(md.relative_to(root)), md.stem, str(tag)))

    if bad:
        print(f"FAIL: {len(bad)} tag(s) not in the glossary for their locale:", file=sys.stderr)
        for rel, lang, tag in bad[:40]:
            print(f"  {rel} [{lang}]: {tag!r}", file=sys.stderr)
        if len(bad) > 40:
            print(f"  … {len(bad) - 40} more", file=sys.stderr)
        print(
            "Add the canonical RU key + EN label to data/tag-glossary.json, "
            "then normalize the entry's tags/playlist titles to match.",
            file=sys.stderr,
        )
        return 1

    print(f"PASS: tags consistent with glossary ({len(valid['ru'])} RU keys / {len(valid['en'])} EN labels)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
