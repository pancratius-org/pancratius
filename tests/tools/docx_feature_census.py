#!/usr/bin/env python3
"""Inventory DOCX constructs in the committed corpus.

This is decision support for the canonical-reader spike, not a production
reader or an audit. It reports package/XML evidence without assigning product
semantics.

Run from the repository root:

    uv run tests/tools/docx_feature_census.py > /tmp/docx-census.json
"""

from __future__ import annotations

import argparse
import json
import re
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
MC_NS = "http://schemas.openxmlformats.org/markup-compatibility/2006"
WP_NS = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
PIC_NS = "http://schemas.openxmlformats.org/drawingml/2006/picture"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
V_NS = "urn:schemas-microsoft-com:vml"
O_NS = "urn:schemas-microsoft-com:office:office"

W = f"{{{W_NS}}}"
R = f"{{{R_NS}}}"
REL = f"{{{REL_NS}}}"
MC = f"{{{MC_NS}}}"

PARTS_OF_INTEREST = (
    "word/document.xml",
    "word/footnotes.xml",
    "word/endnotes.xml",
    "word/comments.xml",
)

SEMANTIC_PARAGRAPH_TAGS = frozenset(
    {
        f"{W}p",
        f"{W}pPr",
        f"{W}r",
        f"{W}rPr",
        f"{W}t",
        f"{W}br",
        f"{W}cr",
        f"{W}tab",
        f"{W}noBreakHyphen",
        f"{W}softHyphen",
        f"{W}lastRenderedPageBreak",
        f"{W}hyperlink",
        f"{W}bookmarkStart",
        f"{W}bookmarkEnd",
        f"{W}proofErr",
        f"{W}footnoteReference",
        f"{W}endnoteReference",
        f"{W}commentReference",
        f"{W}fldChar",
        f"{W}instrText",
        f"{W}fldSimple",
        f"{W}drawing",
        f"{W}pict",
        f"{W}object",
        f"{W}sym",
        f"{W}delText",
        f"{W}ins",
        f"{W}del",
        f"{W}moveFrom",
        f"{W}moveTo",
        f"{W}sdt",
        f"{W}sdtPr",
        f"{W}sdtContent",
        f"{MC}AlternateContent",
        f"{MC}Choice",
        f"{MC}Fallback",
    }
)

STYLE_HEADING = re.compile(r"(?:Heading|heading|Заголовок|[1-9])")


def qname_label(tag: str) -> str:
    if not tag.startswith("{"):
        return tag
    uri, local = tag[1:].split("}", 1)
    prefixes = {
        W_NS: "w",
        R_NS: "r",
        MC_NS: "mc",
        WP_NS: "wp",
        PIC_NS: "pic",
        A_NS: "a",
        V_NS: "v",
    }
    return f"{prefixes.get(uri, uri)}:{local}"


def w_val(element: ET.Element | None) -> str:
    return "" if element is None else str(element.get(f"{W}val") or "")


def enabled(element: ET.Element | None) -> bool:
    return element is not None and element.get(f"{W}val") not in {
        "0",
        "false",
        "False",
        "off",
    }


@dataclass
class Feature:
    occurrences: int = 0
    documents: set[str] = field(default_factory=set)
    examples: list[str] = field(default_factory=list)

    def add(self, document: str, count: int = 1, *, example: str = "") -> None:
        if count <= 0:
            return
        self.occurrences += count
        self.documents.add(document)
        if example and example not in self.examples and len(self.examples) < 8:
            self.examples.append(example)

    def payload(self) -> dict[str, Any]:
        return {
            "documents": len(self.documents),
            "occurrences": self.occurrences,
            "examples": self.examples,
        }


@dataclass
class Census:
    root: Path
    features: dict[str, Feature] = field(default_factory=lambda: defaultdict(Feature))
    element_tags: Counter[str] = field(default_factory=Counter)
    paragraph_styles: Counter[str] = field(default_factory=Counter)
    relationship_types: Counter[str] = field(default_factory=Counter)
    field_instruction_kinds: Counter[str] = field(default_factory=Counter)
    package_parts: Counter[str] = field(default_factory=Counter)
    documents: list[dict[str, Any]] = field(default_factory=list)

    def feature(
        self,
        name: str,
        document: str,
        count: int = 1,
        *,
        example: str = "",
    ) -> None:
        self.features[name].add(document, count, example=example or document)


def relationship_kind(value: str) -> str:
    return value.rsplit("/", 1)[-1]


def paragraph_text(paragraph: ET.Element) -> str:
    pieces: list[str] = []
    for element in paragraph.iter():
        if element.tag in {f"{W}t", f"{W}delText", f"{W}instrText"} and element.text:
            pieces.append(element.text)
        elif element.tag == f"{W}tab":
            pieces.append("\t")
        elif element.tag in {f"{W}br", f"{W}cr"}:
            pieces.append("\n")
    return re.sub(r"[ \t]+", " ", "".join(pieces)).strip()


def excerpt(path: str, paragraph: ET.Element, ordinal: int) -> str:
    text = paragraph_text(paragraph).replace("\n", " / ")
    if len(text) > 90:
        text = text[:87] + "..."
    return f"{path}#p{ordinal}: {text}"


def count_feature(
    census: Census,
    name: str,
    path: str,
    root: ET.Element,
    tag: str,
) -> None:
    count = sum(1 for _ in root.iter(tag))
    census.feature(name, path, count)


def scan_paragraphs(census: Census, path: str, root: ET.Element) -> dict[str, int]:
    stats: Counter[str] = Counter()
    paragraphs = list(root.iter(f"{W}p"))
    for ordinal, paragraph in enumerate(paragraphs):
        ppr = paragraph.find(f"{W}pPr")
        sample = excerpt(path, paragraph, ordinal)
        style = w_val(ppr.find(f"{W}pStyle") if ppr is not None else None)
        if style:
            census.paragraph_styles[style] += 1
            if STYLE_HEADING.fullmatch(style):
                census.feature("heading-style-paragraph", path, example=sample)

        num_pr = ppr.find(f"{W}numPr") if ppr is not None else None
        if num_pr is not None:
            census.feature("direct-numbering", path, example=sample)

        border = ppr.find(f"{W}pBdr") if ppr is not None else None
        if border is not None:
            sides = {
                side
                for side in ("top", "bottom", "left", "right", "between", "bar")
                if (node := border.find(f"{W}{side}")) is not None
                and node.get(f"{W}val", "none") not in {"none", "nil"}
            }
            if sides == {"top", "bottom", "left", "right"}:
                kind = "box"
            elif sides == {"left"}:
                kind = "left-rule"
            else:
                kind = "other"
            census.feature(f"paragraph-border:{kind}", path, example=sample)

        alignment = w_val(ppr.find(f"{W}jc") if ppr is not None else None)
        if alignment:
            census.feature(f"direct-alignment:{alignment}", path, example=sample)
        if enabled(ppr.find(f"{W}pageBreakBefore") if ppr is not None else None):
            census.feature("page-break-before", path, example=sample)
        if ppr is not None and ppr.find(f"{W}contextualSpacing") is not None:
            census.feature("contextual-spacing", path, example=sample)

        for element in paragraph.iter(f"{W}br"):
            kind = element.get(f"{W}type") or "textWrapping"
            census.feature(f"break:{kind}", path, example=sample)
        count = sum(1 for _ in paragraph.iter(f"{W}cr"))
        census.feature("break:carriage-return", path, count, example=sample)

        horizontal_rules = sum(
            1
            for pict in paragraph.iter(f"{W}pict")
            if any(
                descendant.tag == f"{{{V_NS}}}rect"
                and descendant.get(f"{{{O_NS}}}hr") in {"t", "true", "1"}
                for descendant in pict.iter()
            )
        )
        census.feature("vml-horizontal-rule", path, horizontal_rules, example=sample)

        probes = {
            "last-rendered-page-break": f"{W}lastRenderedPageBreak",
            "hyperlink": f"{W}hyperlink",
            "drawing": f"{W}drawing",
            "vml-picture": f"{W}pict",
            "embedded-object": f"{W}object",
            "footnote-reference": f"{W}footnoteReference",
            "endnote-reference": f"{W}endnoteReference",
            "comment-reference": f"{W}commentReference",
            "complex-field": f"{W}fldChar",
            "field-instruction": f"{W}instrText",
            "simple-field": f"{W}fldSimple",
            "symbol": f"{W}sym",
            "tab": f"{W}tab",
            "no-break-hyphen": f"{W}noBreakHyphen",
            "soft-hyphen": f"{W}softHyphen",
            "bookmark": f"{W}bookmarkStart",
            "tracked-insertion": f"{W}ins",
            "tracked-deletion": f"{W}del",
            "move-from": f"{W}moveFrom",
            "move-to": f"{W}moveTo",
            "content-control": f"{W}sdt",
            "alternate-content": f"{MC}AlternateContent",
            "bold": f"{W}b",
            "italic": f"{W}i",
            "underline": f"{W}u",
            "strike": f"{W}strike",
            "vertical-align": f"{W}vertAlign",
            "rtl": f"{W}rtl",
            "bidi": f"{W}bidi",
        }
        for name, tag in probes.items():
            found = sum(1 for _ in paragraph.iter(tag))
            census.feature(name, path, found, example=sample)

        for instruction in paragraph.iter(f"{W}instrText"):
            value = (instruction.text or "").strip()
            match = re.match(r"([A-Za-z]+)", value)
            kind = match.group(1).upper() if match else "(empty)"
            census.field_instruction_kinds[kind] += 1
            census.feature(f"field:{kind}", path, example=sample)
        for simple in paragraph.iter(f"{W}fldSimple"):
            value = str(simple.get(f"{W}instr") or "").strip()
            match = re.match(r"([A-Za-z]+)", value)
            census.field_instruction_kinds[
                f"simple:{match.group(1).upper() if match else '(empty)'}"
            ] += 1

        unknown = {
            qname_label(element.tag)
            for element in paragraph.iter()
            if element.tag not in SEMANTIC_PARAGRAPH_TAGS
            and not element.tag.startswith(f"{W}pPr"[: len(W)])
            and not element.tag.startswith(f"{W}rPr"[: len(W)])
        }
        for tag in unknown:
            census.feature(f"paragraph-extension:{tag}", path, example=sample)

    stats["paragraphs"] = len(paragraphs)
    stats["readable_paragraphs"] = sum(bool(paragraph_text(p)) for p in paragraphs)
    return dict(stats)


def scan_story(census: Census, path: str, part: str, xml: bytes) -> dict[str, int]:
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        census.feature("xml-parse-error", path, example=f"{path}:{part}: {exc}")
        return {}
    for element in root.iter():
        census.element_tags[qname_label(element.tag)] += 1
    stats = scan_paragraphs(census, path, root)
    if part == "word/document.xml":
        body = root.find(f"{W}body")
        if body is not None:
            top = Counter(qname_label(child.tag) for child in body)
            stats.update({f"top_level_{key}": value for key, value in sorted(top.items())})
            census.feature("top-level-table", path, top.get("w:tbl", 0))
            census.feature("top-level-content-control", path, top.get("w:sdt", 0))
            census.feature("alt-chunk", path, top.get("w:altChunk", 0))
        tables = list(root.iter(f"{W}tbl"))
        census.feature("table", path, len(tables))
        nested = sum(1 for table in tables if any(inner is not table for inner in table.iter(f"{W}tbl")))
        census.feature("nested-table", path, nested)
        for name, tag in {
            "horizontal-cell-merge": f"{W}gridSpan",
            "vertical-cell-merge": f"{W}vMerge",
            "table-caption": f"{W}tblCaption",
        }.items():
            count_feature(census, name, path, root, tag)
    return stats


def scan_relationships(census: Census, path: str, xml: bytes) -> None:
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return
    for relation in root.findall(f"{REL}Relationship"):
        kind = relationship_kind(str(relation.get("Type") or ""))
        census.relationship_types[kind] += 1
        target_mode = relation.get("TargetMode") or "Internal"
        census.feature(f"relationship:{kind}:{target_mode}", path)


def normalized_part(name: str) -> str:
    if re.fullmatch(r"word/(?:header|footer)\d+\.xml", name):
        return re.sub(r"\d+", "*", name)
    if name.startswith("word/media/"):
        return "word/media/*"
    if name.startswith("customXml/"):
        return "customXml/*"
    return name


def scan_docx(census: Census, docx: Path) -> None:
    rel = docx.relative_to(census.root).as_posix()
    record: dict[str, Any] = {"path": rel, "bytes": docx.stat().st_size}
    try:
        with zipfile.ZipFile(docx) as archive:
            names = archive.namelist()
            for name in set(names):
                census.package_parts[normalized_part(name)] += 1
            if len(names) != len(set(names)):
                census.feature("duplicate-package-entry", rel)
            for feature, present in {
                "footnotes-part": "word/footnotes.xml" in names,
                "endnotes-part": "word/endnotes.xml" in names,
                "comments-part": "word/comments.xml" in names,
                "numbering-part": "word/numbering.xml" in names,
                "styles-part": "word/styles.xml" in names,
                "settings-part": "word/settings.xml" in names,
                "headers": any(re.fullmatch(r"word/header\d+\.xml", n) for n in names),
                "footers": any(re.fullmatch(r"word/footer\d+\.xml", n) for n in names),
                "media": any(n.startswith("word/media/") for n in names),
                "custom-xml": any(n.startswith("customXml/") for n in names),
            }.items():
                if present:
                    census.feature(feature, rel)

            story_stats: dict[str, dict[str, int]] = {}
            for part in PARTS_OF_INTEREST:
                if part in names:
                    story_stats[part] = scan_story(census, rel, part, archive.read(part))
            for name in names:
                if name.endswith(".rels"):
                    scan_relationships(census, rel, archive.read(name))
            record["stories"] = story_stats
            record["media_files"] = sum(n.startswith("word/media/") for n in names)
    except (OSError, zipfile.BadZipFile, KeyError) as exc:
        record["error"] = str(exc)
        census.feature("package-error", rel, example=f"{rel}: {exc}")
    census.documents.append(record)


def payload(census: Census) -> dict[str, Any]:
    ordered_docs = sorted(census.documents, key=lambda item: str(item["path"]))
    total_paragraphs = sum(
        int(story.get("paragraphs", 0))
        for document in ordered_docs
        for story in document.get("stories", {}).values()
    )
    max_paragraphs = max(
        ordered_docs,
        key=lambda item: int(
            item.get("stories", {}).get("word/document.xml", {}).get("paragraphs", 0)
        ),
        default=None,
    )
    largest = max(ordered_docs, key=lambda item: int(item["bytes"]), default=None)
    return {
        "schema": 1,
        "root": ".",
        "summary": {
            "documents": len(ordered_docs),
            "bytes": sum(int(item["bytes"]) for item in ordered_docs),
            "story_paragraphs": total_paragraphs,
            "largest_document": largest,
            "most_paragraphs": max_paragraphs,
        },
        "features": {
            name: census.features[name].payload()
            for name in sorted(census.features)
        },
        "paragraph_styles": dict(census.paragraph_styles.most_common()),
        "relationship_types": dict(census.relationship_types.most_common()),
        "field_instruction_kinds": dict(census.field_instruction_kinds.most_common()),
        "package_parts": dict(census.package_parts.most_common()),
        "element_tags": dict(census.element_tags.most_common()),
        "documents": ordered_docs,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--content", type=Path, default=Path("src/content"))
    parser.add_argument("--indent", type=int, default=2)
    args = parser.parse_args()
    root = args.root.resolve()
    content = args.content if args.content.is_absolute() else root / args.content
    census = Census(root)
    for docx in sorted(content.rglob("*.docx")):
        scan_docx(census, docx)
    print(json.dumps(payload(census), ensure_ascii=False, indent=args.indent, sort_keys=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
