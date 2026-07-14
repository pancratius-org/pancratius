from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from pancratius.ooxml import (
    MC_NS,
    W_NS,
    NamespaceBinding,
    OoxmlNamespaceError,
    PrefixValueReference,
    W,
    parse_xml,
    prefix_value_references_in_xml,
    serialize_xml,
    unresolved_prefix_value_references,
)


def test_serialize_xml_does_not_leak_elementtree_namespace_registry() -> None:
    namespace_map = getattr(ET, "_namespace_map", None)
    assert isinstance(namespace_map, dict)
    before = dict(namespace_map)
    root = ET.Element("{http://example.com/pancratius-test}root")

    payload = serialize_xml(
        root,
        bindings=(NamespaceBinding("pan", "http://example.com/pancratius-test"),),
    )

    assert b"<pan:root" in payload
    assert dict(namespace_map) == before


def test_serializer_does_not_repair_ancestor_from_descendant_alias() -> None:
    xml = (
        f'<w:document xmlns:w="{W_NS}" xmlns:mc="{MC_NS}" '
        'mc:Ignorable="x"><w:body><w:p xmlns:x="urn:local"/></w:body></w:document>'
    ).encode()
    assert unresolved_prefix_value_references(xml) == {"x"}

    with pytest.raises(OoxmlNamespaceError, match=r"unresolved.*x"):
        serialize_xml(parse_xml(xml))


def test_serializer_rejects_conflicting_scoped_prefix_references() -> None:
    xml = (
        f'<w:document xmlns:w="{W_NS}" xmlns:mc="{MC_NS}" '
        'xmlns:x="urn:one" mc:Ignorable="x"><w:body>'
        '<x:first/><w:p xmlns:x="urn:two" mc:Requires="x"><x:second/></w:p>'
        '</w:body></w:document>'
    ).encode()

    with pytest.raises(OoxmlNamespaceError, match=r"conflicting meanings"):
        serialize_xml(parse_xml(xml))


def test_serializer_discards_provenance_with_removed_subtrees() -> None:
    xml = (
        f'<w:document xmlns:w="{W_NS}" xmlns:mc="{MC_NS}">'
        '<w:body><w:p xmlns:x="urn:one" mc:Ignorable="x"/>'
        '<w:p xmlns:x="urn:two" mc:Ignorable="x"/></w:body></w:document>'
    ).encode()
    tree = parse_xml(xml)
    body = tree.root.find(f"{W}body")
    assert body is not None
    body.remove(body.findall(f"{W}p")[1])

    projected = serialize_xml(tree)

    assert prefix_value_references_in_xml(projected) == (
        PrefixValueReference("x", "urn:one"),
    )


def test_serializer_preserves_an_irrelevant_descendant_rebinding() -> None:
    xml = (
        f'<w:document xmlns:w="{W_NS}" xmlns:mc="{MC_NS}" '
        'xmlns:x="urn:one" mc:Ignorable="x"><w:body>'
        '<x:first/><w:p xmlns:x="urn:two"><x:second/></w:p>'
        '</w:body></w:document>'
    ).encode()

    projected = serialize_xml(parse_xml(xml))

    assert prefix_value_references_in_xml(projected) == (
        PrefixValueReference("x", "urn:one"),
    )


def test_serializer_repairs_missing_known_common_prefix() -> None:
    wps_uri = "http://schemas.microsoft.com/office/word/2010/wordprocessingShape"
    xml = (
        f'<w:document xmlns:w="{W_NS}" xmlns:mc="{MC_NS}" '
        'mc:Ignorable="wps"><w:body/></w:document>'
    ).encode()
    assert unresolved_prefix_value_references(xml) == {"wps"}

    projected = serialize_xml(parse_xml(xml))

    assert unresolved_prefix_value_references(projected) == set()
    assert f'xmlns:wps="{wps_uri}"'.encode() in projected


def test_serializer_understands_implicit_xml_and_unprefixed_qnames() -> None:
    xml = (
        f'<w:document xmlns:w="{W_NS}" xmlns:mc="{MC_NS}" '
        'mc:PreserveAttributes="xml:lang local"><w:body/></w:document>'
    ).encode()

    projected = serialize_xml(parse_xml(xml))

    assert prefix_value_references_in_xml(projected) == (
        PrefixValueReference("xml", "http://www.w3.org/XML/1998/namespace"),
    )


def test_serializer_ignores_similarly_named_domain_attributes() -> None:
    xml = b'<x:root xmlns:x="urn:example" x:Requires="literal"/>'

    projected = serialize_xml(parse_xml(xml))

    assert prefix_value_references_in_xml(projected) == ()


def test_serializer_escapes_repaired_namespace_uris() -> None:
    xml = (
        f'<w:document xmlns:w="{W_NS}" xmlns:mc="{MC_NS}" '
        'mc:Ignorable="x"><w:body/></w:document>'
    ).encode()

    projected = serialize_xml(
        parse_xml(xml),
        bindings=(NamespaceBinding("x", "urn:a&b"),),
    )

    assert prefix_value_references_in_xml(projected) == (
        PrefixValueReference("x", "urn:a&b"),
    )


def test_serializer_rejects_unknown_lexical_prefix() -> None:
    root = ET.fromstring(
        f'<w:document xmlns:w="{W_NS}" xmlns:mc="{MC_NS}" '
        'mc:Ignorable="unknown"/>'
    )

    with pytest.raises(OoxmlNamespaceError, match="unknown"):
        serialize_xml(root)
