"""Behavioural tests for ``pancratius video sync``.

Tests run offline by injecting a ``_FakeClient`` that implements the same domain
methods as ``YouTubeClient`` (``resolve_channel``, ``list_playlist_video_ids``,
``fetch_videos``, ``list_channel_playlists``). Cleaner than mocking the SDK's
nested chain (``service.channels().list().execute()``) and lets the test file
talk about videos and playlists, not URLs.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from pancratius import video_scan
from pancratius.openrouter import ChatMessage, Completion, ModelId, ModelPricing, Usage
from pancratius.video_channels import (
    ChannelHandleOnly,
    ChannelIdOnly,
    ChannelIdWithHandle,
    ChannelsError,
    VideoChannel,
    load_channels,
    write_channels,
)
from pancratius.video_scan import (
    ChannelLocator,
    ResolvedChannel,
    VideoLocalization,
    VideoMetadata,
    YouTubePlaylist,
)


@dataclass
class _FakeEditorialClient:
    """Returns a canned model reply so scan enrichment is deterministic offline."""

    reply: str

    def complete(
        self,
        *,
        model: ModelId,
        messages: Sequence[ChatMessage],
        temperature: float,
        max_tokens: int,
        response_format: dict[str, Any] | None = None,
        reasoning_max_tokens: int | None = None,
    ) -> Completion:
        del messages, temperature, max_tokens, response_format, reasoning_max_tokens
        return Completion(text=self.reply, usage=Usage(20, 20, 0, 0.001), model=model)

    def fetch_pricing(self, model: ModelId) -> ModelPricing:
        del model
        return ModelPricing(0.1, 0.4, None)


def _single_video_client(description: str) -> _FakeClient:
    return _FakeClient(
        uploads_ids=["vid-1"],
        videos={
            "vid-1": VideoMetadata(
                id="vid-1",
                title="Заголовок видео",
                description=description,
                published_at="2026-02-01",
                duration="PT3M",
                thumbnail_url="https://i.ytimg.com/vi/vid-1/maxresdefault.jpg",
            ),
        },
        playlists=[],
        playlist_members={},
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

    def fetch_videos(
        self, video_ids: Sequence[str], default_lang: str = "ru",
    ) -> dict[str, VideoMetadata]:
        del default_lang
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
                address=ChannelHandleOnly("@test"),
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
        enrich=False,
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
        enrich=False,
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
        enrich=False,
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
        enrich=False,
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


def test_scannable_channel_requires_api_locator(tmp_path: Path) -> None:
    path = tmp_path / "channels.yaml"
    path.write_text(
        """
- id: catalogue-only
  platform: youtube
  url: https://www.youtube.com/@catalogue
  title:
    ru: Каталог
    en: Catalogue
  copy:
    ru: Каталог
    en: Catalogue
""".lstrip(),
        encoding="utf-8",
    )

    with pytest.raises(ChannelsError, match="scan requires channel_id or handle"):
        load_channels(path)


def test_unscanned_catalogue_channel_may_be_url_only(tmp_path: Path) -> None:
    path = tmp_path / "channels.yaml"
    path.write_text(
        """
- id: catalogue-only
  platform: youtube
  url: https://www.youtube.com/@catalogue
  scan: false
  title:
    ru: Каталог
    en: Catalogue
  copy:
    ru: Каталог
    en: Catalogue
""".lstrip(),
        encoding="utf-8",
    )

    [channel] = load_channels(path)
    assert channel.scan is False


def test_scannable_channel_address_values_must_be_non_empty() -> None:
    with pytest.raises(ValueError, match="handle must be non-empty"):
        ChannelHandleOnly(" ")
    with pytest.raises(ValueError, match="channel_id must be non-empty"):
        ChannelIdOnly("")
    with pytest.raises(ValueError, match="handle must be non-empty"):
        ChannelIdWithHandle(channel_id="UC-123", handle="")


def test_scan_attributes_videos_to_playlists_as_tags(
    tmp_path: Path, channels_path: Path,
) -> None:
    video_scan.scan(
        content_root=tmp_path,
        channels_path=channels_path,
        client=_two_videos_client(),
        enrich=False,
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
        enrich=False,
    )
    # 1 resolve + 1 uploads list + 1 videos fetch + 1 playlists list + 2 per-playlist = 6.
    assert result.quota_used == 6


def test_scan_enriches_description_into_hook_and_body(
    tmp_path: Path, channels_path: Path,
) -> None:
    description = (
        "Настоящая мысль о свете внутри. Свет живёт в сердце, а не в правилах.\n\n"
        "📢 Telegram: https://t.me/x\n"
        "💖 Поддержать проект: RUB 2200 1535 2426 2640"
    )
    reply = json.dumps(
        {
            "hook": "Свет живёт в сердце, а не в правилах.",
            "body_markdown": "Настоящая мысль о свете внутри. Свет живёт в сердце, а не в правилах.",
            "dropped": ["promo footer"],
        },
        ensure_ascii=False,
    )
    result = video_scan.scan(
        content_root=tmp_path,
        channels_path=channels_path,
        client=_single_video_client(description),
        editorial_client=_FakeEditorialClient(reply),
    )
    assert result.fallback_videos == []
    md = next((tmp_path / "videos").glob("01-*/ru.md"))
    fm = video_scan._read_frontmatter(md)
    assert fm is not None
    assert fm["description"] == "Свет живёт в сердце, а не в правилах."
    body = md.read_text(encoding="utf-8").split("---\n", 2)[-1]
    assert "Настоящая мысль о свете внутри." in body
    # The promo footer never reaches the file.
    assert "Telegram" not in body and "RUB" not in body and "t.me" not in body


def test_scan_writes_clean_fallback_when_model_returns_junk(
    tmp_path: Path, channels_path: Path,
) -> None:
    # The model leaks a donation link + card number; QA rejects it every attempt,
    # and the file on disk is the clean deterministic fallback (never the junk).
    description = "Ясная мысль о свете и тишине сердца.\n\n💖 https://t.me/x RUB 2200 1535 2426 2640"
    junk_reply = json.dumps(
        {"hook": "Пиши https://t.me/x", "body_markdown": "Жертвуй 2200 1535 2426 2640", "dropped": []},
        ensure_ascii=False,
    )
    result = video_scan.scan(
        content_root=tmp_path,
        channels_path=channels_path,
        client=_single_video_client(description),
        editorial_client=_FakeEditorialClient(junk_reply),
    )
    assert result.fallback_videos == ["main:vid-1"]
    fm = video_scan._read_frontmatter(next((tmp_path / "videos").glob("01-*/ru.md")))
    assert fm is not None
    for leak in ("t.me", "https", "2200", "1535"):
        assert leak not in fm["description"]


@dataclass
class _BilingualEditorial:
    """Returns the Russian or English canned reply based on the LANGUAGE marker the
    prompt carries, so one fake serves both locale calls."""

    ru_reply: str
    en_reply: str

    def complete(
        self,
        *,
        model: ModelId,
        messages: Sequence[ChatMessage],
        temperature: float,
        max_tokens: int,
        response_format: dict[str, Any] | None = None,
        reasoning_max_tokens: int | None = None,
    ) -> Completion:
        del temperature, max_tokens, response_format, reasoning_max_tokens
        text = " ".join(m.content for m in messages)
        reply = self.en_reply if "LANGUAGE: English" in text else self.ru_reply
        return Completion(text=reply, usage=Usage(10, 10, 0, 0.001), model=model)

    def fetch_pricing(self, model: ModelId) -> ModelPricing:
        del model
        return ModelPricing(0.1, 0.4, None)


def _repo_shaped(tmp_path: Path) -> Path:
    """A tmp tree shaped like `<root>/src/content` with sibling `data/` glossaries,
    so the scanner's tag/terminology loaders resolve. Returns the content root."""
    content = tmp_path / "src" / "content"
    (content / "videos").mkdir(parents=True)
    data = tmp_path / "data"
    data.mkdir()
    (data / "tag-glossary.yaml").write_text("en:\n  Апокалипсис: Apocalypse\n", encoding="utf-8")
    (data / "translation-glossary.yaml").write_text(
        "terms:\n  - ru: Панкратиус\n    en:\n      use: Pancratius\n      avoid: [Pankratius]\n"
        "      enforcement: denylist\n",
        encoding="utf-8",
    )
    return content


def test_scan_scaffolds_english_from_localization(tmp_path: Path) -> None:
    content = _repo_shaped(tmp_path)
    channels_path = content / "videos" / "channels.yaml"
    write_channels(
        [VideoChannel(
            key="main", platform="youtube", address=ChannelHandleOnly("@t"),
            url="https://www.youtube.com/@t", title={"ru": "T", "en": "T"},
            copy={"ru": "t", "en": "t"}, badge=None, scan=True, default_lang="ru",
        )],
        channels_path,
    )
    client = _FakeClient(
        uploads_ids=["v1"],
        videos={"v1": VideoMetadata(
            id="v1", title="Слово о свете", description="Свет живёт в сердце, а не в правилах.",
            published_at="2026-02-01", duration="PT3M",
            thumbnail_url="https://i.ytimg.com/vi/v1/maxresdefault.jpg",
            localizations={"en": VideoLocalization(
                title="A Word from Pankratius",
                description="Light lives in the heart, not in rules, Pankratius says.",
            )},
        )},
        playlists=[YouTubePlaylist(id="pl", title="Апокалипсис", item_count=1)],
        playlist_members={"pl": ["v1"]},
    )
    editorial = _BilingualEditorial(
        ru_reply=json.dumps({"hook": "Свет живёт в сердце.", "body_markdown": "Свет живёт в сердце, а не в правилах.", "dropped": []}, ensure_ascii=False),
        en_reply=json.dumps({"hook": "Light lives in the heart.", "body_markdown": "Light lives in the heart, not in rules, Pankratius says.", "dropped": []}, ensure_ascii=False),
    )
    result = video_scan.scan(content_root=content, channels_path=channels_path, client=client, editorial_client=editorial)

    assert result.localized_videos == ["main:v1"]
    folder = next((content / "videos").glob("01-*"))
    en = video_scan._read_frontmatter(folder / "en.md")
    assert en is not None
    assert en["lang"] == "en"
    assert en["title"] == "A Word from Pancratius"  # terminology-normalized title
    assert en["slug"] == "01-a-word-from-pancratius"
    assert en["tags"] == ["Apocalypse"]  # RU playlist mapped through the glossary
    assert "cover" not in en  # EN falls back to the RU cover
    assert en["translation"] == {"source": "literary"}
    body = (folder / "en.md").read_text(encoding="utf-8").split("---\n", 2)[-1]
    assert "Pancratius" in body and "Pankratius" not in body  # body normalized too


def test_scan_falls_back_to_clean_split_without_editorial_client(
    tmp_path: Path, channels_path: Path,
) -> None:
    description = (
        "Ясная мысль о тишине. Тишина глубже слов.\n\n"
        "💖 Поддержать проект: RUB 2200 1535 2426 2640"
    )
    result = video_scan.scan(
        content_root=tmp_path,
        channels_path=channels_path,
        client=_single_video_client(description),
        enrich=False,
    )
    assert result.fallback_videos == ["main:vid-1"]
    md = next((tmp_path / "videos").glob("01-*/ru.md"))
    fm = video_scan._read_frontmatter(md)
    assert fm is not None
    assert fm["description"].startswith("Ясная мысль о тишине")
    # No donation block, no card number leaks into the lede.
    assert "RUB" not in fm["description"] and "Поддержать" not in fm["description"]


# ─────────────────────────────────────────────────────────────────────
# Pure parser/helper tests (no client involved).
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        pytest.param("Оставаться перед Богом #faith #молитва #бог", "Оставаться перед Богом", id="trailing-tags"),
        pytest.param("Свобода от Мамоны #shorts", "Свобода от Мамоны", id="single-tag"),
        pytest.param("Чистый заголовок без тегов", "Чистый заголовок без тегов", id="no-tags"),
        pytest.param("Перестань #kingdomofgod #믿음", "Перестань", id="mixed-script-tag"),
        pytest.param("Послание #51 #shorts", "Послание #51", id="preserve-episode-marker"),
        pytest.param("Послание #shorts #51", "Послание #shorts #51", id="numeric-marker-stops-strip"),
    ],
)
def test_clean_title_strips_trailing_hashtags(title: str, expected: str) -> None:
    assert video_scan._clean_title(title) == expected


def test_parse_localizations_maps_en_us_and_skips_default() -> None:
    item = {
        "localizations": {
            "ru": {"title": "Русский", "description": "..."},
            "en-US": {"title": "English #faith", "description": "The English body."},
            "de": {"title": "Deutsch", "description": "..."},
        }
    }
    locs = video_scan._parse_localizations(item, default_lang="ru")
    assert set(locs) == {"en"}  # ru is the default; de is not a site locale
    assert locs["en"].title == "English"  # hashtags stripped
    assert locs["en"].description == "The English body."


@pytest.mark.parametrize(
    ("iso", "expected"),
    [
        pytest.param("PT2M40S", 160, id="minutes-seconds"),
        pytest.param("PT36S", 36, id="seconds-only"),
        pytest.param("PT1H3M", 3780, id="hours-minutes"),
        pytest.param("PT9M01S", 541, id="zero-padded"),
        pytest.param("garbage", None, id="unparsable"),
    ],
)
def test_iso_duration_seconds(iso: str, expected: int | None) -> None:
    assert video_scan._iso_duration_seconds(iso) == expected


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
