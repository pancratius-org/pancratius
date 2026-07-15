"""No rights boilerplate in Markdown and no restrictions in downloadable DOCX."""

from __future__ import annotations

import re
import sys
from pathlib import Path

from pancratius import docx_source
from pancratius.rights_boilerplate import (
    RightsBoilerplateKind,
    classify_rights_boilerplate_notice,
)

ROOT = Path(__file__).resolve().parents[1]
CONTENT = ROOT / "src" / "content"
DOCX_RESTRICTIONS = frozenset(
    {
        RightsBoilerplateKind.RESERVED_RIGHTS,
        RightsBoilerplateKind.REPRODUCTION_RESTRICTION,
    }
)
_FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})")
_QUOTE_RE = re.compile(r"^\s*>\s?")
_LIST_RE = re.compile(r"^\s*(?:\d+[.)]|[-+*])\s+")
_ATX_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}(?:\s+|$)")
_SETEXT_RE = re.compile(r"^\s{0,3}(?:=+|-+)\s*$")
_HTML_WRAPPER_RE = re.compile(
    r"^<(strong|b|em|i|p)>\s*(.*?)\s*</\1>$",
    re.I | re.S,
)
_EMPHASIS_PAIRS = (("**", "**"), ("__", "__"), ("*", "*"), ("_", "_"))


def _visible_markdown_paragraph(lines: list[str]) -> str:
    text = " ".join(line.strip() for line in lines).strip()
    while True:
        if html := _HTML_WRAPPER_RE.fullmatch(text):
            text = html.group(2).strip()
            continue
        inner = next(
            (
                text[len(opening):-len(closing)].strip()
                for opening, closing in _EMPHASIS_PAIRS
                if text.startswith(opening)
                and text.endswith(closing)
                and len(text) > len(opening) + len(closing)
            ),
            None,
        )
        if inner is None:
            return text
        text = inner


def markdown_notices(markdown: str) -> tuple[tuple[int, str], ...]:
    """Standalone notice paragraphs after removing target-format presentation."""
    lines = markdown.splitlines()
    frontmatter_end = 0
    if lines and lines[0].strip() == "---":
        frontmatter_end = next(
            (index + 1 for index, line in enumerate(lines[1:], start=1) if line.strip() == "---"),
            len(lines),
        )

    hits: list[tuple[int, str]] = []
    paragraph: list[str] = []
    paragraph_line = 0
    paragraph_kind = ""
    fence: tuple[str, int] | None = None

    def flush() -> None:
        nonlocal paragraph, paragraph_line, paragraph_kind
        if paragraph:
            visible = _visible_markdown_paragraph(paragraph)
            if classify_rights_boilerplate_notice(visible) is not None:
                hits.append((paragraph_line, visible))
        paragraph = []
        paragraph_line = 0
        paragraph_kind = ""

    for line_number, raw in enumerate(lines, start=1):
        if line_number <= frontmatter_end:
            continue
        if fence is not None:
            character, width = fence
            if re.fullmatch(rf"\s*{re.escape(character)}{{{width},}}\s*", raw):
                fence = None
            continue
        if match := _FENCE_RE.match(raw):
            flush()
            fence = (match.group(1)[0], len(match.group(1)))
            continue
        if not raw.strip():
            flush()
            continue
        if _SETEXT_RE.fullmatch(raw) and paragraph:
            flush()
            continue
        if not paragraph and (raw.startswith("    ") or raw.startswith("\t")):
            continue

        line = raw
        quoted = False
        while match := _QUOTE_RE.match(line):
            quoted = True
            line = line[match.end():]
        if quoted and not line.strip():
            flush()
            continue
        if heading := _ATX_HEADING_RE.match(line):
            flush()
            line = re.sub(r"\s+#+\s*$", "", line[heading.end():]).strip()
        list_item = _LIST_RE.match(line)
        kind = "list" if list_item else "quote" if quoted else "paragraph"
        if list_item:
            line = line[list_item.end():]
        elif paragraph_kind in {"list", "quote"} and not quoted:
            kind = paragraph_kind

        if paragraph and (
            kind != paragraph_kind
            or list_item is not None
        ):
            flush()
        if not paragraph:
            paragraph_line = line_number
            paragraph_kind = kind
        paragraph.append(line)
    flush()
    return tuple(hits)


def docx_restrictions(docx: Path) -> tuple[tuple[int, str], ...]:
    """Restrictive standalone notices in every selected body-story paragraph."""
    hits: list[tuple[int, str]] = []
    contents = docx_source.read_story(docx, docx_source.StoryPart.DOCUMENT)
    for position, content in enumerate(contents):
        text = content.reading.strip()
        if classify_rights_boilerplate_notice(text) in DOCX_RESTRICTIONS:
            hits.append((position, text))
    return tuple(hits)


def main() -> int:
    failures: list[str] = []
    for md in CONTENT.rglob("*.md"):
        text = md.read_text(encoding="utf-8")
        for line, paragraph in markdown_notices(text):
            failures.append(f"{md.relative_to(ROOT)}:{line} {paragraph!r}")
    for docx in CONTENT.rglob("*.docx"):
        try:
            hits = docx_restrictions(docx)
        except docx_source.DocxSourceError as exc:
            failures.append(f"{docx.relative_to(ROOT)}: cannot inspect source: {exc}")
            continue
        for position, text in hits:
            failures.append(
                f"{docx.relative_to(ROOT)}:story paragraph {position} {text!r}"
            )
    if failures:
        print(f"FAIL: {len(failures)} rights-boilerplate hits", file=sys.stderr)
        for f in failures[:25]:
            print(f"  {f}", file=sys.stderr)
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
