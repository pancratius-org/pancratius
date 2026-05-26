# import-pure: no filesystem mutation
"""Extract corpus cross-references from a converted body.

Scans footnote bodies and inline mentions (litres URLs, inline book titles) for
references to other works in the corpus, resolving each through the title index.
Resolved references become frontmatter `cross_refs` entries; unresolved mentions
are dropped as noise. PURE: operates on strings only, no filesystem access.
"""

from __future__ import annotations

import re
from typing import Any

# A title-index hit: `(slug, work-number, kind)`. The index maps a normalized
# litres URL or book title to the work it names; matches `content_catalog`'s
# `build_title_index` return.
type _IndexHit = tuple[str, int | None, str | None]

_FOOTNOTE_LINE = re.compile(r"^\[\^([^\]]+)\]:\s*(.+)$", re.MULTILINE)
_LITRES_URL = re.compile(r"https?://(?:www\.)?litres\.ru/[\w\-/]+")
_INLINE_BOOK_TITLE = re.compile(r"книг[аеу]\s+«([^»]{3,80})»")
_EN_INLINE_BOOK_TITLE = re.compile(r"the\s+book\s+\"([^\"]{3,80})\"", re.IGNORECASE)


def extract_cross_refs(
    md: str,
    own_slug: str,
    title_index: dict[str, _IndexHit],
) -> list[dict[str, Any]]:
    """Scan footnote bodies and inline mentions for references to other works
    in the corpus. Emit `{target: {kind, number}, source, snippet}` entries
    when the reference resolves; drop unresolved mentions (they're noise)."""
    refs: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()

    def push(slug: str | None, number: int | None, kind: str | None, source: str, snippet: str, url: str | None = None) -> None:
        if slug == own_slug or number is None or not kind:
            return
        key = (kind, number)
        if key in seen:
            return
        seen.add(key)
        entry: dict[str, Any] = {
            "target": {"kind": kind, "number": number},
            "source": source,
            "snippet": snippet[:240],
        }
        if url:
            entry["source_url"] = url
        refs.append(entry)

    def lookup(key: str) -> _IndexHit | tuple[None, None, None]:
        return title_index.get(key) or (None, None, None)

    for m in _FOOTNOTE_LINE.finditer(md):
        body = m.group(2)
        for url in _LITRES_URL.findall(body):
            slug, number, kind = lookup(url.rstrip("/").lower())
            push(slug, number, kind, "footnote", body)
        for title_m in _INLINE_BOOK_TITLE.findall(body):
            slug, number, kind = lookup(title_m.lower().strip())
            push(slug, number, kind, "footnote", body)

    for url in _LITRES_URL.findall(md):
        slug, number, kind = lookup(url.rstrip("/").lower())
        push(slug, number, kind, "inline_url", url, url=url)

    for title_m in _INLINE_BOOK_TITLE.findall(md):
        slug, number, kind = lookup(title_m.lower().strip())
        push(slug, number, kind, "inline_title", title_m)

    for title_m in _EN_INLINE_BOOK_TITLE.findall(md):
        slug, number, kind = lookup(title_m.lower().strip())
        push(slug, number, kind, "inline_title", title_m)

    return refs
