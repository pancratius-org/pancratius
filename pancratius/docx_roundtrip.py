from __future__ import annotations

import copy
import json
import re
import shutil
import subprocess
import tempfile
import unicodedata
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from html import unescape
from pathlib import Path
from typing import Any, Literal

from pancratius import import_docx
from pancratius.content_catalog import CatalogEntry, scan_catalog, split_frontmatter
from pancratius.kinds import SEGMENT_OF
from pancratius.locales import Locale
from pancratius.pandoc import PandocNotFoundError, pandoc_argv0
from pancratius.paths import CONTENT_ROOT
from pancratius.writeplan import Diagnostic

RoundTripFindingSeverity = Literal["fatal", "warning", "info"]
RoundTripVerdict = Literal["pass", "fail"]

PANDOC_TIMEOUT_SECONDS = 300
PANDOC_MARKDOWN_FORMAT = "gfm+footnotes+raw_html+yaml_metadata_block"

_CYRILLIC_RE = re.compile(r"[А-Яа-яЁё]")
_MIXED_SCRIPT_TOKEN_RE = re.compile(r"\b(?=\w*[A-Za-z])(?=\w*[А-Яа-яЁё])\w+\b")
_SIGNATURE_HTML_RE = re.compile(
    r"(?ms)^\s*<p\b[^>]*\bclass=[\"']signature[\"'][^>]*>\s*(.*?)\s*</p>\s*$"
)
_LINEATED_BLOCK_RE = re.compile(
    r"(?is)<div\b[^>]*\bclass=[\"'][^\"']*\blineated\b[^\"']*[\"'][^>]*>\s*(.*?)\s*</div\s*>"
)
_MARKDOWN_IMAGE_RE = re.compile(
    r"!\[(?P<alt>[^\]]*)\]\(\s*(?P<target><[^>]+>|[^)\s]+)(?:\s+[\"'][^)]*[\"'])?\s*\)"
)
_HTML_IMG_RE = re.compile(r"(?is)<img\b[^>]*>")
_HTML_ATTR_RE = re.compile(r"""\b(?P<name>[A-Za-z_:][-A-Za-z0-9_:.]*)\s*=\s*(?P<quote>["'])(?P<value>.*?)\2""")
_IGNORED_FRONTMATTER_PATHS = frozenset({
    "translation.model",
    "translation.generated_at",
})
type DocxRoundTripProgress = Callable[[int, int, DocxRoundTripTarget], None]


class DocxRoundTripError(Exception):
    """The DOCX round-trip diagnostic cannot run."""


@dataclass(frozen=True, slots=True)
class DocxRoundTripTarget:
    """One committed DOCX/Markdown pair checked through the importer."""

    entry: CatalogEntry
    md_path: Path
    docx_path: Path

    @property
    def selector(self) -> str:
        return f"{self.entry.kind}:{self.entry.number}"

    @property
    def label(self) -> str:
        return f"{self.entry.kind}-{self.entry.number:02d}"


@dataclass(frozen=True, slots=True)
class DocxRoundTripFinding:
    """One reason a round-trip passed with notes or failed."""

    severity: RoundTripFindingSeverity
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class DocxRoundTripReport:
    """Comparison result for one target."""

    target: DocxRoundTripTarget
    verdict: RoundTripVerdict
    findings: tuple[DocxRoundTripFinding, ...]
    committed_chars: int
    imported_chars: int

    @property
    def failed(self) -> bool:
        return self.verdict == "fail"


@dataclass(frozen=True, slots=True)
class DocxRoundTripBatch:
    """Corpus-wide round-trip result."""

    reports: tuple[DocxRoundTripReport, ...]
    checked: int
    missing_docx: int
    missing_md: int
    coverage_required: bool

    @property
    def failed(self) -> bool:
        return (
            any(report.failed for report in self.reports)
            or (self.coverage_required and (self.missing_docx > 0 or self.missing_md > 0))
        )

    @property
    def passed(self) -> int:
        return sum(1 for report in self.reports if not report.failed)

    @property
    def failed_count(self) -> int:
        return sum(1 for report in self.reports if report.failed)


@dataclass(frozen=True, slots=True)
class StagedDocxRoundTripWorkspace:
    """A temp content copy reused by staged DOCX checks."""

    temp_content_root: Path

    def check(
        self,
        *,
        entry: CatalogEntry,
        md_path: Path,
        docx_path: Path,
        lang: Locale = "en",
    ) -> DocxRoundTripReport:
        _require_book_roundtrip_entry(entry)
        target = DocxRoundTripTarget(entry=entry, md_path=md_path, docx_path=docx_path)
        return _roundtrip_one(target, self.temp_content_root, lang=lang)


@contextmanager
def staged_docx_roundtrip_workspace(
    *,
    content_root: Path = CONTENT_ROOT,
) -> Iterator[StagedDocxRoundTripWorkspace]:
    root = content_root.expanduser().resolve()
    with tempfile.TemporaryDirectory(prefix="pancratius-docx-roundtrip-") as td:
        temp_content = Path(td) / "src" / "content"
        _copy_content_root(root, temp_content)
        yield StagedDocxRoundTripWorkspace(temp_content)


def check_docx_markdown_roundtrip(
    *,
    content_root: Path = CONTENT_ROOT,
    lang: Locale = "en",
    book: int | None = None,
    limit: int = 0,
    progress: DocxRoundTripProgress | None = None,
) -> DocxRoundTripBatch:
    """Import committed ``<lang>.docx`` files into a temp content root and compare
    the generated ``<lang>.md`` against the committed one.

    The function never writes into ``content_root``. It copies the content tree to
    a temp root so importer decisions that depend on the real catalog, covers, and
    authored frontmatter are exercised without touching source files.
    """
    if limit < 0:
        raise DocxRoundTripError("--limit must be non-negative.")
    if book is not None and limit:
        raise DocxRoundTripError("--limit cannot be combined with an explicit book:NN selector.")
    root = content_root.expanduser().resolve()
    targets, missing_docx, missing_md = _discover_targets(root, lang=lang, book=book)
    if book is not None and not targets:
        raise DocxRoundTripError(f"book-{book:02d} has no committed {lang}.docx/{lang}.md pair.")
    if limit:
        targets = targets[:limit]
    if not targets:
        return DocxRoundTripBatch(
            (),
            checked=0,
            missing_docx=missing_docx,
            missing_md=missing_md,
            coverage_required=limit == 0 and book is None,
        )

    with tempfile.TemporaryDirectory(prefix="pancratius-docx-roundtrip-") as td:
        temp_content = Path(td) / "src" / "content"
        _copy_content_root(root, temp_content)
        reports_list: list[DocxRoundTripReport] = []
        total = len(targets)
        for index, target in enumerate(targets, start=1):
            if progress is not None:
                progress(index, total, target)
            reports_list.append(_roundtrip_one(target, temp_content, lang=lang))
        reports = tuple(reports_list)
    return DocxRoundTripBatch(
        reports=reports,
        checked=len(reports),
        missing_docx=missing_docx,
        missing_md=missing_md,
        coverage_required=limit == 0 and book is None,
    )


def check_staged_docx_markdown_roundtrip(
    *,
    content_root: Path = CONTENT_ROOT,
    entry: CatalogEntry,
    md_path: Path,
    docx_path: Path,
    lang: Locale = "en",
) -> DocxRoundTripReport:
    """Check one staged DOCX against committed Markdown without writing content."""
    with staged_docx_roundtrip_workspace(content_root=content_root) as workspace:
        return workspace.check(entry=entry, md_path=md_path, docx_path=docx_path, lang=lang)


def print_roundtrip_batch(batch: DocxRoundTripBatch, *, json_output: bool = False) -> None:
    if json_output:
        print(json.dumps(_batch_payload(batch), ensure_ascii=False, indent=2))
        return
    print(
        f"docx roundtrip-md: {batch.checked} checked, {batch.passed} passed, "
        f"{batch.failed_count} failed; {batch.missing_docx} missing docx, {batch.missing_md} missing md."
    )
    for report in batch.reports:
        status = "FAIL" if report.failed else "PASS"
        print(
            f"  {status} {report.target.label}: {report.target.docx_path} "
            f"({report.imported_chars}/{report.committed_chars} imported chars)"
        )
        for finding in report.findings:
            print(f"      {finding.severity}: [{finding.code}] {finding.message}")


def _discover_targets(
    content_root: Path,
    *,
    lang: Locale,
    book: int | None,
) -> tuple[list[DocxRoundTripTarget], int, int]:
    targets: list[DocxRoundTripTarget] = []
    missing_docx = 0
    missing_md = 0
    for entry in sorted(scan_catalog(content_root), key=lambda item: (item.kind, item.number, item.lang)):
        if entry.kind != "book" or entry.lang != lang:
            continue
        if book is not None and entry.number != book:
            continue
        md_path = entry.md_path
        docx_path = entry.work_dir / f"{lang}.docx"
        if not md_path.is_file():
            missing_md += 1
            continue
        if not docx_path.is_file():
            missing_docx += 1
            continue
        targets.append(DocxRoundTripTarget(entry=entry, md_path=md_path, docx_path=docx_path))
    missing_md += _count_docx_without_markdown(content_root, lang=lang, book=book)
    return targets, missing_docx, missing_md


def _count_docx_without_markdown(content_root: Path, *, lang: Locale, book: int | None) -> int:
    books_root = content_root / "books"
    if not books_root.is_dir():
        return 0
    missing = 0
    for docx_path in books_root.glob(f"*/{lang}.docx"):
        if book is not None and _book_number_from_dir(docx_path.parent) != book:
            continue
        if not (docx_path.parent / f"{lang}.md").is_file():
            missing += 1
    return missing


def _book_number_from_dir(path: Path) -> int | None:
    match = re.match(r"^(\d+)-", path.name)
    if match is None:
        return None
    return int(match.group(1))


def _require_book_roundtrip_entry(entry: CatalogEntry) -> None:
    if entry.kind != "book":
        raise DocxRoundTripError("staged DOCX roundtrip supports book entries only.")


def _copy_content_root(src: Path, dst: Path) -> None:
    shutil.copytree(
        src,
        dst,
        ignore=shutil.ignore_patterns(".DS_Store"),
    )


def _roundtrip_one(
    target: DocxRoundTripTarget,
    temp_content: Path,
    *,
    lang: Locale,
) -> DocxRoundTripReport:
    staged_md = temp_content / SEGMENT_OF[target.entry.kind] / target.entry.work_dir.name / f"{lang}.md"
    findings: list[DocxRoundTripFinding] = []
    try:
        report = import_docx.import_work(import_docx.ImportRequest.for_explicit_work(
            docx=target.docx_path,
            lang=lang,
            out_content=temp_content,
            kind="book",
            number=target.entry.number,
            replace=True,
        ))
    except import_docx.ImportWorkError as exc:
        committed = target.md_path.read_text(encoding="utf-8") if target.md_path.is_file() else ""
        return DocxRoundTripReport(
            target=target,
            verdict="fail",
            findings=(DocxRoundTripFinding("fatal", "roundtrip.import-error", str(exc)),),
            committed_chars=len(committed),
            imported_chars=0,
        )
    findings.extend(_writer_findings(tuple(report.diagnostics)))
    if report.refused:
        findings.append(DocxRoundTripFinding(
            "fatal",
            "roundtrip.import-refused",
            "the importer refused the temporary round-trip write",
        ))
    if not staged_md.is_file():
        findings.append(DocxRoundTripFinding(
            "fatal",
            "roundtrip.missing-imported-md",
            f"import did not produce {staged_md}",
        ))
        committed = target.md_path.read_text(encoding="utf-8")
        return _report(target, committed, "", findings)

    committed = target.md_path.read_text(encoding="utf-8")
    imported = staged_md.read_text(encoding="utf-8")
    findings.extend(compare_markdown_pair(
        committed,
        imported,
        lang=lang,
        committed_dir=target.md_path.parent,
        imported_dir=staged_md.parent,
    ))
    return _report(target, committed, imported, findings)


def _writer_findings(diagnostics: tuple[Diagnostic, ...]) -> list[DocxRoundTripFinding]:
    findings: list[DocxRoundTripFinding] = []
    for diagnostic in diagnostics:
        if diagnostic.severity == "info":
            continue
        severity: RoundTripFindingSeverity = "fatal" if diagnostic.severity == "fatal" else "warning"
        findings.append(DocxRoundTripFinding(
            severity,
            diagnostic.code,
            diagnostic.message,
        ))
    return findings


def compare_markdown_pair(
    committed: str,
    imported: str,
    *,
    lang: Locale,
    committed_dir: Path | None = None,
    imported_dir: Path | None = None,
) -> tuple[DocxRoundTripFinding, ...]:
    committed_fm, committed_body = split_frontmatter(committed)
    imported_fm, imported_body = split_frontmatter(imported)
    findings: list[DocxRoundTripFinding] = []
    findings.extend(_frontmatter_findings(committed_fm, imported_fm))
    findings.extend(_body_findings(
        committed_body,
        imported_body,
        lang=lang,
        committed_dir=committed_dir,
        imported_dir=imported_dir,
    ))
    return tuple(findings)


def _frontmatter_findings(
    committed: dict[str, Any],
    imported: dict[str, Any],
) -> list[DocxRoundTripFinding]:
    findings: list[DocxRoundTripFinding] = []
    committed_visible = _public_frontmatter(committed)
    imported_visible = _public_frontmatter(imported)
    if committed_visible != imported_visible:
        paths = _changed_paths(committed_visible, imported_visible)
        findings.append(DocxRoundTripFinding(
            "fatal",
            "roundtrip.frontmatter-drift",
            "public frontmatter changed: " + ", ".join(paths[:12]),
        ))
    ignored = _changed_paths(committed, imported, include_only=_IGNORED_FRONTMATTER_PATHS)
    if ignored:
        findings.append(DocxRoundTripFinding(
            "info",
            "roundtrip.ignored-frontmatter-drift",
            "ignored bootstrap metadata changed: " + ", ".join(ignored),
        ))
    return findings


def _public_frontmatter(data: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(data)
    translation = out.get("translation")
    if isinstance(translation, dict):
        for key in ("model", "generated_at"):
            translation.pop(key, None)
    return out


def _body_findings(
    committed: str,
    imported: str,
    *,
    lang: Locale,
    committed_dir: Path | None,
    imported_dir: Path | None,
) -> list[DocxRoundTripFinding]:
    findings: list[DocxRoundTripFinding] = []
    if committed == imported:
        findings.append(DocxRoundTripFinding("info", "roundtrip.byte-identical", "Markdown body is byte-identical."))
        return findings
    structure_findings = _lineation_structure_findings(committed, imported)
    findings.extend(structure_findings)
    findings.extend(_image_reference_findings(
        committed,
        imported,
        committed_dir=committed_dir,
        imported_dir=imported_dir,
    ))

    committed_plain = _plain_markdown(committed)
    imported_plain = _plain_markdown(imported)
    committed_visible = _normalize_visible_text(committed_plain)
    imported_visible = _normalize_visible_text(imported_plain)
    if committed_visible == imported_visible:
        if not structure_findings:
            findings.append(DocxRoundTripFinding(
                "warning",
                "roundtrip.markdown-format-drift",
                "visible text is stable, but Markdown serialization changed.",
            ))
    elif _normalize_human_text(committed_plain) == _normalize_human_text(imported_plain):
        findings.append(DocxRoundTripFinding(
            "warning",
            "roundtrip.typography-drift",
            "text differs only by quote/footnote spacing typography.",
        ))
    else:
        findings.append(DocxRoundTripFinding(
            "fatal",
            "roundtrip.visible-text-drift",
            _text_drift_message(committed_plain, imported_plain),
        ))

    findings.extend(_script_findings(committed_plain, imported_plain, lang=lang))
    return findings


def _image_reference_findings(
    committed: str,
    imported: str,
    *,
    committed_dir: Path | None,
    imported_dir: Path | None,
) -> list[DocxRoundTripFinding]:
    committed_refs = _image_refs(committed)
    imported_refs = _image_refs(imported)
    if committed_refs == imported_refs:
        return []
    if _image_refs_payload_equal(
        committed_refs,
        imported_refs,
        committed_dir=committed_dir,
        imported_dir=imported_dir,
    ):
        return []
    return [DocxRoundTripFinding(
        "fatal",
        "roundtrip.image-reference-drift",
        _image_reference_drift_message(committed_refs, imported_refs),
    )]


def _image_refs(markdown: str) -> tuple[tuple[str, str], ...]:
    refs: list[tuple[str, str]] = []
    for match in _MARKDOWN_IMAGE_RE.finditer(markdown):
        refs.append((
            unescape(match.group("alt")).strip(),
            _normalize_image_target(match.group("target")),
        ))
    for match in _HTML_IMG_RE.finditer(markdown):
        tag = match.group(0)
        target = _html_attr(tag, "src")
        if target is None:
            continue
        refs.append((
            _html_attr(tag, "alt") or "",
            _normalize_image_target(target),
        ))
    return tuple(refs)


def _image_refs_payload_equal(
    committed_refs: tuple[tuple[str, str], ...],
    imported_refs: tuple[tuple[str, str], ...],
    *,
    committed_dir: Path | None,
    imported_dir: Path | None,
) -> bool:
    if committed_dir is None or imported_dir is None or len(committed_refs) != len(imported_refs):
        return False
    for expected, actual in zip(committed_refs, imported_refs, strict=True):
        if expected[0] != actual[0]:
            return False
        if expected[1] == actual[1]:
            continue
        expected_path = _resolve_local_image_ref(committed_dir, expected[1])
        actual_path = _resolve_local_image_ref(imported_dir, actual[1])
        if expected_path is None or actual_path is None:
            return False
        try:
            if expected_path.read_bytes() == actual_path.read_bytes():
                continue
        except OSError:
            return False
        if not _image_pixels_equal(expected_path, actual_path):
            return False
    return True


def _image_pixels_equal(left: Path, right: Path) -> bool:
    try:
        from PIL import Image, ImageChops
    except ImportError:
        return False
    try:
        with Image.open(left) as left_image, Image.open(right) as right_image:
            left_pixels = left_image.convert("RGBA")
            right_pixels = right_image.convert("RGBA")
    except OSError:
        return False
    return left_pixels.size == right_pixels.size and ImageChops.difference(left_pixels, right_pixels).getbbox() is None


def _resolve_local_image_ref(base_dir: Path, target: str) -> Path | None:
    if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", target) or target.startswith("//"):
        return None
    clean = target.split("#", 1)[0].split("?", 1)[0]
    if not clean:
        return None
    return (base_dir / clean).resolve()


def _html_attr(tag: str, name: str) -> str | None:
    for match in _HTML_ATTR_RE.finditer(tag):
        if match.group("name").lower() == name:
            return unescape(match.group("value")).strip()
    return None


def _normalize_image_target(target: str) -> str:
    target = unescape(target).strip()
    if target.startswith("<") and target.endswith(">"):
        target = target[1:-1].strip()
    return target


def _image_reference_drift_message(
    committed_refs: tuple[tuple[str, str], ...],
    imported_refs: tuple[tuple[str, str], ...],
) -> str:
    if len(committed_refs) != len(imported_refs):
        return f"image reference count changed: expected {len(committed_refs)}, imported {len(imported_refs)}"
    for index, (expected, actual) in enumerate(zip(committed_refs, imported_refs, strict=True), start=1):
        if expected != actual:
            return f"image reference {index} changed: expected {expected!r}, imported {actual!r}"
    return "image references changed"


def _lineation_structure_findings(committed: str, imported: str) -> list[DocxRoundTripFinding]:
    committed_shape = _lineation_shape(committed)
    imported_shape = _lineation_shape(imported)
    if committed_shape == imported_shape:
        return []
    if committed_shape and not imported_shape:
        return [DocxRoundTripFinding(
            "fatal",
            "roundtrip.lineation-structure-drift",
            "lineated block structure was lost: "
            f"expected {_lineation_shape_summary(committed_shape)}, imported none",
        )]
    return [DocxRoundTripFinding(
        "warning",
        "roundtrip.lineation-structure-drift",
        "lineated block shape changed: "
        f"expected {_lineation_shape_summary(committed_shape)}, imported {_lineation_shape_summary(imported_shape)}",
    )]


def _lineation_shape_summary(shape: tuple[tuple[int, ...], ...]) -> str:
    blocks = len(shape)
    stanzas = sum(len(block) for block in shape)
    lines = sum(sum(block) for block in shape)
    return f"{blocks} block(s), {stanzas} stanza(s), {lines} line(s)"


def _lineation_shape(markdown: str) -> tuple[tuple[int, ...], ...]:
    blocks: list[tuple[int, ...]] = []
    for match in _LINEATED_BLOCK_RE.finditer(markdown):
        stanza_lengths: list[int] = []
        current_lines = 0
        for raw_line in match.group(1).splitlines():
            if not raw_line.strip():
                if current_lines:
                    stanza_lengths.append(current_lines)
                    current_lines = 0
                continue
            current_lines += 1
        if current_lines:
            stanza_lengths.append(current_lines)
        blocks.append(tuple(stanza_lengths))
    return tuple(blocks)


def _script_findings(
    committed_plain: str,
    imported_plain: str,
    *,
    lang: Locale,
) -> list[DocxRoundTripFinding]:
    if lang != "en":
        return []
    findings: list[DocxRoundTripFinding] = []
    committed_cyr = len(_CYRILLIC_RE.findall(committed_plain))
    imported_cyr = len(_CYRILLIC_RE.findall(imported_plain))
    if imported_cyr > committed_cyr:
        findings.append(DocxRoundTripFinding(
            "fatal",
            "roundtrip.added-cyrillic",
            f"English import added {imported_cyr - committed_cyr} Cyrillic character(s).",
        ))
    committed_mixed = set(_MIXED_SCRIPT_TOKEN_RE.findall(committed_plain))
    imported_mixed = set(_MIXED_SCRIPT_TOKEN_RE.findall(imported_plain))
    added_mixed = sorted(imported_mixed - committed_mixed)
    if added_mixed:
        sample = ", ".join(added_mixed[:8])
        findings.append(DocxRoundTripFinding(
            "fatal",
            "roundtrip.added-mixed-script-token",
            f"English import added mixed-script token(s): {sample}",
        ))
    return findings


def _plain_markdown(markdown: str) -> str:
    markdown = _project_visible_raw_html(markdown)
    try:
        proc = subprocess.run(
            [pandoc_argv0(), "--from", PANDOC_MARKDOWN_FORMAT, "--to", "plain"],
            input=markdown,
            capture_output=True,
            text=True,
            check=False,
            timeout=PANDOC_TIMEOUT_SECONDS,
        )
    except (FileNotFoundError, PandocNotFoundError) as exc:
        raise DocxRoundTripError(
            "pandoc not found; run `uv sync` or install it with `brew install pandoc`."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise DocxRoundTripError("pandoc timed out while reading Markdown for round-trip comparison.") from exc
    if proc.returncode != 0:
        raise DocxRoundTripError(f"pandoc failed while reading Markdown: {proc.stderr.strip()}")
    return proc.stdout


def _project_visible_raw_html(markdown: str) -> str:
    def signature_replacement(match: re.Match[str]) -> str:
        signature = unescape(re.sub(r"<[^>]+>", "", match.group(1)))
        lines = [line.strip() for line in signature.splitlines() if line.strip()]
        return "\n\n" + " ".join(lines) + "\n\n"

    return _SIGNATURE_HTML_RE.sub(signature_replacement, markdown)


def _normalize_visible_text(value: str) -> str:
    text = unicodedata.normalize("NFC", value).replace("\u00a0", " ")
    text = text.replace("\u200b", "")
    return re.sub(r"\s+", " ", text).strip()


def _normalize_human_text(value: str) -> str:
    text = _normalize_visible_text(value)
    text = text.translate(str.maketrans({
        "«": '"',
        "»": '"',
        "“": '"',
        "”": '"',
        "„": '"',
        "‟": '"',
        "‘": "'",
        "’": "'",
        "‚": "'",
        "‛": "'",
        "…": "...",
    }))
    return re.sub(r"\s+(\[\d+\])", r"\1", text)


def _text_drift_message(committed_plain: str, imported_plain: str) -> str:
    committed_norm = _normalize_human_text(committed_plain)
    imported_norm = _normalize_human_text(imported_plain)
    prefix = _common_prefix_len(committed_norm, imported_norm)
    expected = _excerpt(committed_norm, prefix)
    actual = _excerpt(imported_norm, prefix)
    return f"visible text changed near char {prefix}: expected {expected!r}, imported {actual!r}"


def _common_prefix_len(left: str, right: str) -> int:
    limit = min(len(left), len(right))
    for index in range(limit):
        if left[index] != right[index]:
            return index
    return limit


def _excerpt(text: str, index: int, *, radius: int = 80) -> str:
    lo = max(0, index - radius)
    hi = min(len(text), index + radius)
    return text[lo:hi]


def _changed_paths(
    left: object,
    right: object,
    *,
    prefix: str = "",
    include_only: frozenset[str] | None = None,
) -> list[str]:
    if include_only is not None and prefix and not any(path == prefix or path.startswith(f"{prefix}.") for path in include_only):
        return []
    if isinstance(left, dict) and isinstance(right, dict):
        out: list[str] = []
        for key in sorted(set(left) | set(right)):
            child = f"{prefix}.{key}" if prefix else str(key)
            out.extend(_changed_paths(left.get(key), right.get(key), prefix=child, include_only=include_only))
        return out
    if left != right and (include_only is None or prefix in include_only):
        return [prefix or "<root>"]
    return []


def _report(
    target: DocxRoundTripTarget,
    committed: str,
    imported: str,
    findings: list[DocxRoundTripFinding],
) -> DocxRoundTripReport:
    verdict: RoundTripVerdict = (
        "fail" if any(finding.severity == "fatal" for finding in findings) else "pass"
    )
    return DocxRoundTripReport(
        target=target,
        verdict=verdict,
        findings=tuple(findings),
        committed_chars=len(committed),
        imported_chars=len(imported),
    )


def _batch_payload(batch: DocxRoundTripBatch) -> dict[str, object]:
    return {
        "checked": batch.checked,
        "passed": batch.passed,
        "failed": batch.failed_count,
        "missing_docx": batch.missing_docx,
        "missing_md": batch.missing_md,
        "coverage_required": batch.coverage_required,
        "reports": [_report_payload(report) for report in batch.reports],
    }


def _report_payload(report: DocxRoundTripReport) -> dict[str, object]:
    return {
        "selector": report.target.selector,
        "label": report.target.label,
        "lang": report.target.entry.lang,
        "work_key": report.target.entry.work_key,
        "md_path": str(report.target.md_path),
        "docx_path": str(report.target.docx_path),
        "verdict": report.verdict,
        "committed_chars": report.committed_chars,
        "imported_chars": report.imported_chars,
        "findings": [
            {
                "severity": finding.severity,
                "code": finding.code,
                "message": finding.message,
            }
            for finding in report.findings
        ],
    }


def exit_code(batch: DocxRoundTripBatch) -> int:
    if batch.failed:
        return 1
    return 0
