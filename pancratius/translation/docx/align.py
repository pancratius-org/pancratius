from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from itertools import combinations
from typing import cast

from pancratius.translation.docx.models import (
    DocxTranslationError,
    IgnoredWordSlot,
    MarkdownTransferDocument,
    MarkdownTransferUnit,
    MarkdownUnitPairing,
    SourceDocxAlignmentPlan,
    TextAlignmentVariantReason,
    TransferAlignment,
    TransferUnitKind,
    TranslatedInline,
    TranslatedTextRun,
    WordTextSlot,
)
from pancratius.writeplan import Diagnostic

SPURIOUS_CONNECTOR_HYPHEN_RE = re.compile(
    r"\s+[—–‑‐−-]\s*(?=(?:так|как|храм)\b)",
    flags=re.IGNORECASE,
)
SOURCE_CITATION_SUFFIX_RE = re.compile(
    r"(?:\s*(?:Википедия|Wikipedia)\+\d+)+\s*$",
    flags=re.IGNORECASE,
)

@dataclass(frozen=True, slots=True)
class TextAlignmentVariant:
    """A named, narrow source-MD/source-DOCX text equivalence."""

    reason: TextAlignmentVariantReason
    key: str


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


normalize_transfer_text = _normalize_text
merge_adjacent_runs = _merge_adjacent_runs
split_inlines_on_newlines = _split_inlines_on_newlines
texts_match_for_source_alignment = _texts_match_for_source_alignment
unit_matches_slot = _unit_matches_slot
slot_has_text = _slot_has_text
slot_preview = _slot_preview
align_source_units = _align_source_units
pair_markdown_units = _pair_markdown_units
join_markdown_units_for_word_slot = _join_units
ignored_slot_diagnostics = _ignored_slot_diagnostics
