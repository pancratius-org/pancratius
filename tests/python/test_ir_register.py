"""Display-register pass: bordered set-apart runs → scripture/inset quotes."""

from __future__ import annotations

from pancratius import ir
from pancratius.ir.lower import _block_md
from pancratius.ir.register import display_register_blocks


def _p(text: str, border: ir.BorderKind = "", *, ord_: int | None = None) -> ir.Paragraph:
    span = ir.SourceSpan(ord_, ord_) if ord_ is not None else None
    return ir.Paragraph(inlines=[ir.Text(text)], border=border, source_span=span)


def _empty() -> ir.Paragraph:
    return ir.Paragraph(inlines=[], empty=True)


def _filler(n: int) -> list[ir.Block]:
    """Plain body paragraphs to keep bordered runs under the baseline rate."""
    return [_p(f"body sentence {i}.") for i in range(n)]


def test_box_run_wraps_as_scripture() -> None:
    blocks: list[ir.Block] = [
        *_filler(8),
        _p("7 Се, грядет с облаками.", "box", ord_=8),
        _p("8 Я есмь Альфа и Омега.", "box", ord_=9),
        _p("after"),
    ]
    out = display_register_blocks(blocks)
    quotes = [b for b in out if isinstance(b, ir.QuoteBlock)]
    assert len(quotes) == 1
    assert quotes[0].register is ir.Register.SCRIPTURE
    assert len(quotes[0].blocks) == 2
    assert quotes[0].source_span == ir.SourceSpan(8, 9)


def test_rule_run_wraps_as_inset() -> None:
    blocks: list[ir.Block] = [*_filler(8), _p("Set-apart passage.", "rule")]
    out = display_register_blocks(blocks)
    quotes = [b for b in out if isinstance(b, ir.QuoteBlock)]
    assert len(quotes) == 1
    assert quotes[0].register is ir.Register.INSET


def test_baseline_border_kind_is_left_alone() -> None:
    # A border kind covering >= 30% of text paragraphs is the book's own frame.
    blocks: list[ir.Block] = [_p(f"boxed {i}", "box") for i in range(5)] + _filler(5)
    out = display_register_blocks(blocks)
    assert not any(isinstance(b, ir.QuoteBlock) for b in out)


def test_interior_empty_continues_run_trailing_stays_out() -> None:
    blocks: list[ir.Block] = [
        *_filler(10),
        _p("first stanza", "rule"),
        _empty(),
        _p("second stanza", "rule"),
        _empty(),
        _p("plain after"),
    ]
    out = display_register_blocks(blocks)
    quotes = [b for b in out if isinstance(b, ir.QuoteBlock)]
    assert len(quotes) == 1
    assert len(quotes[0].blocks) == 3  # para, empty, para
    # The trailing empty did not enter the wrapper.
    idx = out.index(quotes[0])
    trailing = out[idx + 1]
    assert isinstance(trailing, ir.Paragraph) and trailing.empty


def test_adjacent_different_kinds_split() -> None:
    blocks: list[ir.Block] = [
        *_filler(12),
        _p("boxed scripture", "box"),
        _p("ruled inset", "rule"),
    ]
    out = display_register_blocks(blocks)
    registers = [b.register for b in out if isinstance(b, ir.QuoteBlock)]
    assert registers == [ir.Register.SCRIPTURE, ir.Register.INSET]


def test_scripture_lowering_is_classed_html_blockquote() -> None:
    quote = ir.QuoteBlock(
        blocks=[_p("7 Се, грядет с облаками."), _p("8 Аминь.")],
        register=ir.Register.SCRIPTURE,
    )
    md = _block_md(quote, "ru")
    assert md is not None
    assert md.splitlines()[0] == '<blockquote class="scripture">'
    assert md.splitlines()[1] == ""  # the load-bearing blank line
    assert "7 Се, грядет с облаками." in md
    assert md.splitlines()[-1] == "</blockquote>"


def test_inset_lowering_is_plain_quote_with_block_separators() -> None:
    quote = ir.QuoteBlock(
        blocks=[_p("Первый абзац."), _empty(), _p("Второй абзац.")],
        register=ir.Register.INSET,
    )
    md = _block_md(quote, "ru")
    assert md == "> Первый абзац.\n>\n> Второй абзац."


def test_quote_member_hard_breaks_become_display_lines() -> None:
    para = ir.Paragraph(inlines=[
        ir.Text("Я — не форма,"),
        ir.LineBreak(),
        ir.Text("но во всех формах живу."),
    ])
    quote = ir.QuoteBlock(blocks=[para], register=ir.Register.INSET)
    md = _block_md(quote, "ru")
    assert md == "> Я — не форма,  \n> но во всех формах живу."


def test_quote_member_lines_escape_leading_list_markers() -> None:
    para = ir.Paragraph(inlines=[
        ir.Text("1. Подготовка через хаос"),
        ir.LineBreak(),
        ir.Text("- и тишина после."),
    ])
    quote = ir.QuoteBlock(blocks=[para], register=ir.Register.INSET)
    md = _block_md(quote, "ru")
    assert md is not None
    # Neither line may parse as a Markdown list inside the quote.
    assert "> 1\\." in md and "> \\-" in md


def test_quote_member_soft_breaks_stay_prose() -> None:
    para = ir.Paragraph(inlines=[
        ir.Text("обычная строка,"),
        ir.SoftBreak(),
        ir.Text("перенесённая в источнике."),
    ])
    quote = ir.QuoteBlock(blocks=[para], register=ir.Register.INSET)
    md = _block_md(quote, "ru")
    assert md == "> обычная строка, перенесённая в источнике."


def test_equation_scaffold_never_promotes_to_verse() -> None:
    from pancratius.ir.normalize import is_equation_scaffold

    assert is_equation_scaffold(["143 = 11 × 13", "а 153 = 9 × 17"])
    assert is_equation_scaffold(["1² + 5² + 3² = 1 + 25 + 9 = 35", "и снова: 3 + 5 = 8"])
    assert not is_equation_scaffold(["Я — не форма,", "но во всех формах живу."])
    assert not is_equation_scaffold(["Свет мой тихий,", "в сердце горит."])


def test_downloads_scripture_degrades_to_plain_quote() -> None:
    from pancratius.render_downloads import _scripture_to_quote

    body = (
        "Прозa до.\n\n"
        '<blockquote class="scripture">\n\n**7 Се, грядет.**\n\nЛиния раз  \nЛиния два\n\n</blockquote>\n\n'
        "Проза после."
    )
    out = _scripture_to_quote(body)
    assert '<blockquote class="scripture">' not in out
    assert "> **7 Се, грядет.**\n>\n> Линия раз  \n> Линия два" in out
