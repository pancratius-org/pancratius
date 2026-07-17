"""Small typed records for portable domain tests."""
from __future__ import annotations

from intent_ai import identity
from intent_ai.identity import BookId, LineId
from intent_ai.records import (
    Align,
    EndPunct,
    IndentVsBook,
    LineFeatures,
    LineRecord,
    RecordDisposition,
    SpacingVsBook,
)

from pancratius.locales import Locale


def sample_records(
    *,
    book_id: BookId | str = "57",
    lang: Locale = "ru",
    count: int = 24,
    run_lengths: tuple[int, ...] | None = None,
) -> list[LineRecord]:
    """Varied body records with explicit run and source-position structure."""
    book = BookId(book_id)
    lengths = run_lengths or (count,)
    if not lengths or any(length <= 0 for length in lengths):
        raise ValueError("sample runs must be non-empty and positive")

    out: list[LineRecord] = []
    ordinal = 10
    sample_index = 0
    total = sum(lengths)
    for run_len in lengths:
        for run_pos in range(run_len):
            text = f"sample line {sample_index}"
            fill = (0.25, 0.7, 1.2)[sample_index % 3]
            features = LineFeatures(
                fill=fill,
                wraps=fill > 1.0,
                char_len=len(text),
                word_count=3,
                end_punct=EndPunct.SENTENCE if sample_index % 2 else EndPunct.NONE,
                starts_lower=bool(sample_index % 2),
                next_line_lower=not bool(sample_index % 2),
                enjambs=not bool(sample_index % 2),
                colon_opens=False,
                align=Align.LEFT,
                indent_vs_book=IndentVsBook.DEFAULT,
                spacing_after_vs_book=SpacingVsBook.TYPICAL,
                align_is_book_default=True,
                sub=0,
                n_subs=1,
                run_len=run_len,
                run_pos=run_pos,
                fill_pctile_in_book=round(sample_index / max(total, 1), 3),
            )
            out.append(
                LineRecord(
                    id=LineId.mapped(lang, book, ordinal, 0),
                    text=text,
                    disposition=RecordDisposition.BODY,
                    features=features,
                    line_text_hash=identity.text_hash(text),
                )
            )
            ordinal += 1
            sample_index += 1
    return out
