"""PAN004 — corpus identity uniqueness: no duplicate (kind, number, lang).

A work is paired across languages by (kind, number); the per-language entry is
keyed by lang. `src/lib/works.ts` buckets entries as `bucket[lang] = entry`, so a
SECOND file with the same (kind, number, lang) silently OVERWRITES the first —
no error, just one work vanishing from the corpus (and from downloads, feed,
search, bulk). The zod schema validates each file in isolation and cannot see
this cross-file collision, so it is type-uncaught.

This scans every content Markdown file's frontmatter and flags any (kind, number,
lang) claimed by more than one file. It groups by the frontmatter `kind` itself
(book/poem/project), so it restates nothing: any numbered, language-tagged entry
participates. Honours ``PANCRATIUS_AUDIT_ROOT``; wrapped as PAN004 in
audit/rules/projects.ts.
"""

from __future__ import annotations

import os
import sys
from collections import defaultdict
from pathlib import Path

import yaml


def _audit_root() -> Path:
    env = os.environ.get("PANCRATIUS_AUDIT_ROOT")
    # audit/python/work_identity.py -> repo root is four levels up.
    return Path(env).resolve() if env else Path(__file__).resolve().parents[2]


def _frontmatter(text: str) -> dict[str, object] | None:
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end < 0:
        return None
    try:
        parsed = yaml.safe_load(text[4:end])
    except yaml.YAMLError:
        # Unparseable frontmatter is rejected upstream by the zod schema at build;
        # skip it here rather than crash the identity scan on a stack trace.
        return None
    return parsed if isinstance(parsed, dict) else None


def main() -> int:
    root = _audit_root()
    content = root / "src" / "content"
    if not content.is_dir():
        print(f"PASS: no {content} directory")
        return 0

    # (kind, number, lang) -> the files claiming that identity.
    claims: dict[tuple[object, object, object], list[str]] = defaultdict(list)
    for md in sorted(content.rglob("*.md")):
        fm = _frontmatter(md.read_text(encoding="utf-8"))
        if fm is None:
            continue
        kind, number, lang = fm.get("kind"), fm.get("number"), fm.get("lang")
        # Only entries with a full (kind, number, lang) identity participate;
        # pages (no number) and subpages (weight, not number) are exempt.
        if kind is None or number is None or lang is None:
            continue
        claims[(kind, number, lang)].append(str(md.relative_to(root)))

    duplicates = {key: files for key, files in claims.items() if len(files) > 1}
    if duplicates:
        print("FAIL: duplicate (kind, number, lang) — corpus identity collision", file=sys.stderr)
        for (kind, number, lang), files in sorted(duplicates.items(), key=lambda kv: repr(kv[0])):
            print(f"  ({kind} #{number} {lang}) claimed by {len(files)} files:", file=sys.stderr)
            for f in files:
                print(f"    {f}", file=sys.stderr)
        return 1

    print(f"PASS: {len(claims)} (kind, number, lang) identities, all unique")
    return 0


if __name__ == "__main__":
    sys.exit(main())
