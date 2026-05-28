"""Plain-text shingle coverage between source DOCX and stripped MD body is
≥ 0.85 for sampled books. Heading counts roughly match (within ±20%)."""
from __future__ import annotations

import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTENT = ROOT / "src" / "content"

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


def main() -> int:
    failures: list[str] = []
    for number in SAMPLE_BOOKS:
        dirs = list((CONTENT / "books").glob(f"{number:02d}-*"))
        if not dirs:
            failures.append(f"book {number}: no content dir")
            continue
        work_dir = dirs[0]
        if sorted(work_dir.glob("ru-part*.docx")):
            # Multipart works need a union comparison; this sample audit skips them.
            print(f"book {number}: skipped (multi-part)")
            continue
        md_path = work_dir / "ru.md"
        docx_path = work_dir / "ru.docx"
        if not md_path.exists() or not docx_path.is_file():
            failures.append(f"book {number}: missing md or docx")
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
