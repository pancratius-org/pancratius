"""Chunk planning keeps stanzas whole and respects the token budget; the
deterministic checks catch missing, echoed, and markup-broken translations.
"""

from __future__ import annotations

from pancratius.translate.checks import Severity, check_translation
from pancratius.translate.chunker import plan_chunks
from pancratius.translate.config import TranslateConfig
from pancratius.translate.document import parse_document

# A small budget so chunk boundaries are exercised on short fixtures.
_CFG = TranslateConfig(chunk_source_tokens=8, source_chars_per_token=1.0)


def test_chunks_cover_every_unit_in_order() -> None:
    body = "Раз.\n\nДва.\n\nТри.\n\nЧетыре.\n"
    doc = parse_document(body)
    chunks = plan_chunks(doc, _CFG)
    flat = [uid for chunk in chunks for uid in chunk.unit_ids]
    assert flat == [u.id for u in doc.units]


def test_verse_stanza_is_never_split() -> None:
    body = '<div class="lineated verse">\n\n' + "".join(f"строка{i}  \n" for i in range(6)) + "\n</div>\n"
    doc = parse_document(body)
    chunks = plan_chunks(doc, TranslateConfig(chunk_source_tokens=3, source_chars_per_token=1.0))
    # The whole stanza is one indivisible atom -> a single chunk holds all lines.
    holding = [c for c in chunks if any(uid in c.unit_ids for uid in (u.id for u in doc.units))]
    assert len(holding) == 1
    assert len(holding[0].unit_ids) == 6


def test_check_flags_missing_and_echoed() -> None:
    doc = parse_document("Свет мой.\n\nТьма.\n")
    first, second = doc.units
    findings = check_translation(doc, {first.id: "Тьма."})  # second missing, first echoed-ish
    codes = {f.code: f.severity for f in findings}
    assert codes["missing"] is Severity.CRITICAL
    assert "residual_cyrillic" in codes


def test_check_flags_unbalanced_bold() -> None:
    doc = parse_document("**Свет**\n")
    findings = check_translation(doc, {doc.units[0].id: "**Light"})
    assert any(f.code == "unbalanced_bold" for f in findings)


def test_clean_translation_has_no_findings() -> None:
    doc = parse_document("Свет мой.\n")
    findings = check_translation(doc, {doc.units[0].id: "My light."})
    assert findings == []


def test_frontmatter_cyrillic_flagged_in_title_description_and_tags() -> None:
    doc = parse_document("Свет.\n")
    en_fm = {"title": "Книга Света", "description": "clean", "tags": ["Light", "Тьма"]}
    findings = check_translation(doc, {doc.units[0].id: "Light."}, en_fm=en_fm)
    fm = [f for f in findings if f.code == "frontmatter_cyrillic"]
    assert len(fm) == 2  # the Cyrillic title and the Cyrillic tag, not the clean description
    assert all(f.severity is Severity.MEDIUM for f in fm)


def test_no_en_fm_keeps_existing_callers_clean() -> None:
    doc = parse_document("Свет.\n")
    findings = check_translation(doc, {doc.units[0].id: "Light."})
    assert not any(f.code == "frontmatter_cyrillic" for f in findings)


def test_mixed_script_flags_welded_token_but_not_a_clean_gloss() -> None:
    doc = parse_document("имеЙ\n\nГлагол\n")
    a, b = doc.units
    findings = check_translation(
        doc,
        {a.id: 'HaveЙ here', b.id: 'Word (Russian «Глагол», "Glagol")'},
    )
    codes = {(f.code, f.unit_id) for f in findings}
    assert ("mixed_script", a.id) in codes  # welded token
    assert ("mixed_script", b.id) not in codes  # the gloss keeps scripts apart


def test_byte_equal_flags_passthrough_but_not_numbers_or_cyrillic_echo() -> None:
    doc = parse_document("First Epistle\n\n42\n\nСвет.\n")
    a, b, c = doc.units
    findings = check_translation(doc, {a.id: "First Epistle", b.id: "42", c.id: "Свет."})
    codes = {(f.code, f.unit_id) for f in findings}
    assert ("byte_equal", a.id) in codes  # non-trivial Latin source returned verbatim
    assert ("byte_equal", b.id) not in codes  # too short (a number) — guarded
    assert ("byte_equal", c.id) not in codes  # Cyrillic echo is the `echoed` code's job
    assert ("echoed", c.id) in codes
