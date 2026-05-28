"""Behavioural tests for ``pancratius video sync``.

Tests run offline by injecting a ``_FakeClient`` that implements the same domain
methods as ``YouTubeClient`` (``resolve_channel``, ``list_playlist_video_ids``,
``fetch_videos``, ``list_channel_playlists``). Cleaner than mocking the SDK's
nested chain (``service.channels().list().execute()``) and lets the test file
talk about videos and playlists, not URLs.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from pancratius import video_scan
from pancratius.video_channels import VideoChannel, write_channels
from pancratius.video_scan import (
    ChannelLocator,
    ResolvedChannel,
    VideoMetadata,
    YouTubePlaylist,
)

# ─────────────────────────────────────────────────────────────────────
# Fake client. Implements the YouTubeClient method surface; the scanner
# never sees an HTTP request.
# ─────────────────────────────────────────────────────────────────────


@dataclass
class _FakeClient:
    """In-memory stand-in for the SDK-backed YouTubeClient."""
    resolved: ResolvedChannel = field(
        default_factory=lambda: ResolvedChannel("UC-FAKE", "UU-UPLOADS")
    )
    uploads_ids: list[str] = field(default_factory=list)
    videos: dict[str, VideoMetadata] = field(default_factory=dict)
    playlists: list[YouTubePlaylist] = field(default_factory=list)
    playlist_members: dict[str, list[str]] = field(default_factory=dict)
    quota_used: int = 0

    def resolve_channel(self, locator: ChannelLocator) -> ResolvedChannel:
        del locator
        self.quota_used += 1
        return self.resolved

    def list_playlist_video_ids(self, playlist_id: str) -> list[str]:
        self.quota_used += 1
        if playlist_id == self.resolved.uploads_playlist_id:
            return list(self.uploads_ids)
        return list(self.playlist_members.get(playlist_id, []))

    def fetch_videos(self, video_ids: Sequence[str]) -> dict[str, VideoMetadata]:
        self.quota_used += 1
        return {vid: self.videos[vid] for vid in video_ids if vid in self.videos}

    def list_channel_playlists(self, channel_id: str) -> list[YouTubePlaylist]:
        del channel_id
        self.quota_used += 1
        return list(self.playlists)


def _two_videos_client() -> _FakeClient:
    """Two videos: an older one in two playlists, a newer one in one."""
    return _FakeClient(
        uploads_ids=["vid-newer-22", "vid-older-11"],  # any order — scanner sorts
        videos={
            "vid-older-11": VideoMetadata(
                id="vid-older-11",
                title="Старое видео про любовь",
                description="Длинное описание. " * 4,
                published_at="2025-12-01",
                duration="PT4M12S",
                thumbnail_url="https://i.ytimg.com/vi/vid-older-11/maxresdefault.jpg",
            ),
            "vid-newer-22": VideoMetadata(
                id="vid-newer-22",
                title="Новое видео про свет",
                description="Короткая аннотация.",
                published_at="2026-01-15",
                duration="PT9M01S",
                thumbnail_url="https://i.ytimg.com/vi/vid-newer-22/maxresdefault.jpg",
            ),
        },
        playlists=[
            YouTubePlaylist(id="pl-1", title="Евангелие Царствия", item_count=2),
            YouTubePlaylist(id="pl-2", title="Апокалипсис", item_count=1),
        ],
        playlist_members={
            "pl-1": ["vid-older-11", "vid-newer-22"],
            "pl-2": ["vid-newer-22"],
        },
    )


# ─────────────────────────────────────────────────────────────────────
# Fixtures.
# ─────────────────────────────────────────────────────────────────────


@pytest.fixture
def channels_path(tmp_path: Path) -> Path:
    """Seed a single-channel ``channels.yaml`` under ``tmp_path`` and return
    its path. ``tmp_path`` doubles as the content root in these tests."""
    (tmp_path / "videos").mkdir(parents=True, exist_ok=True)
    path = tmp_path / "videos" / "channels.yaml"
    write_channels(
        [
            VideoChannel(
                key="main",
                platform="youtube",
                handle="@test",
                channel_id=None,
                url="https://www.youtube.com/@test",
                title={"ru": "Тестовый", "en": "Test"},
                copy={"ru": "Тест", "en": "Test"},
                badge=None,
                scan=True,
                default_lang="ru",
            ),
        ],
        path,
    )
    return path


# ─────────────────────────────────────────────────────────────────────
# scan() behavioural tests.
# ─────────────────────────────────────────────────────────────────────


def test_scan_scaffolds_new_videos_in_publication_order(
    tmp_path: Path, channels_path: Path,
) -> None:
    result = video_scan.scan(
        content_root=tmp_path,
        channels_path=channels_path,
        client=_two_videos_client(),
    )
    assert len(result.new_videos) == 2
    folder_names = sorted(
        f.name for f in (tmp_path / "videos").iterdir() if f.is_dir()
    )
    # 01- (oldest) and 02- (newest) — sorted by snippet.publishedAt.
    assert any(n.startswith("01-staroe") for n in folder_names)
    assert any(n.startswith("02-novoe") for n in folder_names)


def test_scan_is_idempotent_and_preserves_editor_edits(
    tmp_path: Path, channels_path: Path,
) -> None:
    video_scan.scan(
        content_root=tmp_path,
        channels_path=channels_path,
        client=_two_videos_client(),
    )
    # Edit one scaffolded file to verify the second scan never touches it.
    folders = [f for f in (tmp_path / "videos").iterdir() if f.is_dir()]
    target_md = next(folders[0].glob("*.md"))
    target_md.write_text(
        target_md.read_text(encoding="utf-8")
        + "\n\n# EDITORIAL\n\nHand-written commentary.\n",
        encoding="utf-8",
    )
    result2 = video_scan.scan(
        content_root=tmp_path,
        channels_path=channels_path,
        client=_two_videos_client(),
    )
    assert result2.new_videos == []
    assert "EDITORIAL" in target_md.read_text(encoding="utf-8")


def test_scan_dry_run_writes_nothing(
    tmp_path: Path, channels_path: Path,
) -> None:
    result = video_scan.scan(
        content_root=tmp_path,
        channels_path=channels_path,
        client=_two_videos_client(),
        dry_run=True,
    )
    folders = [f for f in (tmp_path / "videos").iterdir() if f.is_dir()]
    assert folders == []
    assert len(result.new_videos) == 2


def test_scan_requires_api_key_when_no_client_injected(
    tmp_path: Path, channels_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    with pytest.raises(video_scan.VideoScanError, match="YOUTUBE_API_KEY"):
        video_scan.scan(
            content_root=tmp_path,
            channels_path=channels_path,
        )


def test_scan_attributes_videos_to_playlists_as_tags(
    tmp_path: Path, channels_path: Path,
) -> None:
    video_scan.scan(
        content_root=tmp_path,
        channels_path=channels_path,
        client=_two_videos_client(),
    )
    # vid-newer-22 is in BOTH playlists; vid-older-11 is in one.
    newer_md = next((tmp_path / "videos").glob("02-*/ru.md"))
    fm = video_scan._read_frontmatter(newer_md)
    assert fm is not None
    assert sorted(fm["tags"]) == ["Апокалипсис", "Евангелие Царствия"]


def test_scan_reports_real_quota_used(
    tmp_path: Path, channels_path: Path,
) -> None:
    result = video_scan.scan(
        content_root=tmp_path,
        channels_path=channels_path,
        client=_two_videos_client(),
    )
    # 1 resolve + 1 uploads list + 1 videos fetch + 1 playlists list + 2 per-playlist = 6.
    assert result.quota_used == 6


# ─────────────────────────────────────────────────────────────────────
# Pure parser/helper tests (no client involved).
# ─────────────────────────────────────────────────────────────────────


def test_truncate_description_handles_long_text() -> None:
    long = "Это первое предложение. " * 30
    out = video_scan._truncate_description(long, limit=120)
    assert len(out) <= 121
    assert out.endswith(".") or out.endswith("…")


@pytest.mark.parametrize(
    "text",
    [pytest.param("", id="empty"), pytest.param("   \n\t  ", id="whitespace-only")],
)
def test_truncate_description_falls_back_to_todo(text: str) -> None:
    assert video_scan._truncate_description(text, limit=240).startswith("TODO")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param("2026-01-15T10:00:00Z", "2026-01-15", id="iso-datetime"),
        pytest.param("2026-01-15", "2026-01-15", id="bare-iso-date"),
        pytest.param("not-a-date", None, id="non-date-string"),
        pytest.param(None, None, id="none"),
        pytest.param("", None, id="empty-string"),
    ],
)
def test_extract_published_at(value: object, expected: str | None) -> None:
    assert video_scan._extract_published_at(value) == expected


@pytest.mark.parametrize(
    ("snippet", "yt_id", "expected"),
    [
        pytest.param(
            {
                "thumbnails": {
                    "default": {"url": "https://i.ytimg.com/vi/X/default.jpg"},
                    "high": {"url": "https://i.ytimg.com/vi/X/hqdefault.jpg"},
                    "maxres": {"url": "https://i.ytimg.com/vi/X/maxres-real.jpg"},
                }
            },
            "X",
            "https://i.ytimg.com/vi/X/maxres-real.jpg",
            id="prefers-highest-rendition",
        ),
        pytest.param(
            {"thumbnails": {"default": {"url": "https://i.ytimg.com/vi/X/default.jpg"}}},
            "X",
            "https://i.ytimg.com/vi/X/default.jpg",
            id="falls-back-to-smaller-rendition",
        ),
        pytest.param(
            {},
            "X",
            "https://i.ytimg.com/vi/X/maxresdefault.jpg",
            id="falls-back-to-cdn-guess-when-thumbnails-absent",
        ),
    ],
)
def test_best_thumbnail_url(snippet: dict[str, Any], yt_id: str, expected: str) -> None:
    assert video_scan._best_thumbnail_url(snippet, yt_id) == expected


def test_read_frontmatter_tolerates_crlf(tmp_path: Path) -> None:
    md = tmp_path / "crlf.md"
    md.write_text("---\r\nkind: video\r\nnumber: 7\r\n---\r\nbody\r\n", encoding="utf-8")
    assert video_scan._read_frontmatter(md) == {"kind": "video", "number": 7}


def test_parse_video_returns_none_when_required_fields_missing() -> None:
    # Missing duration → unusable.
    item = {
        "id": "abc",
        "snippet": {"title": "x", "publishedAt": "2026-01-01T00:00:00Z"},
        "contentDetails": {},
    }
    assert video_scan._parse_video(item) is None


def test_parse_video_returns_metadata_for_well_formed_item() -> None:
    item = {
        "id": "abc",
        "snippet": {
            "title": "Hello",
            "description": "World",
            "publishedAt": "2026-01-01T12:00:00Z",
            "thumbnails": {"maxres": {"url": "https://i.ytimg.com/vi/abc/maxresdefault.jpg"}},
        },
        "contentDetails": {"duration": "PT3M"},
    }
    meta = video_scan._parse_video(item)
    assert meta is not None
    assert meta.id == "abc"
    assert meta.title == "Hello"
    assert meta.published_at == "2026-01-01"
    assert meta.duration == "PT3M"
