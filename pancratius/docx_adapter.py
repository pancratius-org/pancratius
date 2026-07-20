"""Project the canonical DOCX source model into Pancratius block IR.

This module never opens a DOCX and never joins independently parsed records.
Every rich inline, physical paragraph fact, relationship, and source coordinate
comes from one ``DocxSourceDocument`` built by ``docx_source.read``.
"""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath
from typing import Literal, assert_never

from pancratius import docx_source, ir
from pancratius.ir.inlines import inline_plain


class _Context:
    def __init__(
        self,
        source: docx_source.DocxSourceDocument,
        media_dir: Path,
        diagnostics: ir.DiagnosticSink,
    ) -> None:
        self.source = source
        self.media_dir = media_dir
        self.diagnostics = diagnostics
        self.media = {part.part_name: part for part in source.media}
        self.notes = {(note.kind, note.note_id): note for note in source.notes}
        self.footnotes: list[ir.FootnoteDef] = []
        self.unknown_inlines: dict[str, int] = {}
        self.unknown_blocks: dict[str, int] = {}
        self.next_number: dict[tuple[int, int], int] = {}

    def image_source(self, image: docx_source.SourceImage) -> str:
        if image.media_part is None:
            return image.target
        media = self.media.get(image.media_part)
        if media is None:
            self.diagnostics.append(ir.Diagnostic(
                "fatal",
                "import.image-missing",
                f"canonical image part {image.media_part!r} is unavailable",
            ))
            return ""
        part = PurePosixPath(media.part_name)
        relative = PurePosixPath(*part.parts[1:]) if part.parts[:1] == ("word",) else part
        destination = self.media_dir.joinpath(*relative.parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.is_file() or destination.read_bytes() != media.data:
            destination.write_bytes(media.data)
        return str(destination)

    def note(self, reference: docx_source.SourceNoteReference) -> ir.FootnoteRef:
        dense_id = len(self.footnotes) + 1
        definition = self.notes.get((reference.kind, reference.note_id))
        if definition is None:
            self.diagnostics.append(ir.Diagnostic(
                "fatal",
                "import.note-missing",
                f"{reference.kind.value} {reference.note_id} has no definition",
            ))
            blocks: list[ir.Block] = []
        else:
            blocks = _adapt_sequence(list(_flatten_controls(definition.blocks)), self)
        self.footnotes.append(ir.FootnoteDef(dense_id, blocks))
        return ir.FootnoteRef(raw_index=dense_id, id=dense_id)

    def finish_diagnostics(self) -> None:
        if self.unknown_inlines:
            summary = ", ".join(
                f"{name}={count}" for name, count in sorted(self.unknown_inlines.items())
            )
            self.diagnostics.append(ir.Diagnostic(
                "warning",
                "import.source-inline-unsupported",
                f"unsupported source inlines preserved explicitly: {summary}",
            ))
        if self.unknown_blocks:
            summary = ", ".join(
                f"{name}={count}" for name, count in sorted(self.unknown_blocks.items())
            )
            self.diagnostics.append(ir.Diagnostic(
                "warning",
                "import.source-block-unsupported",
                f"unsupported source blocks preserved explicitly: {summary}",
            ))


def _source_provenance(
    block: docx_source.SourceParagraphBlock,
) -> ir.SourceProvenance | None:
    if block.coordinates:
        return ir.SourceProvenance.for_lines(block.coordinates)
    if block.paragraph is not None and block.paragraph.disposition is not (
        docx_source.ParagraphDisposition.PAGINATION_ONLY
    ):
        ordinal = int(block.paragraph.ordinal)
        return ir.SourceProvenance(ordinal, ordinal)
    return None


def _covering_span(blocks: list[ir.Block]) -> ir.SourceProvenance | None:
    present = [block.source_span for block in blocks if block.source_span is not None]
    return ir.merge_source_spans(present) if present else None


def _source_diagnostic(finding: docx_source.SourceDiagnostic) -> ir.Diagnostic:
    suffix = finding.code.removeprefix("source.")
    severity: Literal["fatal", "warning", "info"]
    if suffix == "compatibility-fallback":
        severity = "info"
    elif suffix.startswith("field-"):
        severity = "warning"
    else:
        severity = "fatal"
    location = ""
    if finding.address is not None:
        path = "/".join(str(index) for index in finding.address.path)
        location = f"{finding.address.story.value}:{path}: "
    return ir.Diagnostic(
        severity,
        f"import.source-{suffix}",
        f"{location}{finding.message}",
    )


def _unknown_inline(
    source: docx_source.SourceUnknownInline,
    ctx: _Context,
) -> list[ir.Inline]:
    ctx.unknown_inlines[source.name] = ctx.unknown_inlines.get(source.name, 0) + 1
    return [ir.UnknownInline(source.name, [ir.Text(source.text)])] if source.text else []


def _run_inlines(
    source: docx_source.SourceRun,
    ctx: _Context,
) -> list[ir.Inline]:
    children = _inlines(source.children, ctx)
    properties = source.properties

    def styled(items: list[ir.Inline]) -> list[ir.Inline]:
        if not items:
            return []
        if properties.code:
            items = [ir.Code(inline_plain(items))]
        else:
            if properties.rtl:
                items = [ir.DirectionalSpan("rtl", items)]
            if properties.subscript:
                items = [ir.Emphasis("sub", items)]
            elif properties.superscript:
                items = [ir.Emphasis("sup", items)]
            if properties.strike:
                items = [ir.Emphasis("strike", items)]
            if properties.italic:
                items = [ir.Emphasis("emph", items)]
            if properties.bold:
                items = [ir.Emphasis("strong", items)]
        if properties.code and properties.rtl:
            items = [ir.DirectionalSpan("rtl", items)]
        return items

    out: list[ir.Inline] = []
    text: list[ir.Inline] = []

    def flush() -> None:
        chunk = list(text)
        text.clear()
        out.extend(styled(chunk))

    for child in children:
        if isinstance(child, (ir.ImageInline, ir.FootnoteRef)):
            flush()
            out.append(child)
        else:
            text.append(child)
    flush()
    return out


def _field_hyperlink_target(instruction: str) -> str | None:
    quoted_or_bare = r'(?:"([^"]+)"|(\S+))'
    local = re.search(
        rf"\\l\s+{quoted_or_bare}", instruction, flags=re.IGNORECASE
    )
    external = re.match(
        rf"\s*HYPERLINK\s+(?!\\[A-Za-z]){quoted_or_bare}",
        instruction,
        flags=re.IGNORECASE,
    )
    target = ""
    if external is not None:
        target = external.group(1) or external.group(2) or ""
    if local is not None:
        anchor = local.group(1) or local.group(2) or ""
        target = f"{target}#{anchor}" if target else f"#{anchor}"
    return target or None


def _inline(source: docx_source.SourceInline, ctx: _Context) -> list[ir.Inline]:
    match source:
        case docx_source.SourceText(value=value):
            value = re.sub(r"[ \t\r\n]+", " ", value)
            return [ir.Text(value)] if value else []
        case docx_source.BreakKind.LINE:
            return [ir.LineBreak()]
        case docx_source.BreakKind.PAGE | docx_source.BreakKind.COLUMN:
            # Pagination is not authored lineation, but it is still a separator
            # when it occurs between readable fragments in one paragraph.  The
            # canonical physical view makes the same promise through
            # ParagraphContent.reading.  Paragraph-edge whitespace is trimmed
            # after projection, so a terminal page break remains invisible.
            return [ir.Text(" ")]
        case docx_source.SourceRenderedPageBreak():
            return []
        case docx_source.SourceHorizontalRule():
            raise AssertionError("horizontal rule reached inline projection")
        case docx_source.SourceRun():
            return _run_inlines(source, ctx)
        case docx_source.SourceHyperlink():
            return [ir.Link(_inlines(source.children, ctx), source.target)]
        case docx_source.SourceImage():
            return [ir.ImageInline(
                ctx.image_source(source),
                " ".join(source.alt.split()),
            )]
        case docx_source.SourceNoteReference():
            return [ctx.note(source)]
        case docx_source.SourceSymbol():
            if source.character:
                return [ir.Text(source.character)]
            name = f"symbol:{source.font or 'unknown'}:{source.code or 'unknown'}"
            ctx.unknown_inlines[name] = ctx.unknown_inlines.get(name, 0) + 1
            return []
        case docx_source.SourceFieldInstruction() | docx_source.SourceFieldBoundary():
            return []
        case docx_source.SourceField():
            children = _inlines(source.children, ctx)
            if source.kind == "HYPERLINK":
                if target := _field_hyperlink_target(source.instruction):
                    return [ir.Link(children, target)]
                ctx.unknown_inlines["field:HYPERLINK"] = (
                    ctx.unknown_inlines.get("field:HYPERLINK", 0) + 1
                )
                return [ir.UnknownInline("field:HYPERLINK", children)]
            if source.kind not in {"TOC", "PAGEREF", "SEQ", "INCLUDEPICTURE"}:
                name = f"field:{source.kind or 'unknown'}"
                ctx.unknown_inlines[name] = ctx.unknown_inlines.get(name, 0) + 1
                return [ir.UnknownInline(name, children)]
            return children
        case docx_source.SourceUnknownInline():
            return _unknown_inline(source, ctx)
        case docx_source.SourceTextBox():
            raise AssertionError("text box reached inline projection")
    assert_never(source)


def _inlines(
    source: tuple[docx_source.SourceInline, ...],
    ctx: _Context,
) -> list[ir.Inline]:
    return _normalize_inlines([
        inline for item in source for inline in _inline(item, ctx)
    ])


def _merge_adjacent_text(inlines: list[ir.Inline]) -> list[ir.Inline]:
    out: list[ir.Inline] = []
    for inline in inlines:
        if isinstance(inline, ir.Text) and out and isinstance(out[-1], ir.Text):
            previous = out[-1]
            assert isinstance(previous, ir.Text)
            out[-1] = ir.Text(re.sub(r" +", " ", previous.value + inline.value))
        else:
            out.append(inline)
    return out


def _without_covering_emphasis(
    inline: ir.Inline,
    kind: ir.EmphKind,
) -> tuple[bool, list[ir.Inline]]:
    """Remove ``kind`` when it covers the inline's entire readable content."""
    if isinstance(inline, ir.Emphasis) and inline.kind == kind:
        return True, list(inline.children)
    if not isinstance(inline, ir.ContainerInline):
        return False, [inline]

    children: list[ir.Inline] = []
    covered = False
    for child in inline.children:
        if isinstance(child, ir.Text) and child.value.isspace():
            children.append(child)
            continue
        child_covered, replacement = _without_covering_emphasis(child, kind)
        if not child_covered:
            return False, [inline]
        covered = True
        children.extend(replacement)
    if not covered:
        return False, [inline]
    return True, [ir.rebuild_container(inline, _merge_adjacent_text(children))]


def _factor_common_emphasis(
    inlines: list[ir.Inline],
    kind: ir.EmphKind,
) -> list[ir.Inline]:
    """Factor a presentation span shared across neighboring source runs.

    Word stores the intersections of bold and italic as independent runs. A
    fixed wrapper order cannot express both an italic span containing bold and
    a bold span containing italic without splitting one of them. Recover the
    common span from adjacent run fragments so Markdown never receives an
    ambiguous ``****`` delimiter seam.
    """
    out: list[ir.Inline] = []
    index = 0
    while index < len(inlines):
        covered, replacement = _without_covering_emphasis(inlines[index], kind)
        if not covered:
            out.append(inlines[index])
            index += 1
            continue

        replacements = list(replacement)
        pending_whitespace: list[ir.Inline] = []
        covered_count = 1
        cursor = index + 1
        while cursor < len(inlines):
            candidate = inlines[cursor]
            if isinstance(candidate, ir.Text) and candidate.value.isspace():
                pending_whitespace.append(candidate)
                cursor += 1
                continue
            candidate_covered, candidate_replacement = _without_covering_emphasis(
                candidate,
                kind,
            )
            if not candidate_covered:
                break
            replacements.extend(pending_whitespace)
            pending_whitespace.clear()
            replacements.extend(candidate_replacement)
            covered_count += 1
            cursor += 1

        if covered_count == 1:
            out.append(inlines[index])
            index += 1
            continue
        out.append(ir.Emphasis(kind, _merge_adjacent_text(replacements)))
        out.extend(pending_whitespace)
        index = cursor
    return out


def _normalize_inlines(inlines: list[ir.Inline]) -> list[ir.Inline]:
    """Erase source run fragmentation while retaining rich boundaries."""
    out: list[ir.Inline] = []

    def append_text(value: str) -> None:
        if out and isinstance(out[-1], ir.Text):
            previous = out[-1]
            assert isinstance(previous, ir.Text)
            out[-1] = ir.Text(re.sub(r" +", " ", previous.value + value))
        else:
            out.append(ir.Text(value))

    for inline in inlines:
        if isinstance(inline, ir.ContainerInline):
            inline = ir.rebuild_container(
                inline, _normalize_inlines(inline.children)
            )
        leading = ""
        trailing = ""
        if isinstance(inline, ir.Emphasis) and inline.children:
            children = list(inline.children)
            if isinstance(children[0], ir.Text):
                value = children[0].value
                stripped = value.lstrip()
                leading = value[: len(value) - len(stripped)]
                children[0] = ir.Text(stripped)
            if children and isinstance(children[-1], ir.Text):
                value = children[-1].value
                stripped = value.rstrip()
                trailing = value[len(stripped):]
                children[-1] = ir.Text(stripped)
            children = [
                child
                for child in children
                if not isinstance(child, ir.Text) or child.value
            ]
            inline = ir.Emphasis(inline.kind, children)
        if leading:
            append_text(leading)
        if isinstance(inline, ir.Emphasis) and not inline.children:
            # Presentation on whitespace has no reading meaning. A whitespace
            # run has both a leading and trailing edge; emit it once.
            if trailing and not leading:
                append_text(trailing)
            continue
        if (
            isinstance(inline, ir.Emphasis)
            and len(out) >= 2
            and isinstance(out[-1], ir.Text)
            and out[-1].value.isspace()
            and isinstance(out[-2], ir.Emphasis)
            and out[-2].kind == inline.kind
        ):
            whitespace = out.pop()
            previous = out.pop()
            assert isinstance(previous, ir.Emphasis)
            out.append(ir.Emphasis(
                previous.kind,
                _normalize_inlines([
                    *previous.children,
                    whitespace,
                    *inline.children,
                ]),
            ))
        elif isinstance(inline, ir.Text) and out and isinstance(out[-1], ir.Text):
            append_text(inline.value)
        elif (
            isinstance(inline, ir.Link)
            and out
            and isinstance(out[-1], ir.Link)
            and out[-1].target == inline.target
        ):
            previous = out[-1]
            assert isinstance(previous, ir.Link)
            out[-1] = ir.Link(
                _normalize_inlines([*previous.children, *inline.children]),
                previous.target,
            )
        elif (
            isinstance(inline, ir.Emphasis)
            and out
            and isinstance(out[-1], ir.Emphasis)
            and out[-1].kind == inline.kind
        ):
            previous = out[-1]
            assert isinstance(previous, ir.Emphasis)
            out[-1] = ir.Emphasis(
                previous.kind,
                _normalize_inlines([*previous.children, *inline.children]),
            )
        else:
            out.append(inline)
        if trailing:
            append_text(trailing)
    for kind in ("strong", "emph"):
        out = _factor_common_emphasis(out, kind)
    return out


def _unwrap_emphasis_kind(
    inlines: list[ir.Inline],
    kind: ir.EmphKind,
) -> list[ir.Inline]:
    """Remove one redundant presentation layer without flattening rich content."""
    out: list[ir.Inline] = []
    for inline in inlines:
        if isinstance(inline, ir.ContainerInline):
            children = _unwrap_emphasis_kind(inline.children, kind)
            if isinstance(inline, ir.Emphasis) and inline.kind == kind:
                out.extend(children)
                continue
            inline = ir.rebuild_container(inline, children)
        out.append(inline)
    return _normalize_inlines(out)


def _trim_paragraph_whitespace(inlines: list[ir.Inline]) -> list[ir.Inline]:
    """Discard paragraph-edge layout whitespace; retain internal NBSP content."""
    out = list(inlines)
    if out and isinstance(out[0], ir.Text):
        out[0] = ir.Text(out[0].value.lstrip())
    if out and isinstance(out[-1], ir.Text):
        out[-1] = ir.Text(out[-1].value.rstrip())
    return [
        inline
        for inline in out
        if not isinstance(inline, ir.Text) or inline.value
    ]


type _ParagraphPart = (
    tuple[docx_source.SourceInline, ...]
    | docx_source.SourceTextBox
    | docx_source.SourceHorizontalRule
)


def _split_embedded_blocks(
    source: tuple[docx_source.SourceInline, ...],
) -> list[_ParagraphPart]:
    out: list[_ParagraphPart] = []
    current: list[docx_source.SourceInline] = []

    def flush() -> None:
        if current:
            out.append(tuple(current))
            current.clear()

    for item in source:
        if isinstance(
            item,
            docx_source.SourceTextBox | docx_source.SourceHorizontalRule,
        ):
            flush()
            out.append(item)
            continue
        if isinstance(item, docx_source.SourceRun):
            for part in _split_embedded_blocks(item.children):
                if isinstance(
                    part,
                    docx_source.SourceTextBox | docx_source.SourceHorizontalRule,
                ):
                    flush()
                    out.append(part)
                else:
                    current.append(docx_source.SourceRun(part, item.properties))
            continue
        if isinstance(item, docx_source.SourceHyperlink):
            for part in _split_embedded_blocks(item.children):
                if isinstance(
                    part,
                    docx_source.SourceTextBox | docx_source.SourceHorizontalRule,
                ):
                    flush()
                    out.append(part)
                else:
                    current.append(docx_source.SourceHyperlink(
                        part, item.target, item.relationship_id
                    ))
            continue
        if isinstance(item, docx_source.SourceField):
            for part in _split_embedded_blocks(item.children):
                if isinstance(
                    part,
                    docx_source.SourceTextBox | docx_source.SourceHorizontalRule,
                ):
                    flush()
                    out.append(part)
                else:
                    current.append(docx_source.SourceField(
                        item.instruction,
                        part,
                        item.field_id,
                    ))
            continue
        current.append(item)
    flush()
    return out


def _all_italic(source: tuple[docx_source.SourceInline, ...]) -> bool:
    text_runs: list[docx_source.SourceRun] = []

    def visit(items: tuple[docx_source.SourceInline, ...]) -> None:
        for item in items:
            if isinstance(item, docx_source.SourceRun):
                if any(
                    isinstance(child, docx_source.SourceText) and child.value.strip()
                    for child in item.children
                ):
                    text_runs.append(item)
                visit(item.children)
            elif isinstance(item, docx_source.SourceHyperlink):
                visit(item.children)
            elif isinstance(item, docx_source.SourceField):
                visit(item.children)

    visit(source)
    return bool(text_runs) and all(run.properties.italic for run in text_runs)


def _paragraph_facts(
    block: docx_source.SourceParagraphBlock,
    inlines: list[ir.Inline],
) -> ir.SourceFacts:
    paragraph = block.paragraph
    if paragraph is None:
        return ir.SourceFacts(
            align=block.alignment.value,
            empty=not inlines,
            italic=_all_italic(block.inlines),
            indented=bool(block.indent.attributes),
        )
    return ir.SourceFacts(
        align=paragraph.alignment.value,
        empty=not inlines,
        italic=_all_italic(block.inlines),
        indented=paragraph.indent_departure,
        border=paragraph.border.value,
        lineation_group=(
            paragraph.visual_group.value if paragraph.visual_group is not None else None
        ),
    )


def _inline_block(
    source: docx_source.SourceParagraphBlock,
    source_inlines: tuple[docx_source.SourceInline, ...],
    ctx: _Context,
    *,
    span: ir.SourceProvenance | None,
) -> ir.Block:
    inlines: list[ir.Inline] = _trim_paragraph_whitespace(
        _inlines(source_inlines, ctx)
    )
    if _all_italic(source_inlines) and inlines:
        inlines = [ir.Emphasis(
            "emph",
            _unwrap_emphasis_kind(inlines, "emph"),
        )]
    # The reading surface has three levels below its page title (h2-h4). Deeper
    # Word outline levels remain source facts, but do not become unstyled h5/h6
    # product headings. An empty outline row likewise remains only a boundary.
    heading_level = _product_heading_level(source)
    if heading_level is not None and inlines:
        inlines = _unwrap_emphasis_kind(inlines, "strong")
        return ir.Heading(heading_level, inlines, span)
    return ir.Paragraph(inlines, _paragraph_facts(source, inlines), span)


def _paragraph_blocks(
    source: docx_source.SourceParagraphBlock,
    ctx: _Context,
) -> list[ir.Block]:
    paragraph = source.paragraph
    if (
        paragraph is not None
        and paragraph.disposition is docx_source.ParagraphDisposition.PAGINATION_ONLY
    ):
        return []
    parts = _split_embedded_blocks(source.inlines)
    span = _source_provenance(source)
    if not parts:
        return [_inline_block(source, (), ctx, span=span)]

    out: list[ir.Block] = []
    span_available = True
    for part in parts:
        if isinstance(part, docx_source.SourceTextBox):
            out.extend(_adapt_sequence(list(_flatten_controls(part.blocks)), ctx))
        elif isinstance(part, docx_source.SourceHorizontalRule):
            out.append(ir.ThematicBreak(span if span_available else None))
            span_available = False
        else:
            out.append(_inline_block(
                source,
                part,
                ctx,
                span=span if span_available else None,
            ))
            span_available = False
    return out


def _is_quote_paragraph(block: docx_source.SourceParagraphBlock) -> bool:
    if block.numbering is not None or _product_heading_level(block) is not None:
        return False
    style = f"{block.direct_style} {block.resolved_style}".replace("_", " ").casefold()
    named_quote = any(
        name in style for name in ("blocktext", "block text", "intensequote", "intense quote", "quote")
    )
    paragraph = block.paragraph
    if paragraph is not None and paragraph.thematic:
        return False
    indented_quote = (
        paragraph is not None
        and paragraph.indent_departure
        and block.indent.left.value > 0
        and block.indent.hanging.value <= 0
    )
    return named_quote or indented_quote


def _product_heading_level(
    block: docx_source.SourceParagraphBlock,
) -> int | None:
    level = block.heading_level
    return level if level is not None and 1 <= level <= 3 else None


def _flatten_controls(
    blocks: tuple[docx_source.SourceBlock, ...],
) -> tuple[docx_source.SourceBlock, ...]:
    out: list[docx_source.SourceBlock] = []
    for block in blocks:
        if isinstance(block, docx_source.SourceContentControl):
            out.extend(_flatten_controls(block.blocks))
        else:
            out.append(block)
    return tuple(out)


def _adapt_list(
    blocks: list[docx_source.SourceBlock],
    start_index: int,
    ctx: _Context,
) -> tuple[ir.ListBlock, int]:
    first = blocks[start_index]
    assert isinstance(first, docx_source.SourceParagraphBlock)
    numbering = first.numbering
    assert numbering is not None
    num_id = numbering.num_id
    base_level = numbering.level
    items: list[list[ir.Block]] = []
    index = start_index

    while index < len(blocks):
        block = blocks[index]
        if not isinstance(block, docx_source.SourceParagraphBlock):
            break
        current = block.numbering
        if current is None or current.num_id != num_id or current.level < base_level:
            break
        if current.level > base_level:
            if not items:
                break
            nested, index = _adapt_list(blocks, index, ctx)
            items[-1].append(nested)
            continue
        if current.ordered != numbering.ordered:
            break
        items.append(_paragraph_blocks(block, ctx))
        index += 1

    key = (num_id, base_level)
    start = ctx.next_number.get(key, numbering.start) if numbering.ordered else 1
    if numbering.ordered:
        ctx.next_number[key] = start + len(items)
    members = [member for item in items for member in item]
    return (
        ir.ListBlock(
            ordered=numbering.ordered,
            items=items,
            start=start,
            source_span=_covering_span(members),
        ),
        index,
    )


def _block_inlines(block: ir.Block) -> list[ir.Inline]:
    match block:
        case ir.Heading() | ir.Paragraph():
            return block.inlines
        case ir.LineatedBlock():
            out: list[ir.Inline] = []
            for stanza in block.stanzas:
                for line in stanza:
                    if out:
                        out.append(ir.Text(" "))
                    out.extend(line.inlines)
            return out
        case ir.QuoteBlock():
            return _blocks_as_inlines(block.blocks)
        case ir.ListBlock():
            return _blocks_as_inlines([
                member for item in block.items for member in item
            ])
        case ir.ImageBlock():
            return [ir.ImageInline(block.src, block.alt, block.asset_id)]
        case ir.CodeBlock():
            return [ir.Code(block.text)]
        case ir.UnknownBlock():
            return [ir.UnknownInline(block.note, [ir.Text(block.text)] if block.text else [])]
        case ir.Signature():
            return [ir.Text(" ".join(block.lines))]
        case ir.Epigraph():
            return [ir.Text(" ".join([*block.quote, *block.footer]))]
        case ir.DialogueLabel():
            return [ir.Text(block.speaker)]
        case ir.ThematicBreak():
            return [ir.Text("***")]
        case ir.Table():
            return [
                inline
                for row in block.rows
                for cell in row
                for inline in cell
            ]
    assert_never(block)


def _blocks_as_inlines(blocks: list[ir.Block]) -> list[ir.Inline]:
    out: list[ir.Inline] = []
    for block in blocks:
        inlines = _block_inlines(block)
        if out and inlines:
            out.append(ir.Text(" "))
        out.extend(inlines)
    return out


def _table(source: docx_source.SourceTableBlock, ctx: _Context) -> ir.Table:
    rows: list[list[list[ir.Inline]]] = []
    multi_block = False
    merged = False
    for row in source.rows:
        cells: list[list[ir.Inline]] = []
        for cell in row.cells:
            source_blocks = list(_flatten_controls(cell.blocks))
            multi_block = multi_block or len(source_blocks) > 1
            merged = merged or cell.row_span != 1 or cell.column_span != 1
            cells.append(_blocks_as_inlines(_adapt_sequence(source_blocks, ctx)))
        rows.append(cells)
    return ir.Table(
        rows,
        ir.TableShape(
            has_merged_cells=merged,
            has_multi_block_cells=multi_block,
        ),
    )


def _adapt_sequence(
    blocks: list[docx_source.SourceBlock],
    ctx: _Context,
) -> list[ir.Block]:
    out: list[ir.Block] = []
    index = 0
    while index < len(blocks):
        block = blocks[index]
        if isinstance(block, docx_source.SourceParagraphBlock):
            # Word headings can also carry numbering.  Heading is the product
            # structure; treating such a row as a list item erases the section
            # boundary and lets later section passes consume unrelated prose.
            if block.numbering is not None and _product_heading_level(block) is None:
                list_block, index = _adapt_list(blocks, index, ctx)
                out.append(list_block)
                continue
            if _is_quote_paragraph(block):
                quote_members: list[ir.Block] = []
                while index < len(blocks):
                    candidate = blocks[index]
                    if not isinstance(candidate, docx_source.SourceParagraphBlock):
                        break
                    if not _is_quote_paragraph(candidate):
                        break
                    quote_members.extend(_paragraph_blocks(candidate, ctx))
                    index += 1
                out.append(ir.QuoteBlock(
                    quote_members,
                    ir.Register.ORDINARY,
                    _covering_span(quote_members),
                ))
                continue
            out.extend(_paragraph_blocks(block, ctx))
        elif isinstance(block, docx_source.SourceTableBlock):
            out.append(_table(block, ctx))
        elif isinstance(block, docx_source.SourceContentControl):
            out.extend(_adapt_sequence(list(_flatten_controls(block.blocks)), ctx))
        elif isinstance(block, docx_source.SourceUnknownBlock):
            ctx.unknown_blocks[block.name] = ctx.unknown_blocks.get(block.name, 0) + 1
            out.append(ir.UnknownBlock(block.name, block.text))
        else:
            assert_never(block)
        index += 1
    return out


def adapt(
    source: docx_source.DocxSourceDocument,
    media_dir: Path,
    diagnostics: ir.DiagnosticSink,
) -> ir.Document:
    """Project one canonical source aggregate into block IR."""
    media_dir.mkdir(parents=True, exist_ok=True)
    ctx = _Context(source, media_dir, diagnostics)
    blocks = _adapt_sequence(list(_flatten_controls(source.body)), ctx)
    # Empty source rows after readable content remain Q1 evidence, including at
    # the tail. Leading layout whitespace has no preceding semantic neighbour
    # and must not become visible IR.
    while blocks and isinstance(blocks[0], ir.Paragraph) and blocks[0].empty:
        blocks.pop(0)
    diagnostics.extend(_source_diagnostic(finding) for finding in source.diagnostics)
    ctx.finish_diagnostics()
    return ir.Document(blocks=blocks, footnotes=ctx.footnotes)
