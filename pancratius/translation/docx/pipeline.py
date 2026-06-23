from __future__ import annotations

import base64
import copy
import json
import re
import subprocess
import tempfile
import unicodedata
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Iterable, Iterator, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from html import unescape
from itertools import combinations
from pathlib import Path, PurePosixPath
from typing import Any, Literal, assert_never, cast

from pancratius.content_catalog import CatalogEntry, scan_catalog, split_frontmatter
from pancratius.docx_merge import DocxMergeError, validate_docx_package
from pancratius.kinds import SEGMENT_OF
from pancratius.locales import DEFAULT_LOCALE, Locale
from pancratius.ooxml import serialize_relationships, serialize_xml
from pancratius.paths import CONTENT_ROOT
from pancratius.writeplan import CopyOp, Diagnostic, EnsureDirOp, WritePlan
from pancratius.writer import WriteReport
from pancratius.writer import apply as apply_plan

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
XML_NS = "http://www.w3.org/XML/1998/namespace"
W = f"{{{W_NS}}}"
R = f"{{{R_NS}}}"
REL = f"{{{REL_NS}}}"
XML_SPACE = f"{{{XML_NS}}}space"
HYPERLINK_REL_TYPE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink"
FIXED_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)

PANDOC_TIMEOUT_SECONDS = 300
PANDOC_MARKDOWN_FORMAT = "gfm+footnotes+raw_html+yaml_metadata_block"
FOOTNOTE_DEFINITION_RE = re.compile(r"^\[\^([^\]]+)\]:\s?(.*)$")
FOOTNOTE_REFERENCE_RE = re.compile(r"\[\^([^\]]+)\]")
SPURIOUS_CONNECTOR_HYPHEN_RE = re.compile(
    r"\s+[—–‑‐−-]\s*(?=(?:так|как|храм)\b)",
    flags=re.IGNORECASE,
)
SOURCE_CITATION_SUFFIX_RE = re.compile(
    r"(?:\s*(?:Википедия|Wikipedia)\+\d+)+\s*$",
    flags=re.IGNORECASE,
)

IMAGE_MEDIA_TYPES: dict[str, str] = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}

TransferUnitKind = Literal[
    "heading",
    "paragraph",
    "lineated",
    "blank",
    "thematic",
    "image",
]


class DocxTranslationError(Exception):
    """The translated-DOCX transfer cannot proceed."""


@dataclass(frozen=True, slots=True)
class TranslatedTextRun:
    """A run of translated text with the inline emphasis Markdown can prove."""

    text: str
    strong: bool = False
    emphasis: bool = False
    link_target: str | None = None


@dataclass(frozen=True, slots=True)
class FootnoteAnchor:
    """A translated footnote reference in the text stream."""

    ordinal: int


type TranslatedInline = TranslatedTextRun | FootnoteAnchor
TextAlignmentVariantReason = Literal[
    "canonical",
    "docx_omitted_smiley",
    "markdown_literal_style_marker",
    "split_letter_typo",
    "spurious_connector_hyphen",
    "nonbreaking_hyphen_import",
    "source_citation_suffix",
    "colon_before_dash",
    "colon_before_punctuation",
    "terminal_label_colon",
]


@dataclass(frozen=True, slots=True)
class MarkdownTransferUnit:
    """One Markdown unit that should land in one Word paragraph slot."""

    kind: TransferUnitKind
    inlines: tuple[TranslatedInline, ...] = ()
    heading_level: int | None = None
    source_note: str = ""

    @property
    def plain_text(self) -> str:
        return "".join(run.text for run in self.inlines if isinstance(run, TranslatedTextRun))

    @property
    def footnote_anchors(self) -> tuple[int, ...]:
        return tuple(run.ordinal for run in self.inlines if isinstance(run, FootnoteAnchor))


@dataclass(frozen=True, slots=True)
class MarkdownTransferDocument:
    """The source-language or translated Markdown projected into transfer units."""

    units: tuple[MarkdownTransferUnit, ...]
    footnotes: tuple[tuple[MarkdownTransferUnit, ...], ...] = ()
    unsupported: tuple[Diagnostic, ...] = ()


@dataclass(frozen=True, slots=True)
class MarkdownFootnoteDefinition:
    """A labeled Markdown footnote body available to raw HTML text."""

    label: str
    markdown: str


@dataclass(frozen=True, slots=True)
class MarkdownCoverImage:
    """A cover image declared by translated Markdown frontmatter."""

    path: Path
    media_type: str

    @property
    def data_uri(self) -> str:
        encoded = base64.b64encode(self.path.read_bytes()).decode("ascii")
        return f"data:{self.media_type};base64,{encoded}"


@dataclass(frozen=True, slots=True)
class WordTextSlot:
    """A source DOCX paragraph selected by Markdown alignment."""

    ordinal: int
    paragraph: ET.Element
    text: str
    has_drawing: bool
    footnote_refs: tuple[ET.Element, ...]


@dataclass(frozen=True, slots=True)
class TransferAlignment:
    """A proven RU Markdown unit -> source DOCX paragraph mapping."""

    unit_indices: tuple[int, ...]
    slot: WordTextSlot


@dataclass(frozen=True, slots=True)
class IgnoredWordSlot:
    """A source DOCX paragraph intentionally removed because Markdown owns content."""

    slot: WordTextSlot
    reason: Literal["source_toc", "source_back_matter", "source_thematic_separator"]


@dataclass(frozen=True, slots=True)
class SourceDocxAlignmentPlan:
    """The complete source DOCX paragraph plan for translated text transfer."""

    alignments: tuple[TransferAlignment, ...]
    ignored_slots: tuple[IgnoredWordSlot, ...] = ()


@dataclass(frozen=True, slots=True)
class MarkdownUnitPairing:
    """Source Markdown unit index -> translated Markdown unit index.

    ``None`` means the source unit is a structural blank absent from the translation;
    the source DOCX blank paragraph is preserved as blank.
    """

    translated_indices_by_source: tuple[tuple[int, ...] | None, ...]


@dataclass(frozen=True, slots=True)
class BookDocxTranslationTarget:
    """One committed book bundle that can receive a translated DOCX artifact."""

    entry: CatalogEntry
    source_docx: Path
    source_md: Path
    translated_md: Path
    translated_docx: Path


@dataclass(frozen=True, slots=True)
class DocxTranslationReport:
    """One work's translated-DOCX outcome."""

    target: BookDocxTranslationTarget
    write_report: WriteReport
    source_units: int
    translated_units: int
    aligned_units: int
    output: Path | None = None


@dataclass(frozen=True, slots=True)
class DocxTranslationDiscovery:
    """Corpus denominator for a translated-DOCX batch."""

    source_books: int
    eligible: int
    missing: int
    existing: int
    ineligible: int


@dataclass(frozen=True, slots=True)
class DocxTranslationBatch:
    """A batch run across one or more work bundles."""

    reports: tuple[DocxTranslationReport, ...]
    discovery: DocxTranslationDiscovery

    @property
    def failed(self) -> bool:
        return any(
            report.write_report.refused
            or any(diagnostic.severity == "fatal" for diagnostic in report.write_report.diagnostics)
            for report in self.reports
        )


@dataclass(frozen=True, slots=True)
class TextAlignmentVariant:
    """A named, narrow source-MD/source-DOCX text equivalence."""

    reason: TextAlignmentVariantReason
    key: str


def _node(value: object) -> dict[str, Any] | None:
    return cast("dict[str, Any]", value) if isinstance(value, dict) else None


def _content(value: dict[str, Any]) -> object:
    return value.get("c")


def _tag(value: dict[str, Any]) -> str:
    return str(value.get("t") or "")


def _normalize_text(value: str) -> str:
    text = unicodedata.normalize("NFC", value).replace("\u00a0", " ")
    text = text.replace("ё", "е").replace("Ё", "Е")
    text = text.replace("\u200b", "")
    text = text.replace("\u2800", "")
    text = text.translate(str.maketrans({
        "“": "\"",
        "”": "\"",
        "„": "\"",
        "«": "\"",
        "»": "\"",
        "‘": "'",
        "’": "'",
        "—": "-",
        "–": "-",
        "‑": "-",
        "‐": "-",
        "−": "-",
        "₀": "0",
        "₁": "1",
        "₂": "2",
        "₃": "3",
        "₄": "4",
        "₅": "5",
        "₆": "6",
        "₇": "7",
        "₈": "8",
        "₉": "9",
    }))
    text = re.sub(r"\s+", " ", text).strip()
    return text.casefold()


def _spurious_connector_hyphen_variants(raw: str) -> tuple[str, ...]:
    matches = tuple(SPURIOUS_CONNECTOR_HYPHEN_RE.finditer(raw))
    if not matches:
        return ()

    variants: list[str] = []
    subsets: list[tuple[int, ...]] = []
    if len(matches) <= 5:
        for size in range(1, len(matches) + 1):
            subsets.extend(combinations(range(len(matches)), size))
    else:
        subsets.extend((index,) for index in range(len(matches)))
        subsets.append(tuple(range(len(matches))))

    for subset in subsets:
        selected = set(subset)
        pieces: list[str] = []
        cursor = 0
        for index, match in enumerate(matches):
            pieces.append(raw[cursor:match.start()])
            pieces.append(" " if index in selected else match.group(0))
            cursor = match.end()
        pieces.append(raw[cursor:])
        variants.append("".join(pieces))
    return tuple(variants)


def _text_alignment_variants(value: str) -> tuple[TextAlignmentVariant, ...]:
    raw = unicodedata.normalize("NFC", value).replace("\u00a0", " ")
    candidates: list[TextAlignmentVariant] = [
        TextAlignmentVariant("canonical", _normalize_text(raw)),
    ]
    variant_specs: list[tuple[TextAlignmentVariantReason, str]] = [
        ("docx_omitted_smiley", raw.replace("☺", "")),
        ("markdown_literal_style_marker", raw.translate(str.maketrans("", "", "~^*_"))),
        ("split_letter_typo", re.sub(r"\bП\s+ока\b", "Пока", raw)),
        ("nonbreaking_hyphen_import", re.sub(r"(?<=\w)‑(?=\w)", "", raw)),
        ("source_citation_suffix", SOURCE_CITATION_SUFFIX_RE.sub("", raw)),
        ("colon_before_dash", re.sub(r":\s*[—–‑‐−-]", " —", raw)),
        ("colon_before_punctuation", re.sub(r":\s*([,.;!?])", r"\1", raw)),
        ("terminal_label_colon", re.sub(r":\s*$", "", raw)),
    ]
    variant_specs.extend(
        ("spurious_connector_hyphen", candidate)
        for candidate in _spurious_connector_hyphen_variants(raw)
    )
    seen = {candidates[0].key}
    for reason, candidate in variant_specs:
        key = _normalize_text(candidate)
        if key and key not in seen:
            seen.add(key)
            candidates.append(TextAlignmentVariant(reason, key))
    return tuple(candidates)


def _source_docx_text_alignment_variants(value: str) -> tuple[TextAlignmentVariant, ...]:
    raw = unicodedata.normalize("NFC", value).replace("\u00a0", " ")
    candidates = [TextAlignmentVariant("canonical", _normalize_text(raw))]
    key = _normalize_text(SOURCE_CITATION_SUFFIX_RE.sub("", raw))
    if key and key != candidates[0].key:
        candidates.append(TextAlignmentVariant("source_citation_suffix", key))
    return tuple(candidates)


def _texts_match_for_source_alignment(source_text: str, docx_text: str) -> bool:
    docx_keys = {variant.key for variant in _source_docx_text_alignment_variants(docx_text)}
    return any(variant.key in docx_keys for variant in _text_alignment_variants(source_text))


def _is_thematic_slot_text(text: str) -> bool:
    stripped = text.strip()
    return not stripped or bool(re.fullmatch(r"(?:[*]\s*){3,}|(?:[-_]\s*){3,}", stripped))


def _merge_adjacent_runs(inlines: Iterable[TranslatedInline]) -> tuple[TranslatedInline, ...]:
    out: list[TranslatedInline] = []
    for inline in inlines:
        if (
            isinstance(inline, TranslatedTextRun)
            and inline.text
            and out
            and isinstance(out[-1], TranslatedTextRun)
            and out[-1].strong == inline.strong
            and out[-1].emphasis == inline.emphasis
            and out[-1].link_target == inline.link_target
        ):
            prev = cast("TranslatedTextRun", out.pop())
            out.append(TranslatedTextRun(
                prev.text + inline.text,
                strong=inline.strong,
                emphasis=inline.emphasis,
                link_target=inline.link_target,
            ))
        elif isinstance(inline, TranslatedTextRun) and not inline.text:
            continue
        else:
            out.append(inline)
    return tuple(out)


def _split_inlines_on_newlines(
    inlines: Sequence[TranslatedInline],
) -> tuple[tuple[TranslatedInline, ...], ...]:
    lines: list[list[TranslatedInline]] = [[]]
    for inline in inlines:
        if not isinstance(inline, TranslatedTextRun) or "\n" not in inline.text:
            lines[-1].append(inline)
            continue
        chunks = inline.text.split("\n")
        for index, chunk in enumerate(chunks):
            if index:
                lines.append([])
            if chunk:
                lines[-1].append(TranslatedTextRun(
                    chunk,
                    strong=inline.strong,
                    emphasis=inline.emphasis,
                    link_target=inline.link_target,
                ))
    return tuple(_merge_adjacent_runs(line) for line in lines)


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
        return _merge_adjacent_runs(out)

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
        if not runs or not _normalize_text(plain):
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
        for line_inlines in _split_inlines_on_newlines(self._inline_runs(inlines)):
            plain = "".join(
                run.text for run in line_inlines if isinstance(run, TranslatedTextRun)
            )
            if not _normalize_text(plain):
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
        return _merge_adjacent_runs(out)

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
        return _merge_adjacent_runs(out)


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
            "pandoc",
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
        "pandoc",
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


def _markdown_cover_image(md: Path) -> MarkdownCoverImage | None:
    frontmatter, _body = split_frontmatter(md.read_text(encoding="utf-8"))
    cover = frontmatter.get("cover")
    if not isinstance(cover, str) or not cover:
        return None
    path = (md.parent / cover).resolve()
    media_type = IMAGE_MEDIA_TYPES.get(path.suffix.casefold())
    if media_type is None or not path.is_file():
        return None
    return MarkdownCoverImage(path=path, media_type=media_type)


def _word_paragraph_text(p: ET.Element) -> str:
    parts: list[str] = []
    for el in p.iter():
        if el.tag == f"{W}t":
            parts.append(el.text or "")
        elif el.tag in {f"{W}br", f"{W}cr"}:
            parts.append("\n")
        elif el.tag == f"{W}tab":
            parts.append("\t")
    return "".join(parts).strip()


def _word_slots(document_root: ET.Element) -> tuple[WordTextSlot, ...]:
    slots: list[WordTextSlot] = []
    body = document_root.find(f"{W}body")
    if body is None:
        return ()
    for ordinal, p in enumerate(body.iter(f"{W}p")):
        slots.append(WordTextSlot(
            ordinal=ordinal,
            paragraph=p,
            text=_word_paragraph_text(p),
            has_drawing=p.find(f".//{W}drawing") is not None or p.find(f".//{W}pict") is not None,
            footnote_refs=tuple(copy.deepcopy(r) for r in p.findall(f".//{W}footnoteReference/..")),
        ))
    return tuple(slots)


def _unit_matches_slot(unit: MarkdownTransferUnit, slot: WordTextSlot) -> bool:
    if unit.kind == "image":
        return slot.has_drawing and not _slot_has_text(slot)
    if unit.kind == "blank":
        return not slot.has_drawing and not _normalize_text(slot.text)
    if unit.kind == "thematic":
        return _is_thematic_slot_text(slot.text)
    return _texts_match_for_source_alignment(unit.plain_text, slot.text)


def _slot_has_text(slot: WordTextSlot) -> bool:
    return bool(_normalize_text(slot.text))


def _slot_preview(slot: WordTextSlot) -> str:
    text = slot.text.strip() or "<image>" if slot.has_drawing else slot.text.strip() or "<blank>"
    return re.sub(r"\s+", " ", text)[:80]


def _is_toc_line(text: str) -> bool:
    stripped = re.sub(r"\s+", " ", text.strip())
    normalized = _normalize_text(stripped)
    return normalized in {"оглавление", "содержание"} or bool(re.fullmatch(r".+\s+\d+", stripped))


def _is_toc_gap(text_slots: Sequence[WordTextSlot]) -> bool:
    return (
        bool(text_slots)
        and _normalize_text(text_slots[0].text) in {"оглавление", "содержание"}
        and all(_is_toc_line(slot.text) for slot in text_slots)
    )


def _is_back_matter_gap(text_slots: Sequence[WordTextSlot]) -> bool:
    if not text_slots:
        return False
    return _normalize_text(text_slots[0].text) in {"библиография", "копирайт", "контакты"}


def _is_thematic_gap(text_slots: Sequence[WordTextSlot]) -> bool:
    return bool(text_slots) and all(_is_thematic_slot_text(slot.text) for slot in text_slots)


def _ignored_gap_slots(
    slots: Sequence[WordTextSlot],
    *,
    start: int,
    end: int,
    context: str,
) -> tuple[IgnoredWordSlot, ...]:
    gap = slots[start:end]
    text_slots = tuple(slot for slot in gap if _slot_has_text(slot))
    if not text_slots:
        return ()
    if _is_toc_gap(text_slots):
        return tuple(IgnoredWordSlot(slot, "source_toc") for slot in gap)
    if _is_thematic_gap(text_slots):
        return tuple(IgnoredWordSlot(slot, "source_thematic_separator") for slot in gap)
    if context == "after the final RU Markdown unit was aligned" and _is_back_matter_gap(text_slots):
        return tuple(IgnoredWordSlot(slot, "source_back_matter") for slot in gap)
    slot = text_slots[0]
    raise DocxTranslationError(
        f"source DOCX paragraph {slot.ordinal} was skipped {context}: "
        f"{_slot_preview(slot)!r}"
    )


def _join_units(units: Sequence[MarkdownTransferUnit]) -> MarkdownTransferUnit:
    if len(units) == 1:
        return units[0]
    if units[0].kind == "image":
        joined = _join_units(units[1:])
        return MarkdownTransferUnit(
            kind=joined.kind,
            inlines=joined.inlines,
            heading_level=joined.heading_level,
            source_note="joined image and adjacent text in one Word paragraph",
        )
    inlines: list[TranslatedInline] = []
    separator = "\n" if all(unit.kind == "lineated" for unit in units) else " "
    for index, unit in enumerate(units):
        unit_inlines = unit.inlines
        unit_plain = _inline_plain_text(unit_inlines)
        colon_emoticon_continuation = (
            index
            and _inline_plain_text(inlines).rstrip().endswith(":")
            and bool(re.match(r"\s*:[)(]+", unit_plain))
        )
        if (
            index
            and _inline_plain_text(inlines).rstrip().endswith(":")
            and re.fullmatch(r"\s*[,.;!?]\s*", unit_plain)
        ):
            inlines = list(_strip_trailing_colon_from_last_text_run(inlines))
            unit_inlines = _strip_leading_whitespace(unit_inlines)
            unit_plain = _inline_plain_text(unit_inlines)
        elif colon_emoticon_continuation:
            unit_inlines = _strip_leading_whitespace(unit_inlines)
            unit_plain = _inline_plain_text(unit_inlines)
        elif (
            index
            and units[0].plain_text.strip().endswith(":")
            and unit.plain_text.lstrip().startswith(":")
        ):
            unit_inlines = _strip_dialogue_continuation_colon(unit_inlines)
            unit_plain = _inline_plain_text(unit_inlines)
        if (
            index
            and unit_plain
            and not colon_emoticon_continuation
            and not re.fullmatch(r"\s*[,.;!?]\s*", unit_plain)
        ):
            inlines.append(TranslatedTextRun(separator))
        inlines.extend(unit_inlines)
    return MarkdownTransferUnit(
        kind=units[0].kind,
        inlines=_merge_adjacent_runs(inlines),
        heading_level=units[0].heading_level,
        source_note="joined adjacent Markdown units",
    )


def _inline_plain_text(inlines: Sequence[TranslatedInline]) -> str:
    return "".join(inline.text for inline in inlines if isinstance(inline, TranslatedTextRun))


def _strip_leading_whitespace(
    inlines: Sequence[TranslatedInline],
) -> tuple[TranslatedInline, ...]:
    out: list[TranslatedInline] = []
    stripped = False
    for inline in inlines:
        if isinstance(inline, TranslatedTextRun) and not stripped:
            text = inline.text.lstrip()
            stripped = text != inline.text
            if text:
                out.append(TranslatedTextRun(
                    text,
                    strong=inline.strong,
                    emphasis=inline.emphasis,
                    link_target=inline.link_target,
                ))
            continue
        out.append(inline)
    return tuple(out)


def _strip_trailing_colon_from_last_text_run(
    inlines: Sequence[TranslatedInline],
) -> tuple[TranslatedInline, ...]:
    out = list(inlines)
    for index in range(len(out) - 1, -1, -1):
        inline = out[index]
        if not isinstance(inline, TranslatedTextRun):
            continue
        text = re.sub(r":\s*$", "", inline.text)
        if text:
            out[index] = TranslatedTextRun(
                text,
                strong=inline.strong,
                emphasis=inline.emphasis,
                link_target=inline.link_target,
            )
        else:
            out.pop(index)
        break
    return tuple(out)


def _strip_dialogue_continuation_colon(
    inlines: Sequence[TranslatedInline],
) -> tuple[TranslatedInline, ...]:
    out: list[TranslatedInline] = []
    stripped = False
    for inline in inlines:
        if isinstance(inline, TranslatedTextRun) and not stripped:
            text = re.sub(r"^\s*:\s*", "", inline.text, count=1)
            stripped = text != inline.text
            if text:
                out.append(TranslatedTextRun(
                    text,
                    strong=inline.strong,
                    emphasis=inline.emphasis,
                    link_target=inline.link_target,
                ))
            continue
        out.append(inline)
    return tuple(out)


def _can_join_for_word_slot(unit: MarkdownTransferUnit) -> bool:
    return unit.kind in {"paragraph", "heading", "lineated", "image"}


def _units_joinable_for_word_slot(units: Sequence[MarkdownTransferUnit]) -> bool:
    if not units or not all(_can_join_for_word_slot(unit) for unit in units):
        return False
    if units[0].kind == "image":
        return len(units) > 1 and _units_joinable_for_word_slot(units[1:])
    if all(unit.kind == units[0].kind for unit in units):
        return True
    first = units[0].plain_text.strip()
    return (
        units[0].kind == "paragraph"
        and first.endswith(":")
        and all(unit.kind in {"paragraph", "lineated"} for unit in units[1:])
    )


def _matching_join_end(
    units: Sequence[MarkdownTransferUnit],
    unit_cursor: int,
    slot: WordTextSlot,
) -> int | None:
    unit = units[unit_cursor]
    if _unit_matches_slot(unit, slot):
        return unit_cursor + 1
    if not _can_join_for_word_slot(unit):
        return None
    for end in range(unit_cursor + 2, min(len(units), unit_cursor + 12) + 1):
        members = units[unit_cursor:end]
        if not all(_can_join_for_word_slot(member) for member in members):
            break
        if not _units_joinable_for_word_slot(members):
            break
        joined = _join_units(members)
        if _unit_matches_slot(joined, slot):
            return end
    return None


def _can_skip_blank_before_source_structure(
    units: Sequence[MarkdownTransferUnit],
    unit_cursor: int,
    slots: Sequence[WordTextSlot],
    cursor: int,
    *,
    window: int,
) -> bool:
    if unit_cursor + 1 >= len(units):
        return False
    hi = min(len(slots), cursor + window)
    for slot_index in range(cursor, hi):
        if any(_slot_has_text(slot) for slot in slots[cursor:slot_index]):
            return False
        if _matching_join_end(units, unit_cursor + 1, slots[slot_index]) is not None:
            return True
    return False


def _align_source_units(
    source: MarkdownTransferDocument,
    slots: Sequence[WordTextSlot],
) -> SourceDocxAlignmentPlan:
    alignments: list[TransferAlignment] = []
    ignored_slots: list[IgnoredWordSlot] = []
    cursor = 0
    unit_cursor = 0
    window = 500
    while unit_cursor < len(source.units):
        unit = source.units[unit_cursor]
        if (
            unit.kind == "blank"
            and cursor < len(slots)
            and not _unit_matches_slot(unit, slots[cursor])
            and _can_skip_blank_before_source_structure(
                source.units,
                unit_cursor,
                slots,
                cursor,
                window=window,
            )
        ):
            unit_cursor += 1
            continue
        if (
            unit.kind == "blank"
            and cursor < len(slots)
            and not _unit_matches_slot(unit, slots[cursor])
            and unit_cursor + 1 < len(source.units)
            and _matching_join_end(source.units, unit_cursor + 1, slots[cursor]) is not None
        ):
            unit_cursor += 1
            continue
        match_at: int | None = None
        join_to = unit_cursor + 1
        hi = min(len(slots), cursor + window)
        for slot_index in range(cursor, hi):
            matched_join_end = _matching_join_end(source.units, unit_cursor, slots[slot_index])
            if matched_join_end is not None:
                match_at = slot_index
                join_to = matched_join_end
                break
        if match_at is None:
            needle = unit.plain_text or unit.kind
            raise DocxTranslationError(
                f"could not align RU Markdown unit {unit_cursor + 1}/{len(source.units)} "
                f"({unit.kind}: {needle[:80]!r}) to the source DOCX paragraph stream"
            )
        ignored_slots.extend(_ignored_gap_slots(
            slots,
            start=cursor,
            end=match_at,
            context=f"before matching RU Markdown unit {unit_cursor + 1}/{len(source.units)}",
        ))
        alignments.append(TransferAlignment(tuple(range(unit_cursor, join_to)), slots[match_at]))
        cursor = match_at + 1
        unit_cursor = join_to
    ignored_slots.extend(_ignored_gap_slots(
        slots,
        start=cursor,
        end=len(slots),
        context="after the final RU Markdown unit was aligned",
    ))
    return SourceDocxAlignmentPlan(tuple(alignments), tuple(ignored_slots))


def _signature(unit: MarkdownTransferUnit) -> tuple[TransferUnitKind, int | None]:
    return unit.kind, unit.heading_level if unit.kind == "heading" else None


def _pair_markdown_units(
    source: MarkdownTransferDocument,
    translated: MarkdownTransferDocument,
) -> tuple[MarkdownUnitPairing, tuple[Diagnostic, ...]]:
    diagnostics: list[Diagnostic] = [*source.unsupported, *translated.unsupported]
    if len(source.footnotes) != len(translated.footnotes):
        diagnostics.append(Diagnostic(
            "fatal",
            "docx-translate.footnote-count-mismatch",
            f"RU Markdown has {len(source.footnotes)} footnotes; translated Markdown has "
            f"{len(translated.footnotes)}.",
        ))
    translated_indices_by_source: list[tuple[int, ...] | None] = []
    src_index = 0
    dst_index = 0
    while src_index < len(source.units) or dst_index < len(translated.units):
        if src_index >= len(source.units):
            if translated.units[dst_index].kind == "blank":
                dst_index += 1
                continue
            diagnostics.append(Diagnostic(
                "fatal",
                "docx-translate.structure-mismatch",
                f"translated Markdown has extra nonblank transfer unit {dst_index + 1}: "
                f"{_signature(translated.units[dst_index])!r}.",
            ))
            break
        if dst_index >= len(translated.units):
            if source.units[src_index].kind == "blank":
                translated_indices_by_source.append(None)
                src_index += 1
                continue
            diagnostics.append(Diagnostic(
                "fatal",
                "docx-translate.structure-mismatch",
                f"RU Markdown has extra nonblank transfer unit {src_index + 1}: "
                f"{_signature(source.units[src_index])!r}.",
            ))
            break

        src = source.units[src_index]
        dst = translated.units[dst_index]
        if _signature(src) != _signature(dst):
            if src.kind == "blank":
                translated_indices_by_source.append(None)
                src_index += 1
                continue
            if dst.kind == "blank":
                dst_index += 1
                continue
            diagnostics.append(Diagnostic(
                "fatal",
                "docx-translate.structure-mismatch",
                f"transfer unit {src_index + 1}/{dst_index + 1} differs: "
                f"RU {_signature(src)!r}, translated {_signature(dst)!r}.",
            ))
            break
        if len(src.footnote_anchors) != len(dst.footnote_anchors):
            diagnostics.append(Diagnostic(
                "fatal",
                "docx-translate.footnote-anchor-mismatch",
                f"transfer unit {src_index + 1} has {len(src.footnote_anchors)} RU footnote anchors and "
                f"{len(dst.footnote_anchors)} translated anchors.",
            ))
            break
        translated_indices_by_source.append((dst_index,))
        src_index += 1
        dst_index += 1
    return MarkdownUnitPairing(tuple(translated_indices_by_source)), tuple(diagnostics)


@dataclass(slots=True)
class HyperlinkRelationshipAllocator:
    """Creates external hyperlink relationships for one OOXML part."""

    parts: dict[str, bytes]
    rels_part: str
    root: ET.Element = field(init=False)
    source_xml: bytes | None = field(init=False)
    next_id: int = field(init=False)

    def __post_init__(self) -> None:
        if self.rels_part in self.parts:
            self.source_xml = self.parts[self.rels_part]
            self.root = ET.fromstring(self.source_xml)
        else:
            self.source_xml = None
            self.root = ET.Element(f"{REL}Relationships")
        ids: list[int] = []
        for rel in self.root.findall(f"{REL}Relationship"):
            rel_id = str(rel.get("Id") or "")
            match = re.fullmatch(r"rId(\d+)", rel_id)
            if match:
                ids.append(int(match.group(1)))
        self.next_id = max(ids, default=0) + 1

    def add_external_hyperlink(self, target: str) -> str:
        rel_id = f"rId{self.next_id}"
        self.next_id += 1
        rel = ET.SubElement(self.root, f"{REL}Relationship")
        rel.set("Id", rel_id)
        rel.set("Type", HYPERLINK_REL_TYPE)
        rel.set("Target", target)
        rel.set("TargetMode", "External")
        return rel_id

    def save(self) -> None:
        self.parts[self.rels_part] = serialize_relationships(self.root, source_xml=self.source_xml)


def _clone_run_properties(
    base: ET.Element | None,
    *,
    strong: bool,
    emphasis: bool,
    hyperlink: bool = False,
) -> ET.Element | None:
    rpr = copy.deepcopy(base) if base is not None else ET.Element(f"{W}rPr")
    if hyperlink and rpr.find(f"{W}rStyle") is None:
        style = ET.SubElement(rpr, f"{W}rStyle")
        style.set(f"{W}val", "Hyperlink")
    if strong and rpr.find(f"{W}b") is None:
        ET.SubElement(rpr, f"{W}b")
    if emphasis and rpr.find(f"{W}i") is None:
        ET.SubElement(rpr, f"{W}i")
    return rpr if len(rpr) or rpr.attrib else None


def _base_run_properties(p: ET.Element) -> ET.Element | None:
    for r in p.findall(f"{W}r"):
        rpr = r.find(f"{W}rPr")
        if rpr is not None:
            return rpr
    return None


def _text_run(
    text: str,
    base_rpr: ET.Element | None,
    *,
    strong: bool,
    emphasis: bool,
    hyperlink: bool = False,
) -> ET.Element:
    r = ET.Element(f"{W}r")
    rpr = _clone_run_properties(
        base_rpr,
        strong=strong,
        emphasis=emphasis,
        hyperlink=hyperlink,
    )
    if rpr is not None:
        r.append(rpr)
    t = ET.SubElement(r, f"{W}t")
    if text[:1].isspace() or text[-1:].isspace():
        t.set(XML_SPACE, "preserve")
    t.text = text
    return r


def _docx_visible_text(text: str) -> str:
    text = re.sub(r"\^([^\s^]+)\^", r"\1", text)
    return re.sub(r"~([^\s~]+)~", r"\1", text)


def _append_ooxml_run(
    p: ET.Element,
    run: ET.Element,
    *,
    link_target: str | None,
    hyperlinks: HyperlinkRelationshipAllocator | None,
) -> None:
    if not link_target:
        p.append(run)
        return
    if hyperlinks is None:
        raise DocxTranslationError(
            f"cannot create hyperlink relationship for {link_target!r} in this DOCX part"
        )
    hyperlink = ET.Element(f"{W}hyperlink")
    hyperlink.set(f"{R}id", hyperlinks.add_external_hyperlink(link_target))
    hyperlink.append(run)
    p.append(hyperlink)


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _replace_image_metadata(p: ET.Element, unit: MarkdownTransferUnit) -> None:
    alt_text = unit.plain_text.strip()
    for index, docpr in enumerate(
        (element for element in p.iter() if _local_name(element.tag) in {"docPr", "cNvPr"}),
        start=1,
    ):
        if alt_text:
            name = alt_text if index == 1 else f"{alt_text} {index}"
            docpr.set("name", name)
            docpr.set("descr", alt_text)
        else:
            docpr.set("name", f"Drawing {index}")
            docpr.attrib.pop("descr", None)


def _has_cyrillic(value: str) -> bool:
    return bool(re.search(r"[А-Яа-яЁё]", value))


def _sanitize_drawing_metadata(root: ET.Element) -> None:
    for index, docpr in enumerate(
        (element for element in root.iter() if _local_name(element.tag) in {"docPr", "cNvPr"}),
        start=1,
    ):
        name = str(docpr.get("name") or "")
        descr = str(docpr.get("descr") or "")
        if _has_cyrillic(name):
            docpr.set("name", f"Drawing {index}")
        if _has_cyrillic(descr):
            docpr.attrib.pop("descr", None)


def _parent_map(root: ET.Element) -> dict[ET.Element, ET.Element]:
    return {child: parent for parent in root.iter() for child in list(parent)}


def _body_child_for(
    paragraph: ET.Element,
    *,
    body: ET.Element,
    parents: dict[ET.Element, ET.Element],
) -> ET.Element:
    node = paragraph
    while (parent := parents.get(node)) is not None:
        if parent is body:
            return node
        node = parent
    raise DocxTranslationError("ignored source DOCX paragraph is not inside the document body")


def _remove_ignored_word_slots(root: ET.Element, ignored_slots: Sequence[IgnoredWordSlot]) -> None:
    if not ignored_slots:
        return
    body = root.find(f"{W}body")
    if body is None:
        raise DocxTranslationError("source DOCX has no word/body")
    parents = _parent_map(root)
    body_children: list[ET.Element] = []
    seen: set[int] = set()
    for ignored in ignored_slots:
        child = _body_child_for(ignored.slot.paragraph, body=body, parents=parents)
        identity = id(child)
        if identity not in seen:
            seen.add(identity)
            body_children.append(child)
    for child in body_children:
        body.remove(child)


def _replace_embedded_cover_data_uri(
    root: ET.Element,
    cover: MarkdownCoverImage | None,
) -> tuple[Diagnostic, ...]:
    instr_text_nodes = [
        node
        for node in root.iter(f"{W}instrText")
        if node.text and "INCLUDEPICTURE" in node.text and "data:image/" in node.text
    ]
    if not instr_text_nodes:
        return ()
    if cover is None:
        return (Diagnostic(
            "fatal",
            "docx-translate.cover-image-missing",
            "source DOCX contains an embedded cover data URI, but translated Markdown has no usable cover image.",
        ),)
    data_uri = cover.data_uri
    for node in instr_text_nodes:
        node.text = re.sub(
            r'data:image/[^";\s]+;base64,[A-Za-z0-9+/=]+',
            data_uri,
            node.text or "",
        )
    return (Diagnostic(
        "warning",
        "docx-translate.cover-image-replaced",
        f"replaced {len(instr_text_nodes)} embedded source cover image(s) with {cover.path.name}.",
    ),)


def _ignored_slot_diagnostics(ignored_slots: Sequence[IgnoredWordSlot]) -> tuple[Diagnostic, ...]:
    if not ignored_slots:
        return ()
    diagnostics: list[Diagnostic] = []
    group: list[IgnoredWordSlot] = []

    def flush() -> None:
        if not group:
            return
        first = group[0].slot.ordinal
        last = group[-1].slot.ordinal
        ordinal_span = str(first) if first == last else f"{first}-{last}"
        preview_slot = next(
            (ignored.slot for ignored in group if _slot_has_text(ignored.slot)),
            group[0].slot,
        )
        reason = group[0].reason.replace("_", "-")
        diagnostics.append(Diagnostic(
            "warning",
            "docx-translate.source-slot-removed",
            f"removed {len(group)} source-only {reason} DOCX paragraph(s) "
            f"{ordinal_span}: {_slot_preview(preview_slot)!r}",
        ))

    for ignored in ignored_slots:
        if (
            group
            and (
                ignored.reason != group[-1].reason
                or ignored.slot.ordinal != group[-1].slot.ordinal + 1
            )
        ):
            flush()
            group = []
        group.append(ignored)
    flush()
    return tuple(diagnostics)


def _append_translated_inlines(
    p: ET.Element,
    unit: MarkdownTransferUnit,
    *,
    base_rpr: ET.Element | None,
    footnote_refs: Sequence[ET.Element],
    hyperlinks: HyperlinkRelationshipAllocator | None,
) -> None:
    footnote_cursor = 0
    for inline in unit.inlines:
        if isinstance(inline, TranslatedTextRun):
            for index, chunk in enumerate(_docx_visible_text(inline.text).split("\n")):
                if index:
                    br_run = ET.Element(f"{W}r")
                    br = ET.SubElement(br_run, f"{W}br")
                    del br
                    _append_ooxml_run(
                        p,
                        br_run,
                        link_target=inline.link_target,
                        hyperlinks=hyperlinks,
                    )
                if chunk:
                    _append_ooxml_run(
                        p,
                        _text_run(
                            chunk,
                            base_rpr,
                            strong=inline.strong,
                            emphasis=inline.emphasis,
                            hyperlink=inline.link_target is not None,
                        ),
                        link_target=inline.link_target,
                        hyperlinks=hyperlinks,
                    )
        elif isinstance(inline, FootnoteAnchor):
            if footnote_cursor >= len(footnote_refs):
                raise DocxTranslationError(
                    f"paragraph {unit.plain_text[:80]!r} needs more footnote reference runs than source DOCX has"
                )
            p.append(copy.deepcopy(footnote_refs[footnote_cursor]))
            footnote_cursor += 1
        else:
            assert_never(inline)
    if footnote_cursor != len(footnote_refs):
        raise DocxTranslationError(
            f"paragraph {unit.plain_text[:80]!r} used {footnote_cursor} footnote references but source has "
            f"{len(footnote_refs)}"
        )


def _replace_paragraph_text(
    p: ET.Element,
    unit: MarkdownTransferUnit,
    *,
    hyperlinks: HyperlinkRelationshipAllocator | None,
) -> None:
    if unit.kind == "image":
        _replace_image_metadata(p, unit)
        return
    if unit.kind == "thematic":
        return
    base_rpr = _base_run_properties(p)
    footnote_refs = [copy.deepcopy(r) for r in p.findall(f".//{W}footnoteReference/..")]
    drawing_runs = [
        copy.deepcopy(r)
        for r in p.findall(f"{W}r")
        if r.find(f".//{W}drawing") is not None or r.find(f".//{W}pict") is not None
    ]
    for child in list(p):
        if child.tag != f"{W}pPr":
            p.remove(child)
    for run in drawing_runs:
        p.append(run)
    _append_translated_inlines(
        p,
        unit,
        base_rpr=base_rpr,
        footnote_refs=footnote_refs,
        hyperlinks=hyperlinks,
    )


def _unit_has_hyperlink(unit: MarkdownTransferUnit) -> bool:
    return any(
        isinstance(inline, TranslatedTextRun) and inline.link_target
        for inline in unit.inlines
    )


def _run_text(run: ET.Element) -> str:
    return "".join(t.text or "" for t in run.findall(f".//{W}t"))


def _footnote_marker_prefix(template_p: ET.Element | None) -> tuple[ET.Element, ...]:
    if template_p is None:
        return ()
    prefix: list[ET.Element] = []
    marker_seen = False
    for child in list(template_p):
        if child.tag == f"{W}pPr":
            continue
        if child.find(f".//{W}footnoteRef") is not None:
            marker_seen = True
            prefix.append(copy.deepcopy(child))
            continue
        if marker_seen and child.tag == f"{W}r" and not _run_text(child).strip():
            prefix.append(copy.deepcopy(child))
            continue
        break
    return tuple(prefix)


def _footnote_body_run_properties(template_p: ET.Element | None) -> ET.Element | None:
    if template_p is None:
        return None
    for run in template_p.findall(f"{W}r"):
        if run.find(f".//{W}footnoteRef") is not None:
            continue
        if not _run_text(run).strip():
            continue
        rpr = run.find(f"{W}rPr")
        if rpr is not None:
            return rpr
    return _base_run_properties(template_p)


def _replace_footnotes(zf_parts: dict[str, bytes], translated: MarkdownTransferDocument) -> None:
    if not translated.footnotes or "word/footnotes.xml" not in zf_parts:
        return
    footnote_hyperlinks = (
        HyperlinkRelationshipAllocator(zf_parts, "word/_rels/footnotes.xml.rels")
        if any(_unit_has_hyperlink(unit) for footnote in translated.footnotes for unit in footnote)
        else None
    )
    root = ET.fromstring(zf_parts["word/footnotes.xml"])
    notes = [
        note for note in root.findall(f"{W}footnote")
        if int(note.get(f"{W}id", "0")) > 0
    ]
    if len(notes) != len(translated.footnotes):
        raise DocxTranslationError(
            f"source DOCX has {len(notes)} footnote definitions; translated Markdown has "
            f"{len(translated.footnotes)}"
        )
    for note, units in zip(notes, translated.footnotes, strict=True):
        template_p = note.find(f"{W}p")
        template_ppr = copy.deepcopy(template_p.find(f"{W}pPr")) if template_p is not None and template_p.find(f"{W}pPr") is not None else None
        marker_prefix = _footnote_marker_prefix(template_p)
        base_rpr = _footnote_body_run_properties(template_p)
        for child in list(note):
            if child.tag == f"{W}p":
                note.remove(child)
        for index, unit in enumerate(units):
            p = ET.Element(f"{W}p")
            if template_ppr is not None:
                p.append(copy.deepcopy(template_ppr))
            if index == 0:
                for run in marker_prefix:
                    p.append(copy.deepcopy(run))
            _append_translated_inlines(
                p,
                unit,
                base_rpr=base_rpr,
                footnote_refs=(),
                hyperlinks=footnote_hyperlinks,
            )
            note.append(p)
    if footnote_hyperlinks is not None:
        footnote_hyperlinks.save()
    zf_parts["word/footnotes.xml"] = serialize_xml(
        root,
        source_xml=zf_parts["word/footnotes.xml"],
    )


@dataclass(frozen=True, slots=True)
class DocxPackageParts:
    """A DOCX package payload plus the donor member order."""

    parts: dict[str, bytes]
    member_order: tuple[str, ...]


def _copy_docx_parts(source_docx: Path) -> DocxPackageParts:
    try:
        with zipfile.ZipFile(source_docx) as zf:
            bad_member = zf.testzip()
            if bad_member is not None:
                raise DocxTranslationError(f"{source_docx} has a corrupt ZIP member: {bad_member}")
            member_order = tuple(zf.namelist())
            return DocxPackageParts(
                parts={name: zf.read(name) for name in member_order},
                member_order=member_order,
            )
    except (OSError, zipfile.BadZipFile) as exc:
        raise DocxTranslationError(f"{source_docx} is not a valid DOCX package") from exc


def _write_docx_parts(parts: dict[str, bytes], out: Path, *, member_order: Sequence[str]) -> None:
    try:
        seen_order = set(member_order)
        ordered_names = [
            name for name in member_order if name in parts
        ] + sorted(name for name in parts if name not in seen_order)
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
            for name in ordered_names:
                info = zipfile.ZipInfo(name, date_time=FIXED_ZIP_TIMESTAMP)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.create_system = 3
                info.external_attr = 0o644 << 16
                zf.writestr(info, parts[name])
    except (OSError, zipfile.BadZipFile) as exc:
        raise DocxTranslationError(f"could not write DOCX package {out}") from exc


def render_translated_docx(
    *,
    source_docx: Path,
    source_md: Path,
    translated_md: Path,
    out: Path,
) -> tuple[int, int, int, tuple[Diagnostic, ...]]:
    source = parse_markdown_transfer(source_md)
    translated = parse_markdown_transfer(translated_md)
    cover = _markdown_cover_image(translated_md)
    pairing, pair_diags = _pair_markdown_units(source, translated)
    diagnostics = list(pair_diags)
    if any(d.severity == "fatal" for d in diagnostics):
        return len(source.units), len(translated.units), 0, tuple(diagnostics)

    try:
        package = _copy_docx_parts(source_docx)
        parts = dict(package.parts)
        document_root = ET.fromstring(parts["word/document.xml"])
    except DocxTranslationError as exc:
        diagnostics.append(Diagnostic("fatal", "docx-translate.invalid-docx", str(exc)))
        return len(source.units), len(translated.units), 0, tuple(diagnostics)
    except KeyError:
        diagnostics.append(Diagnostic(
            "fatal",
            "docx-translate.invalid-docx",
            f"{source_docx} has no word/document.xml",
        ))
        return len(source.units), len(translated.units), 0, tuple(diagnostics)
    except ET.ParseError as exc:
        diagnostics.append(Diagnostic(
            "fatal",
            "docx-translate.invalid-docx",
            f"{source_docx}:word/document.xml is not well-formed XML: {exc}",
        ))
        return len(source.units), len(translated.units), 0, tuple(diagnostics)

    slots = _word_slots(document_root)
    try:
        alignment_plan = _align_source_units(source, slots)
        diagnostics.extend(_ignored_slot_diagnostics(alignment_plan.ignored_slots))
        document_hyperlinks = (
            HyperlinkRelationshipAllocator(parts, "word/_rels/document.xml.rels")
            if any(_unit_has_hyperlink(unit) for unit in translated.units)
            else None
        )
        for alignment in alignment_plan.alignments:
            translated_members: list[MarkdownTransferUnit] = []
            for source_index in alignment.unit_indices:
                translated_indices = pairing.translated_indices_by_source[source_index]
                if translated_indices is None:
                    translated_members.append(MarkdownTransferUnit("blank"))
                else:
                    translated_members.extend(translated.units[index] for index in translated_indices)
            translated_unit = _join_units(
                tuple(translated_members)
            )
            _replace_paragraph_text(
                alignment.slot.paragraph,
                translated_unit,
                hyperlinks=document_hyperlinks,
            )
        _remove_ignored_word_slots(document_root, alignment_plan.ignored_slots)
        _sanitize_drawing_metadata(document_root)
        diagnostics.extend(_replace_embedded_cover_data_uri(document_root, cover))
        if any(d.severity == "fatal" for d in diagnostics):
            return len(source.units), len(translated.units), 0, tuple(diagnostics)
        if document_hyperlinks is not None:
            document_hyperlinks.save()
        parts["word/document.xml"] = serialize_xml(
            document_root,
            source_xml=package.parts["word/document.xml"],
        )
        _replace_footnotes(parts, translated)
    except DocxTranslationError as exc:
        diagnostics.append(Diagnostic("fatal", "docx-translate.transfer-failed", str(exc)))
        return len(source.units), len(translated.units), 0, tuple(diagnostics)
    except ET.ParseError as exc:
        diagnostics.append(Diagnostic(
            "fatal",
            "docx-translate.invalid-docx",
            f"{source_docx} contains malformed XML: {exc}",
        ))
        return len(source.units), len(translated.units), 0, tuple(diagnostics)

    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        _write_docx_parts(parts, out, member_order=package.member_order)
        validation = validate_docx_package(out)
        del validation
    except (DocxTranslationError, DocxMergeError) as exc:
        if out.exists():
            with suppress(OSError):
                out.unlink()
        diagnostics.append(Diagnostic("fatal", "docx-translate.invalid-docx", str(exc)))
        return len(source.units), len(translated.units), 0, tuple(diagnostics)
    aligned_units = sum(len(alignment.unit_indices) for alignment in alignment_plan.alignments)
    return len(source.units), len(translated.units), aligned_units, tuple(diagnostics)


def discover_targets(
    *,
    content_root: Path = CONTENT_ROOT,
    book: int | None = None,
    lang: Locale = "en",
    include_existing: bool = False,
) -> tuple[BookDocxTranslationTarget, ...]:
    targets, _discovery = _discover_targets(
        content_root=content_root,
        book=book,
        lang=lang,
        include_existing=include_existing,
    )
    return targets


def _discover_targets(
    *,
    content_root: Path = CONTENT_ROOT,
    book: int | None = None,
    lang: Locale = "en",
    include_existing: bool = False,
) -> tuple[tuple[BookDocxTranslationTarget, ...], DocxTranslationDiscovery]:
    if lang == DEFAULT_LOCALE:
        raise DocxTranslationError(
            f"docx translate-from-md refuses source locale {DEFAULT_LOCALE!r}; "
            "choose a translated target locale."
        )
    catalog = scan_catalog(content_root)
    translated_entries = {
        entry.number: entry
        for entry in catalog
        if entry.kind == "book" and entry.lang == lang
    }
    source_entries = sorted(
        (entry for entry in catalog if entry.kind == "book" and entry.lang == DEFAULT_LOCALE),
        key=lambda entry: entry.number,
    )
    targets: list[BookDocxTranslationTarget] = []
    source_books = 0
    eligible = 0
    missing = 0
    existing = 0
    ineligible = 0
    matched_source = False
    explicit_ineligible_detail = ""

    for entry in source_entries:
        if book is not None and entry.number != book:
            continue
        matched_source = True
        source_books += 1
        source_md = entry.work_dir / "ru.md"
        source_docx = entry.work_dir / "ru.docx"
        translated_md = entry.work_dir / f"{lang}.md"
        translated_docx = entry.work_dir / f"{lang}.docx"
        missing_inputs = [
            path.name
            for path in (source_docx, source_md, translated_md)
            if not path.is_file()
        ]
        if entry.number not in translated_entries:
            missing_inputs.append(f"{lang}.md catalog entry")
        if missing_inputs:
            ineligible += 1
            explicit_ineligible_detail = ", ".join(dict.fromkeys(missing_inputs))
            continue
        eligible += 1
        translated_exists = translated_docx.is_file()
        if translated_exists:
            existing += 1
        else:
            missing += 1

        should_include = not translated_exists
        if translated_exists and (book is not None or include_existing):
            should_include = True
        if not should_include:
            continue
        targets.append(BookDocxTranslationTarget(
            entry=entry,
            source_docx=source_docx,
            source_md=source_md,
            translated_md=translated_md,
            translated_docx=translated_docx,
        ))
    discovery = DocxTranslationDiscovery(
        source_books=source_books,
        eligible=eligible,
        missing=missing,
        existing=existing,
        ineligible=ineligible,
    )
    if book is not None and not targets:
        if not matched_source:
            raise DocxTranslationError(f"book-{book:02d} was not found in {content_root / 'books'}")
        detail = explicit_ineligible_detail or f"eligible {lang}.md book entry"
        raise DocxTranslationError(
            f"book-{book:02d} cannot be translated to {lang}.docx: missing {detail}."
        )
    return tuple(sorted(targets, key=lambda target: target.entry.number)), discovery


def _target_scope(target: BookDocxTranslationTarget) -> PurePosixPath:
    segment = SEGMENT_OF[target.entry.kind]
    return PurePosixPath(segment) / target.entry.work_dir.name


def translate_docx_batch(
    *,
    content_root: Path = CONTENT_ROOT,
    book: int | None = None,
    lang: Locale = "en",
    dry_run: bool = False,
    replace: bool = False,
    limit: int = 0,
) -> DocxTranslationBatch:
    if replace and book is None:
        raise DocxTranslationError(
            "--replace requires an explicit book:NN selector; existing translated DOCX is source."
        )
    if limit < 0:
        raise DocxTranslationError("--limit must be non-negative.")
    if book is not None and limit:
        raise DocxTranslationError("--limit cannot be combined with an explicit book:NN selector.")
    discovered_targets, discovery = _discover_targets(
        content_root=content_root,
        book=book,
        lang=lang,
        include_existing=replace,
    )
    targets = list(discovered_targets)
    if limit:
        targets = targets[:limit]
    reports: list[DocxTranslationReport] = []
    for target in targets:
        diagnostics: list[Diagnostic] = []
        if target.translated_docx.exists() and not replace:
            diagnostics.append(Diagnostic(
                "fatal",
                "docx-translate.overwrite-refused",
                (
                    f"{target.translated_docx} exists; after bootstrap, translated DOCX is "
                    "source. Pass --replace with this explicit book only if you intend to "
                    "discard DOCX-side edits."
                ),
            ))
            plan = WritePlan(
                target_root=content_root,
                target_scope=_target_scope(target),
                operations=(EnsureDirOp(_target_scope(target), "source_artifact", "work bundle"),),
                diagnostics=tuple(diagnostics),
                replace=replace,
                source_document=target.source_docx,
            )
            reports.append(DocxTranslationReport(
                target=target,
                write_report=apply_plan(plan, dry_run=True),
                source_units=0,
                translated_units=0,
                aligned_units=0,
            ))
            continue

        with tempfile.TemporaryDirectory(prefix="pancratius-docx-translate-") as tmp:
            staged = Path(tmp) / target.translated_docx.name
            source_units, translated_units, aligned_units, transfer_diags = render_translated_docx(
                source_docx=target.source_docx,
                source_md=target.source_md,
                translated_md=target.translated_md,
                out=staged,
            )
            diagnostics.extend(transfer_diags)
            scope = _target_scope(target)
            rel_docx = scope / target.translated_docx.name
            operations: list[EnsureDirOp | CopyOp] = [
                EnsureDirOp(scope, "source_artifact", "work bundle"),
            ]
            if staged.exists():
                operations.append(CopyOp(
                    rel_path=rel_docx,
                    role="source_artifact",
                    reason=f"translated DOCX from {target.source_docx.name}, {target.source_md.name}, and {target.translated_md.name}",
                    source=staged,
                ))
            plan = WritePlan(
                target_root=content_root,
                target_scope=scope,
                operations=tuple(operations),
                diagnostics=tuple(diagnostics),
                replace=replace,
                source_document=target.source_docx,
            )
            write_report = apply_plan(plan, dry_run=dry_run)
            reports.append(DocxTranslationReport(
                target=target,
                write_report=write_report,
                source_units=source_units,
                translated_units=translated_units,
                aligned_units=aligned_units,
                output=target.translated_docx,
            ))
    return DocxTranslationBatch(tuple(reports), discovery=discovery)


def print_batch(batch: DocxTranslationBatch, *, dry_run: bool) -> None:
    write_verb = "would write" if dry_run else "wrote"
    if not batch.reports:
        print(f"nothing to translate to DOCX. {_discovery_summary(batch.discovery)}")
        return
    for report in batch.reports:
        rel = report.target.translated_docx
        wr = report.write_report
        refused = bool(wr.refused) or any(diagnostic.severity == "fatal" for diagnostic in wr.diagnostics)
        status = "REFUSE" if refused else "OK"
        verb = "would refuse" if dry_run and refused else "refused" if refused else write_verb
        print(
            f"  {status} book-{report.target.entry.number:02d}: {verb} {rel} "
            f"({report.aligned_units}/{report.source_units} aligned units)"
        )
        for diag in wr.diagnostics:
            if diag.severity in {"fatal", "warning"}:
                print(f"      {diag.severity}: [{diag.code}] {diag.message}")
    print(_discovery_summary(batch.discovery))


def _discovery_summary(discovery: DocxTranslationDiscovery) -> str:
    return (
        f"coverage: {discovery.eligible}/{discovery.source_books} source book(s) eligible; "
        f"{discovery.missing} missing, {discovery.existing} existing source DOCX, "
        f"{discovery.ineligible} ineligible."
    )
