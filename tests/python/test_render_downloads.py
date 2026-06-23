from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest

from pancratius import render_downloads
from pancratius.content_catalog import split_frontmatter


def _work_entry(
    tmp_path: Path,
    body: str,
    *,
    kind: str = "book",
    number: int = 1,
    lang: str = "ru",
) -> render_downloads.WorkEntry:
    folder = tmp_path / f"{kind}s" / f"test-{kind}-{number}"
    folder.mkdir(parents=True)
    md = folder / f"{lang}.md"
    md.write_text(
        "\n".join(
            [
                "---",
                f"kind: {kind}",
                f"number: {number}",
                f"slug: test-{kind}-{number}",
                "title: Test Work",
                f"lang: {lang}",
                "---",
                "",
                body,
            ]
        ),
        encoding="utf-8",
    )
    return render_downloads.WorkEntry(
        kind=cast(Any, kind),
        number=number,
        folder=folder,
        lang=cast(Any, lang),
        md=md,
        slug=f"test-{kind}-{number}",
        title="Test Work",
    )


def test_render_epub_refuses_unknown_html_before_pandoc(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry = _work_entry(tmp_path, "<aside>foo</aside>\n")
    pandoc_called = False

    def fake_run(*_args: object, **_kwargs: object) -> None:
        nonlocal pandoc_called
        pandoc_called = True
        raise AssertionError("Pandoc should not run after an HTML allowlist refusal")

    monkeypatch.setattr(render_downloads.subprocess, "run", fake_run)

    with pytest.raises(render_downloads.DownloadRenderError, match="<aside>"):
        render_downloads.render_epub(entry, tmp_path / "scratch")

    assert not pandoc_called


@pytest.mark.parametrize("raw_html", ["<!-- hidden -->", "<!doctype html>", '<?xml version="1.0"?>'])
def test_render_epub_refuses_raw_html_comments_and_declarations_before_pandoc(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    raw_html: str,
) -> None:
    entry = _work_entry(tmp_path, f"{raw_html}\n")
    pandoc_called = False

    def fake_run(*_args: object, **_kwargs: object) -> None:
        nonlocal pandoc_called
        pandoc_called = True
        raise AssertionError("Pandoc should not run after an HTML allowlist refusal")

    monkeypatch.setattr(render_downloads.subprocess, "run", fake_run)

    with pytest.raises(render_downloads.DownloadRenderError) as excinfo:
        render_downloads.render_epub(entry, tmp_path / "scratch")

    assert raw_html in str(excinfo.value)
    assert not pandoc_called


@pytest.mark.parametrize("href", ["javascript:alert(1)", "java&#115;cript:alert(1)"])
def test_render_epub_refuses_unsafe_anchor_href_before_pandoc(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    href: str,
) -> None:
    entry = _work_entry(tmp_path, f'<a href="{href}">x</a>\n')
    pandoc_called = False

    def fake_run(*_args: object, **_kwargs: object) -> None:
        nonlocal pandoc_called
        pandoc_called = True
        raise AssertionError("Pandoc should not run after an HTML allowlist refusal")

    monkeypatch.setattr(render_downloads.subprocess, "run", fake_run)

    with pytest.raises(
        render_downloads.DownloadRenderError,
        match="unsupported href URL scheme 'javascript'",
    ):
        render_downloads.render_epub(entry, tmp_path / "scratch")

    assert not pandoc_called


def test_export_markdown_allows_canonical_html_wrappers(tmp_path: Path) -> None:
    entry = _work_entry(
        tmp_path,
        "\n\n".join(
            [
                '<div class="lineated">\nLineated one  \nLineated two\n</div>',
                '<div class="lineated verse">\nVerse one  \nVerse two\n</div>',
                '<blockquote class="epigraph">\n'
                '<p>Quote <em>text</em><br>next <a href="https://example.test">link</a></p>\n'
                '<footer>Source <span dir="rtl">YHWH</span></footer>\n'
                "</blockquote>",
                '<p class="signature">\nName\n</p>',
            ]
        ),
    )
    dest = tmp_path / "out.md"

    render_downloads._write_export_markdown(entry, dest, {})

    rendered = dest.read_text(encoding="utf-8")
    assert '<div class="lineated">' in rendered
    assert '<div class="lineated verse">' in rendered
    assert '<blockquote class="epigraph">' in rendered
    assert '<p class="signature">' in rendered


@pytest.mark.parametrize("class_name", ["verse", "verse-block"])
def test_export_markdown_refuses_undocumented_div_wrapper(tmp_path: Path, class_name: str) -> None:
    entry = _work_entry(tmp_path, f'<div class="{class_name}">\nLine one  \nLine two\n</div>')
    dest = tmp_path / "out.md"

    with pytest.raises(render_downloads.DownloadRenderError, match="expected class"):
        render_downloads._write_export_markdown(entry, dest, {})


def test_current_work_corpus_download_html_allowlist() -> None:
    failures: list[str] = []
    for root in [
        render_downloads.CONTENT / render_downloads.KIND_DIRS["book"],
        render_downloads.CONTENT / render_downloads.KIND_DIRS["poem"],
    ]:
        for md in sorted(root.rglob("*.md")):
            _frontmatter, body = split_frontmatter(md.read_text(encoding="utf-8"))
            try:
                render_downloads._validate_download_html_allowlist(body.lstrip(), md)
            except render_downloads.DownloadRenderError as exc:
                failures.append(str(exc))

    assert failures == []


def test_render_uses_provided_entries_in_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    book_2 = _work_entry(tmp_path, "Book 2", kind="book", number=2)
    poem_1 = _work_entry(tmp_path, "Poem 1", kind="poem", number=1)
    rendered: list[tuple[str, int, str]] = []

    monkeypatch.setattr(render_downloads, "discover_works", lambda: [])
    monkeypatch.setattr(render_downloads, "_ensure_tools", lambda _formats: None)
    monkeypatch.setattr(render_downloads, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(render_downloads, "CACHE_ROOT", tmp_path / ".cache")
    monkeypatch.setattr(
        render_downloads,
        "render_pdf",
        lambda entry, _scratch_dir: rendered.append((entry.kind, entry.number, "pdf")),
    )
    monkeypatch.setattr(
        render_downloads,
        "render_epub",
        lambda entry, _scratch_dir: rendered.append((entry.kind, entry.number, "epub")),
    )

    render_downloads.render(
        entries=(poem_1, book_2),
        skip_epub=True,
        force=True,
    )

    assert rendered == [("poem", 1, "pdf"), ("book", 2, "pdf")]
