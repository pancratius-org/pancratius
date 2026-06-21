from __future__ import annotations

from pancratius.docx_optimize import (
    MAX_LONG_EDGE,
    DisplayRectEmu,
    parse_display_rects,
    per_image_long_edge,
)


def test_parse_display_rects_keeps_largest_media_extent() -> None:
    document_xml = b"""
    <w:document>
      <w:drawing>
        <wp:extent cx="100" cy="400"/>
        <a:blip r:embed="rId1"/>
      </w:drawing>
      <w:drawing>
        <wp:extent cx="300" cy="300"/>
        <a:blip r:embed="rId1"/>
      </w:drawing>
      <w:drawing>
        <wp:extent cx="999" cy="999"/>
        <a:blip r:embed="missing"/>
      </w:drawing>
    </w:document>
    """
    rels_xml = b"""
    <Relationships>
      <Relationship Id="rId1" Type="image" Target="media/image1.png"/>
    </Relationships>
    """

    assert parse_display_rects(document_xml, rels_xml) == {
        "image1.png": DisplayRectEmu(cx=300, cy=300)
    }


def test_per_image_long_edge_uses_display_rect_value_object() -> None:
    assert per_image_long_edge(None) == MAX_LONG_EDGE
    assert per_image_long_edge(DisplayRectEmu(cx=914400, cy=1828800)) == 576
