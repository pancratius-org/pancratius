"""Local QA: export translated DOCX files with LibreOffice.

It checks LibreOffice load/export behavior, not Word compatibility or visual
equality. The script works from scratch copies, writes PDFs only under scratch
or ``--out-dir``, and never touches ``src/content``.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
WORK_COLLECTIONS = ("books", "poetry")
PDF_RENDER_TIMEOUT_SECONDS = 180

type TranslatedDocxPath = Path


@dataclass(frozen=True, slots=True)
class LibreOfficeOpenResult:
    docx: TranslatedDocxPath
    ok: bool
    pdf: Path | None
    message: str


def _find_soffice() -> str | None:
    from pancratius.docx_render import find_soffice

    return find_soffice()


def _translated_docx_paths(content_root: Path, *, lang: str, limit: int) -> tuple[TranslatedDocxPath, ...]:
    paths = sorted(
        path
        for collection in WORK_COLLECTIONS
        for path in (content_root / collection).glob(f"*/{lang}.docx")
    )
    if limit:
        return tuple(paths[:limit])
    return tuple(paths)


def _stable_output_dir(out_root: Path, docx: Path, *, content_root: Path) -> Path:
    rel_parent = docx.parent.relative_to(content_root)
    return out_root / rel_parent


def _is_relative_to(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
    except ValueError:
        return False
    return True


def _process_output(completed: subprocess.CompletedProcess[str]) -> str:
    detail = "\n".join(part.strip() for part in (completed.stdout, completed.stderr) if part.strip())
    return detail or f"LibreOffice exited {completed.returncode}"


def _without_pdf_paths(results: tuple[LibreOfficeOpenResult, ...]) -> tuple[LibreOfficeOpenResult, ...]:
    return tuple(
        LibreOfficeOpenResult(
            docx=result.docx,
            ok=result.ok,
            pdf=None,
            message=result.message,
        )
        for result in results
    )


def _convert_one(
    docx: TranslatedDocxPath,
    *,
    soffice: str,
    content_root: Path,
    out_root: Path,
) -> LibreOfficeOpenResult:
    output_dir = _stable_output_dir(out_root, docx, content_root=content_root)
    output_dir.mkdir(parents=True, exist_ok=True)
    pdf = output_dir / f"{docx.stem}.pdf"
    if pdf.exists() or pdf.is_symlink():
        if not pdf.is_file() or pdf.is_symlink():
            return LibreOfficeOpenResult(
                docx=docx,
                ok=False,
                pdf=None,
                message=f"expected PDF path is not a regular file: {pdf}",
            )
        pdf.unlink()
    with tempfile.TemporaryDirectory(prefix="pancratius-lo-open-") as td:
        work_dir = Path(td)
        source_copy = work_dir / docx.name
        shutil.copy2(docx, source_copy)
        profile = work_dir / "profile"
        profile.mkdir()
        try:
            completed = subprocess.run(
                [
                    soffice,
                    "--headless",
                    "--nologo",
                    "--nodefault",
                    "--nofirststartwizard",
                    "--nolockcheck",
                    f"-env:UserInstallation={profile.as_uri()}",
                    "--convert-to",
                    "pdf",
                    "--outdir",
                    str(output_dir),
                    str(source_copy),
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=PDF_RENDER_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            return LibreOfficeOpenResult(
                docx=docx,
                ok=False,
                pdf=None,
                message="LibreOffice conversion timed out",
            )
    if completed.returncode != 0:
        return LibreOfficeOpenResult(
            docx=docx,
            ok=False,
            pdf=None,
            message=_process_output(completed),
        )
    if not pdf.is_file():
        return LibreOfficeOpenResult(
            docx=docx,
            ok=False,
            pdf=None,
            message=f"LibreOffice produced no PDF. {_process_output(completed)}",
        )
    if pdf.stat().st_size == 0:
        return LibreOfficeOpenResult(
            docx=docx,
            ok=False,
            pdf=pdf,
            message="LibreOffice produced an empty PDF",
        )
    return LibreOfficeOpenResult(docx=docx, ok=True, pdf=pdf, message="ok")


def run_check(
    *,
    repo_root: Path = ROOT,
    content_root: Path | None = None,
    lang: str = "en",
    limit: int = 0,
    out_dir: Path | None = None,
) -> tuple[LibreOfficeOpenResult, ...]:
    if limit < 0:
        raise ValueError("--limit must be non-negative")
    root = repo_root.resolve()
    resolved_content_root = (content_root or root / "src" / "content").resolve()
    soffice = _find_soffice()
    if soffice is None:
        raise RuntimeError("LibreOffice soffice not found")
    targets = _translated_docx_paths(resolved_content_root, lang=lang, limit=limit)
    if not targets:
        raise RuntimeError(f"no {lang}.docx files found under {resolved_content_root}")
    if out_dir is not None:
        output_root = out_dir.expanduser().resolve()
        if _is_relative_to(output_root, resolved_content_root):
            raise ValueError("--out-dir must not be inside the content root")
        output_root.mkdir(parents=True, exist_ok=True)
        return tuple(
            _convert_one(target, soffice=soffice, content_root=resolved_content_root, out_root=output_root)
            for target in targets
        )
    with tempfile.TemporaryDirectory(prefix="pancratius-docx-pdf-open-") as td:
        output_root = Path(td)
        results = tuple(
            _convert_one(target, soffice=soffice, content_root=resolved_content_root, out_root=output_root)
            for target in targets
        )
        return _without_pdf_paths(results)


def _payload(results: tuple[LibreOfficeOpenResult, ...]) -> dict[str, Any]:
    return {
        "checked": len(results),
        "passed": sum(1 for result in results if result.ok),
        "failed": sum(1 for result in results if not result.ok),
        "results": [
            {
                "docx": str(result.docx),
                "ok": result.ok,
                "pdf": str(result.pdf) if result.pdf is not None else None,
                "message": result.message,
            }
            for result in results
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--content-root",
        default=str(ROOT / "src" / "content"),
        help="Content tree containing books/ and poetry/ directories.",
    )
    parser.add_argument("--lang", default="en", help="Translated DOCX language code to check.")
    parser.add_argument("--limit", type=int, default=0, help="Check only the first N files.")
    parser.add_argument("--out-dir", type=Path, help="Keep rendered PDFs under this directory.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable results.")
    args = parser.parse_args(argv)
    try:
        results = run_check(
            content_root=Path(args.content_root),
            lang=args.lang,
            limit=args.limit,
            out_dir=args.out_dir,
        )
    except (RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    payload = _payload(results)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(
            f"libreoffice-open: {payload['checked']} checked, "
            f"{payload['passed']} passed, {payload['failed']} failed"
        )
        for result in results:
            status = "PASS" if result.ok else "FAIL"
            print(f"  {status} {result.docx}")
            if not result.ok:
                print(f"      {result.message}")
    return 1 if any(not result.ok for result in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
