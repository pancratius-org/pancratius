from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from html import unescape
from pathlib import Path
from typing import Any, cast

from pancratius.content_catalog import split_frontmatter
from pancratius.pandoc import pandoc_argv0
from pancratius.translation.docx.align import (
    merge_adjacent_runs,
    normalize_transfer_text,
    split_inlines_on_newlines,
)
from pancratius.translation.docx.models import (
    DocxTranslationError,
    FootnoteAnchor,
    MarkdownCoverImage,
    MarkdownFootnoteDefinition,
    MarkdownTransferDocument,
    MarkdownTransferUnit,
    TranslatedInline,
    TranslatedTextRun,
)
from pancratius.writeplan import Diagnostic

PANDOC_TIMEOUT_SECONDS = 300
PANDOC_MARKDOWN_FORMAT = "gfm+footnotes+raw_html+yaml_metadata_block"
FOOTNOTE_DEFINITION_RE = re.compile(r"^\[\^([^\]]+)\]:\s?(.*)$")
FOOTNOTE_REFERENCE_RE = re.compile(r"\[\^([^\]]+)\]")
IMAGE_MEDIA_TYPES: dict[str, str] = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}


def _node(value: object) -> dict[str, Any] | None:
    return cast("dict[str, Any]", value) if isinstance(value, dict) else None


def _content(value: dict[str, Any]) -> object:
    return value.get("c")


def _tag(value: dict[str, Any]) -> str:
    return str(value.get("t") or "")


@dataclass(slots=True)
class _MarkdownProjector:
    """Project Pandoc JSON into paragraph-shaped transfer units."""

    footnote_definitions: dict[str, MarkdownFootnoteDefinition] = field(default_factory=dict)
    footnotes: list[tuple[MarkdownTransferUnit, ...]] = field(default_factory=list)
    manual_footnote_ordinals: dict[str, int] = field(default_factory=dict)
    unsupported: list[Diagnostic] = field(default_factory=list)

    def project(self, ast: dict[str, Any]) -> MarkdownTransferDocument:
        units = tuple(self._blocks(cast("list[object]", ast.get("blocks") or []), lineated=False))
        return MarkdownTransferDocument(
            units=units,
            footnotes=tuple(self.footnotes),
            unsupported=tuple(self.unsupported),
        )

    def _blocks(self, blocks: Sequence[object], *, lineated: bool) -> Iterator[MarkdownTransferUnit]:
        in_lineated = lineated
        lineated_stanza_seen = False
        for raw in blocks:
            block = _node(raw)
            if block is None:
                continue
            tag = _tag(block)
            content = _content(block)

            if tag == "RawBlock" and self._is_lineated_open(content):
                in_lineated = True
                lineated_stanza_seen = False
                continue
            if tag == "RawBlock" and self._is_lineated_close(content):
                in_lineated = False
                continue

            if tag == "RawBlock":
                yield from self._raw_html_units(content)
                continue

            if tag == "Header":
                level, _attrs, inlines = cast("list[Any]", content)
                yield MarkdownTransferUnit(
                    "heading",
                    self._inline_runs(cast("list[object]", inlines)),
                    heading_level=int(level),
                )
            elif tag in {"Para", "Plain"}:
                inlines = cast("list[object]", content)
                if in_lineated:
                    if lineated_stanza_seen:
                        yield MarkdownTransferUnit("blank", source_note="lineated stanza break")
                    yield from self._lineated_units(inlines)
                    lineated_stanza_seen = True
                else:
                    unit = self._paragraph_unit(inlines)
                    if unit is not None:
                        yield unit
            elif tag == "HorizontalRule":
                yield MarkdownTransferUnit("thematic")
            elif tag == "BlockQuote":
                yield from self._blockquote_units(cast("list[object]", content), lineated=in_lineated)
            elif tag in {"BulletList", "OrderedList"}:
                yield from self._list_units(block)
            elif tag == "Table":
                yield from self._table_units(block)
            elif tag == "CodeBlock":
                attrs, code = cast("list[Any]", content)
                del attrs
                yield MarkdownTransferUnit("paragraph", (TranslatedTextRun(str(code)),))
            elif tag == "Div":
                div_content = cast("list[Any]", content)
                attrs = cast("list[Any]", div_content[0])
                classes = set(cast("list[str]", attrs[1]))
                child_blocks = cast("list[object]", div_content[1])
                yield from self._blocks(child_blocks, lineated=("lineated" in classes or in_lineated))
            elif tag == "Null":
                continue
            else:
                self.unsupported.append(Diagnostic(
                    "fatal",
                    "docx-translate.unknown-block",
                    f"unsupported Pandoc block {tag!r}; refusing lossy DOCX text transfer.",
                ))

    @staticmethod
    def _is_lineated_open(content: object) -> bool:
        if not isinstance(content, list) or len(content) != 2:
            return False
        fmt, html = content
        return fmt == "html" and isinstance(html, str) and bool(re.search(
            r"<div\b[^>]*\bclass=[\"'][^\"']*\blineated\b", html
        ))

    @staticmethod
    def _is_lineated_close(content: object) -> bool:
        return (
            isinstance(content, list)
            and len(content) == 2
            and content[0] == "html"
            and isinstance(content[1], str)
            and bool(re.search(r"</div\s*>", content[1]))
        )

    def _raw_html_units(self, content: object) -> Iterator[MarkdownTransferUnit]:
        if not isinstance(content, list) or len(content) != 2 or content[0] != "html":
            return
        html = str(content[1])
        if "class=\"epigraph\"" in html or "class='epigraph'" in html:
            for line in self._html_text_lines(html):
                yield MarkdownTransferUnit("paragraph", self._text_line_inlines(line))
            return
        p_match = re.fullmatch(
            r"\s*<p\b[^>]*class=[\"']signature[\"'][^>]*>(.*?)</p>\s*",
            html,
            flags=re.DOTALL | re.IGNORECASE,
        )
        if p_match:
            text = re.sub(r"<[^>]+>", "", p_match.group(1))
            for line in unescape(text).splitlines():
                stripped = line.strip()
                if stripped:
                    yield MarkdownTransferUnit("paragraph", (TranslatedTextRun(stripped),))
            return
        if self._is_safe_structural_html(html):
            return
        if html.strip():
            self.unsupported.append(Diagnostic(
                "fatal",
                "docx-translate.raw-html-skipped",
                f"raw HTML block skipped during DOCX text transfer: {html.strip()[:80]!r}",
            ))

    @staticmethod
    def _html_text_lines(html: str) -> tuple[str, ...]:
        text = re.sub(r"</(?:p|footer|div|blockquote)>\s*", "\n", html, flags=re.IGNORECASE)
        text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
        text = re.sub(r"<[^>]+>", "", text)
        return tuple(line.strip() for line in unescape(text).splitlines() if line.strip())

    def _text_line_inlines(self, line: str) -> tuple[TranslatedInline, ...]:
        if "[^" not in line:
            return (TranslatedTextRun(line),)
        out: list[TranslatedInline] = []
        cursor = 0
        for match in FOOTNOTE_REFERENCE_RE.finditer(line):
            if match.start() > cursor:
                out.append(TranslatedTextRun(line[cursor:match.start()]))
            anchor = self._manual_footnote_anchor(match.group(1))
            if anchor is not None:
                out.append(anchor)
            cursor = match.end()
        if cursor < len(line):
            out.append(TranslatedTextRun(line[cursor:]))
        return merge_adjacent_runs(out)

    def _manual_footnote_anchor(self, label: str) -> FootnoteAnchor | None:
        existing = self.manual_footnote_ordinals.get(label)
        if existing is not None:
            return FootnoteAnchor(existing)
        definition = self.footnote_definitions.get(label)
        if definition is None:
            self.unsupported.append(Diagnostic(
                "fatal",
                "docx-translate.footnote-definition-missing",
                f"footnote reference [^{label}] appears in raw HTML text but has no definition.",
            ))
            return None
        ordinal = len(self.footnotes) + 1
        parsed = _parse_markdown_fragment(definition.markdown)
        self.unsupported.extend(parsed.unsupported)
        self.footnotes.append(parsed.units or (MarkdownTransferUnit("paragraph"),))
        self.manual_footnote_ordinals[label] = ordinal
        return FootnoteAnchor(ordinal)

    @staticmethod
    def _is_safe_structural_html(html: str) -> bool:
        stripped = html.strip()
        return bool(
            re.fullmatch(
                r"<blockquote\b[^>]*\bclass=[\"'][^\"']*\bscripture\b[^\"']*[\"'][^>]*>",
                stripped,
                flags=re.IGNORECASE,
            )
            or re.fullmatch(r"</blockquote\s*>", stripped, flags=re.IGNORECASE)
        )

    def _list_units(self, block: dict[str, Any]) -> Iterator[MarkdownTransferUnit]:
        tag = _tag(block)
        content = _content(block)
        items: list[object]
        if tag == "BulletList":
            items = cast("list[object]", content)
        else:
            ordered = cast("list[Any]", content)
            items = cast("list[object]", ordered[1])
        for item in items:
            yield from self._blocks(cast("list[object]", item), lineated=False)

    def _blockquote_units(
        self,
        blocks: Sequence[object],
        *,
        lineated: bool,
    ) -> Iterator[MarkdownTransferUnit]:
        for raw in blocks:
            block = _node(raw)
            if block is None:
                continue
            tag = _tag(block)
            if tag in {"Para", "Plain"}:
                yield from self._blockquote_line_units(cast("list[object]", _content(block)))
            else:
                yield from self._blocks((raw,), lineated=lineated)

    def _blockquote_line_units(self, inlines: Sequence[object]) -> Iterator[MarkdownTransferUnit]:
        current: list[object] = []
        for inline in inlines:
            node = _node(inline)
            if node is not None and _tag(node) in {"LineBreak", "SoftBreak"}:
                unit = self._paragraph_unit(current)
                if unit is not None:
                    yield unit
                current = []
            else:
                current.append(inline)
        unit = self._paragraph_unit(current)
        if unit is not None:
            yield unit

    def _table_units(self, block: dict[str, Any]) -> Iterator[MarkdownTransferUnit]:
        content = cast("list[Any]", _content(block))
        head = content[3]
        bodies = cast("list[Any]", content[4])
        foot = content[5]
        yield from self._table_part_rows(head)
        for body in bodies:
            # Pandoc 3 table body: [attr, row-head-cols, head-rows, body-rows]
            yield from self._table_rows(cast("list[Any]", body[2]))
            yield from self._table_rows(cast("list[Any]", body[3]))
        yield from self._table_part_rows(foot)

    def _table_part_rows(self, part: object) -> Iterator[MarkdownTransferUnit]:
        if not isinstance(part, list) or len(part) < 2:
            return
        yield from self._table_rows(cast("list[Any]", part[1]))

    def _table_rows(self, rows: Sequence[object]) -> Iterator[MarkdownTransferUnit]:
        for row in rows:
            if not isinstance(row, list) or len(row) < 2:
                continue
            cells = cast("list[Any]", row[1])
            for cell in cells:
                if not isinstance(cell, list) or len(cell) < 5:
                    continue
                blocks = cast("list[object]", cell[4])
                yielded = False
                for unit in self._blocks(blocks, lineated=False):
                    yielded = True
                    yield unit
                if not yielded:
                    yield MarkdownTransferUnit("blank", source_note="empty table cell")

    def _paragraph_unit(self, inlines: Sequence[object]) -> MarkdownTransferUnit | None:
        if self._is_image_only(inlines):
            return MarkdownTransferUnit(
                "image",
                self._image_alt_inlines(inlines),
                source_note="image paragraph",
            )
        runs = self._inline_runs(inlines)
        plain = "".join(run.text for run in runs if isinstance(run, TranslatedTextRun))
        if not runs or not normalize_transfer_text(plain):
            return MarkdownTransferUnit("blank")
        return MarkdownTransferUnit("paragraph", runs)

    def _lineated_units(self, inlines: Sequence[object]) -> Iterator[MarkdownTransferUnit]:
        current: list[object] = []
        for inline in inlines:
            node = _node(inline)
            if node is not None and _tag(node) in {"LineBreak", "SoftBreak"}:
                yield from self._lineated_line(current)
                current = []
            else:
                current.append(inline)
        yield from self._lineated_line(current)

    def _lineated_line(self, inlines: Sequence[object]) -> Iterator[MarkdownTransferUnit]:
        if self._is_image_only(inlines):
            yield MarkdownTransferUnit(
                "image",
                self._image_alt_inlines(inlines),
                source_note="lineated image",
            )
            return
        for line_inlines in split_inlines_on_newlines(self._inline_runs(inlines)):
            plain = "".join(
                run.text for run in line_inlines if isinstance(run, TranslatedTextRun)
            )
            if not normalize_transfer_text(plain):
                yield MarkdownTransferUnit("blank", source_note="lineated blank")
            else:
                yield MarkdownTransferUnit("lineated", line_inlines)

    @staticmethod
    def _is_image_only(inlines: Sequence[object]) -> bool:
        meaningful = [node for node in (_node(inline) for inline in inlines) if node is not None]
        return bool(meaningful) and all(_tag(node) in {"Image", "Space", "SoftBreak"} for node in meaningful)

    def _image_alt_inlines(self, inlines: Sequence[object]) -> tuple[TranslatedInline, ...]:
        out: list[TranslatedInline] = []
        for raw in inlines:
            node = _node(raw)
            if node is None:
                continue
            tag = _tag(node)
            content = _content(node)
            if tag == "Image":
                _attrs, label, _target = cast("list[Any]", content)
                out.extend(self._inline_runs(cast("list[object]", label)))
            elif tag in {"Space", "SoftBreak"} and out:
                out.append(TranslatedTextRun(" "))
        return merge_adjacent_runs(out)

    def _inline_runs(
        self,
        inlines: Sequence[object],
        *,
        strong: bool = False,
        emphasis: bool = False,
        link_target: str | None = None,
    ) -> tuple[TranslatedInline, ...]:
        out: list[TranslatedInline] = []
        for raw in inlines:
            node = _node(raw)
            if node is None:
                continue
            tag = _tag(node)
            content = _content(node)
            match tag:
                case "Str":
                    out.append(TranslatedTextRun(
                        str(content),
                        strong=strong,
                        emphasis=emphasis,
                        link_target=link_target,
                    ))
                case "Space" | "SoftBreak":
                    out.append(TranslatedTextRun(
                        " ",
                        strong=strong,
                        emphasis=emphasis,
                        link_target=link_target,
                    ))
                case "LineBreak":
                    out.append(TranslatedTextRun(
                        "\n",
                        strong=strong,
                        emphasis=emphasis,
                        link_target=link_target,
                    ))
                case "Strong":
                    out.extend(self._inline_runs(
                        cast("list[object]", content),
                        strong=True,
                        emphasis=emphasis,
                        link_target=link_target,
                    ))
                case "Emph":
                    out.extend(self._inline_runs(
                        cast("list[object]", content),
                        strong=strong,
                        emphasis=True,
                        link_target=link_target,
                    ))
                case "Strikeout" | "Superscript" | "Subscript" | "SmallCaps":
                    out.extend(self._inline_runs(
                        cast("list[object]", content),
                        strong=strong,
                        emphasis=emphasis,
                        link_target=link_target,
                    ))
                    self.unsupported.append(Diagnostic(
                        "fatal",
                        "docx-translate.inline-style-lossy",
                        f"unsupported Pandoc inline style {tag!r}; refusing lossy DOCX text transfer.",
                    ))
                case "Quoted":
                    quote_kind, quoted = cast("list[Any]", content)
                    open_mark, close_mark = ("'", "'") if quote_kind == "SingleQuote" else ("\"", "\"")
                    out.append(TranslatedTextRun(
                        open_mark,
                        strong=strong,
                        emphasis=emphasis,
                        link_target=link_target,
                    ))
                    out.extend(self._inline_runs(
                        cast("list[object]", quoted),
                        strong=strong,
                        emphasis=emphasis,
                        link_target=link_target,
                    ))
                    out.append(TranslatedTextRun(
                        close_mark,
                        strong=strong,
                        emphasis=emphasis,
                        link_target=link_target,
                    ))
                case "Code":
                    _attrs, code = cast("list[Any]", content)
                    out.append(TranslatedTextRun(
                        str(code),
                        strong=strong,
                        emphasis=emphasis,
                        link_target=link_target,
                    ))
                case "Link":
                    _attrs, label, target = cast("list[Any]", content)
                    target_url = str(target[0]) if isinstance(target, list) and target else ""
                    out.extend(self._inline_runs(
                        cast("list[object]", label),
                        strong=strong,
                        emphasis=emphasis,
                        link_target=target_url or link_target,
                    ))
                case "Image":
                    _attrs, label, _target = cast("list[Any]", content)
                    out.extend(self._inline_runs(cast("list[object]", label), strong=strong, emphasis=emphasis))
                    self.unsupported.append(Diagnostic(
                        "fatal",
                        "docx-translate.inline-image-lossy",
                        "inline image recreation is not implemented; refusing lossy DOCX text transfer.",
                    ))
                case "Note":
                    ordinal = len(self.footnotes) + 1
                    note_units = tuple(self._blocks(cast("list[object]", content), lineated=False))
                    self.footnotes.append(note_units or (MarkdownTransferUnit("paragraph"),))
                    out.append(FootnoteAnchor(ordinal))
                case "RawInline":
                    raw = cast("list[Any]", content)
                    if len(raw) == 2 and raw[0] == "html":
                        html = str(raw[1])
                        stripped = html.strip()
                        if re.fullmatch(r"<br\s*/?>", stripped, flags=re.IGNORECASE):
                            out.append(TranslatedTextRun(
                                "\n",
                                strong=strong,
                                emphasis=emphasis,
                                link_target=link_target,
                            ))
                        elif not re.search(r"</?\w", stripped):
                            text = unescape(html)
                            if text:
                                out.append(TranslatedTextRun(
                                    text,
                                    strong=strong,
                                    emphasis=emphasis,
                                    link_target=link_target,
                                ))
                        elif stripped:
                            self.unsupported.append(Diagnostic(
                                "fatal",
                                "docx-translate.raw-inline-html-skipped",
                                f"raw inline HTML is not supported for DOCX text transfer: {stripped[:80]!r}",
                            ))
                case "Math":
                    _math_type, text = cast("list[Any]", content)
                    out.append(TranslatedTextRun(
                        str(text),
                        strong=strong,
                        emphasis=emphasis,
                        link_target=link_target,
                    ))
                    self.unsupported.append(Diagnostic(
                        "fatal",
                        "docx-translate.math-text-only",
                        "math recreation is not implemented; refusing text-only DOCX transfer.",
                    ))
                case "Span":
                    _attrs, children = cast("list[Any]", content)
                    out.extend(self._inline_runs(
                        cast("list[object]", children),
                        strong=strong,
                        emphasis=emphasis,
                        link_target=link_target,
                    ))
                case _:
                    self.unsupported.append(Diagnostic(
                        "fatal",
                        "docx-translate.unknown-inline",
                        f"unsupported Pandoc inline {tag!r}; refusing lossy DOCX text transfer.",
                    ))
        return merge_adjacent_runs(out)


def _extract_footnote_definitions(markdown: str) -> dict[str, MarkdownFootnoteDefinition]:
    definitions: dict[str, MarkdownFootnoteDefinition] = {}
    lines = markdown.splitlines()
    index = 0
    while index < len(lines):
        match = FOOTNOTE_DEFINITION_RE.match(lines[index])
        if match is None:
            index += 1
            continue
        label = match.group(1)
        body: list[str] = [match.group(2)]
        index += 1
        while index < len(lines):
            line = lines[index]
            if FOOTNOTE_DEFINITION_RE.match(line):
                break
            if line.startswith("    "):
                body.append(line[4:])
                index += 1
                continue
            if line.startswith("\t"):
                body.append(line[1:])
                index += 1
                continue
            if not line.strip() and index + 1 < len(lines) and (
                lines[index + 1].startswith("    ") or lines[index + 1].startswith("\t")
            ):
                body.append("")
                index += 1
                continue
            break
        definitions[label] = MarkdownFootnoteDefinition(
            label=label,
            markdown="\n".join(body).strip(),
        )
    return definitions


def _parse_markdown_fragment(markdown: str) -> MarkdownTransferDocument:
    proc = subprocess.run(
        [
            pandoc_argv0(),
            "--from",
            PANDOC_MARKDOWN_FORMAT,
            "--to",
            "json",
        ],
        input=markdown,
        capture_output=True,
        text=True,
        timeout=PANDOC_TIMEOUT_SECONDS,
        check=False,
    )
    if proc.returncode != 0:
        raise DocxTranslationError(f"pandoc failed on Markdown footnote fragment: {proc.stderr.strip()}")
    return _MarkdownProjector().project(json.loads(proc.stdout))


def parse_markdown_transfer(md: Path) -> MarkdownTransferDocument:
    markdown = md.read_text(encoding="utf-8")
    cmd = [
        pandoc_argv0(),
        "--from",
        PANDOC_MARKDOWN_FORMAT,
        "--to",
        "json",
        str(md),
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=PANDOC_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise DocxTranslationError(f"pandoc timed out after {PANDOC_TIMEOUT_SECONDS}s on {md}") from exc
    if proc.returncode != 0:
        raise DocxTranslationError(f"pandoc failed on {md}: {proc.stderr.strip()}")
    return _MarkdownProjector(
        footnote_definitions=_extract_footnote_definitions(markdown),
    ).project(json.loads(proc.stdout))


def markdown_cover_image(md: Path) -> MarkdownCoverImage | None:
    frontmatter, _body = split_frontmatter(md.read_text(encoding="utf-8"))
    cover = frontmatter.get("cover")
    if not isinstance(cover, str) or not cover:
        return None
    path = (md.parent / cover).resolve()
    media_type = IMAGE_MEDIA_TYPES.get(path.suffix.casefold())
    if media_type is None or not path.is_file():
        return None
    return MarkdownCoverImage(path=path, media_type=media_type)
