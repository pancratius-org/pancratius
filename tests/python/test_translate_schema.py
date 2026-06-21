"""The structured-output schemas are well-formed (strict, id enum scoped to the
shown units, described fields) and their parser reads the array reply back into an
id→text map, ignoring malformed rows.
"""

from __future__ import annotations

from pancratius.translate.schema import parse_translations, profile_format, translation_format


def test_translation_schema_pins_ids_to_an_enum() -> None:
    fmt = translation_format(["u0001", "u0002"])
    item = fmt["json_schema"]["schema"]["properties"]["translations"]["items"]
    assert fmt["json_schema"]["strict"] is True
    assert item["properties"]["id"]["enum"] == ["u0001", "u0002"]
    assert item["additionalProperties"] is False
    assert set(item["required"]) == {"id", "english"}
    # Every field carries a description (the model is conditioned by it).
    assert item["properties"]["english"]["description"]


def test_parse_translations_reads_array_and_skips_bad_rows() -> None:
    text = (
        '{"translations": ['
        '{"id": "u0001", "english": "Light."},'
        '{"id": "u0002", "english": "Truth."},'
        '{"id": "u0003"},'  # missing english -> skipped
        '{"english": "orphan"}]}'  # missing id -> skipped
    )
    assert parse_translations(text) == {"u0001": "Light.", "u0002": "Truth."}


def test_parse_translations_flattens_stray_newlines() -> None:
    # A model-emitted newline would render as a second physical line and re-parse as
    # an extra unit (structure drift) — it must be flattened to one line.
    text = '{"translations": [{"id": "u0001", "english": "Light\\nand truth."}]}'
    assert parse_translations(text) == {"u0001": "Light and truth."}


def test_profile_schema_describes_every_field() -> None:
    props = profile_format()["json_schema"]["schema"]["properties"]
    assert {"title_en", "tags_en", "terms", "personas"} <= set(props)
    assert all("description" in spec for spec in props.values())
    term = props["terms"]["items"]["properties"]
    assert set(term) == {"source", "target", "note", "locked"}
