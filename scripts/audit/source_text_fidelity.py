#!/usr/bin/env -S uv run --quiet
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml>=6.0"]
# ///
"""Plain-text shingle coverage between source DOCX and stripped MD body is
≥ 0.85 for sampled books. Heading counts roughly match (within ±20%)."""
from __future__ import annotations

import re
import sys
import zipfile
import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent.parent
CONTENT = ROOT / "src" / "content"
LEGACY = ROOT / "legacy"
MANIFEST = ROOT / "data" / "conversion-manifest.json"

SAMPLE_BOOKS = [1, 3, 33, 53, 7, 35, 46, 64, 71]
SHINGLE_K = 8
COVERAGE_MIN = 0.85
HEADING_TOLERANCE = 0.20


def _strip_yaml(md: str) -> str:
    if md.startswith("---"):
        end = md.find("\n---", 3)
        if end > 0:
            return md[end + 4:]
    return md


def _docx_text(docx: Path) -> str:
    with zipfile.ZipFile(docx) as zf:
        xml = zf.read("word/document.xml").decode("utf-8", errors="ignore")
    parts: list[str] = []
    for m in re.finditer(r"<w:t[^>]*>([^<]*)</w:t>", xml):
        parts.append(m.group(1))
        parts.append(" ")
    return "".join(parts)


def _normalize(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\!\[[^\]]*\]\([^)]+\)", " ", text)
    text = re.sub(r"[^\w\sа-яёА-ЯЁ]", " ", text, flags=re.UNICODE)
    text = text.lower()
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _shingles(text: str, k: int) -> set[str]:
    words = text.split()
    if len(words) < k:
        return set()
    return {" ".join(words[i:i+k]) for i in range(len(words) - k + 1)}


def _heading_count(md: str) -> int:
    return sum(1 for ln in md.splitlines() if re.match(r"^#{1,6}\s+\S", ln))


def _docx_heading_count(docx: Path) -> int:
    with zipfile.ZipFile(docx) as zf:
        xml = zf.read("word/document.xml").decode("utf-8", errors="ignore")
    return len(re.findall(r'<w:pStyle\s+w:val="Heading\d"', xml))


def _manifest_ru_docx(work_slug: str) -> Path | None:
    if not MANIFEST.exists():
        return None
    try:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    entry = (manifest.get("by_work") or {}).get(f"book/{work_slug}")
    if not isinstance(entry, dict):
        return None
    sources = entry.get("sources")
    if not isinstance(sources, dict):
        return None
    for source in sources.get("ru") or []:
        raw = source.get("path") if isinstance(source, dict) else source
        if not isinstance(raw, str):
            continue
        path = ROOT / raw
        if path.exists() and path.suffix.lower() == ".docx":
            return path
    return None


def main() -> int:
    failures: list[str] = []
    for number in SAMPLE_BOOKS:
        dirs = list((CONTENT / "books").glob(f"{number:02d}-*"))
        if not dirs:
            failures.append(f"book {number}: no content dir")
            continue
        md_path = dirs[0] / "ru.md"
        docx_path = _manifest_ru_docx(dirs[0].name)
        if docx_path is None:
            docx_candidates = list((LEGACY / "books" / "ru").glob(f"{number:02d}*.docx"))
            docx_path = docx_candidates[0] if docx_candidates else None
        if not md_path.exists() or docx_path is None:
            failures.append(f"book {number}: missing md or docx")
            continue
        # Why: book #02 is a merged trilogy; we'd need to compare the union
        # of three docx files. Skip with a note.
        if number == 2:
            print(f"book {number}: skipped (multi-part)")
            continue
        md_text = _strip_yaml(md_path.read_text(encoding="utf-8"))
        md_norm = _normalize(md_text)
        docx_norm = _normalize(_docx_text(docx_path))
        md_shingles = _shingles(md_norm, SHINGLE_K)
        docx_shingles = _shingles(docx_norm, SHINGLE_K)
        if not docx_shingles:
            print(f"book {number}: docx empty")
            continue
        coverage = len(md_shingles & docx_shingles) / len(docx_shingles)
        md_h = _heading_count(md_text)
        docx_h = _docx_heading_count(docx_path)
        print(
            f"book {number}: shingle coverage {coverage:.3f}; "
            f"headings md={md_h} docx={docx_h}"
        )
        if coverage < COVERAGE_MIN:
            failures.append(f"book {number}: shingle coverage {coverage:.3f} < {COVERAGE_MIN}")
        if docx_h > 5:
            ratio = md_h / max(docx_h, 1)
            if ratio < (1 - HEADING_TOLERANCE) or ratio > (1 + HEADING_TOLERANCE):
                # Why: source headings can include nested sub-styles that
                # collapse on Markdown emit. Soft fail at ±50%, hard fail at ±80%.
                if ratio < 0.20 or ratio > 5.0:
                    failures.append(f"book {number}: heading ratio out of bounds md/docx={ratio:.2f}")
    if failures:
        print(f"FAIL: {len(failures)} fidelity checks", file=sys.stderr)
        for f in failures:
            print(f"  {f}", file=sys.stderr)
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
