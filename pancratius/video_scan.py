"""YouTube channel scanner — the metadata side of ``pancratius video sync``.

What this module does:

  1. Reads ``src/content/videos/channels.yaml``; for each ``scan: true`` channel
     resolves handle → channel id → uploads playlist via the YouTube Data API.
  2. Enumerates the uploads playlist (paginated through ``list_next``).
  3. Fetches full ``snippet,contentDetails`` for new IDs in 50-batches.
  4. Maps each new video to the channel playlists that contain it.
  5. Sorts new IDs by ``snippet.publishedAt`` (publication order; the upload
     playlist's own order is not documented) and scaffolds ``<lang>.md`` per
     video plus a ``cover.<lang>.jpg`` thumbnail.

The raw YouTube description is discovery copy, not reading copy, so the scaffold
does not dump it into the page. It goes through
:func:`pancratius.video_description.draft_description`, which splits it into a
clean hook (frontmatter ``description``) and a reading ``body`` — the author's
own words, junk removed, faithful and QA-gated. The scanner does not write a
curated ``title``, ``cross_refs``, or ``related_book``.

Re-runs are idempotent by video ID. Editor edits to existing entries are never
touched.

Auth: ``YOUTUBE_API_KEY`` (Data API v3) for the scan; ``OPENROUTER_API_KEY`` for
the description split — absent the latter, the split uses its deterministic
fallback. Uses ``google-api-python-client`` so pagination (``list_next``), HTTP
retries, and structured ``HttpError``s are handled by the SDK.
"""

from __future__ import annotations

import logging
import os
import re
import urllib.error
import urllib.request
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field, replace
from itertools import batched
from pathlib import Path
from typing import Any, Protocol, assert_never

import yaml
from googleapiclient.discovery import build as _build_service
from googleapiclient.errors import HttpError

from pancratius.docx_conversion import to_ascii_slug
from pancratius.locales import LOCALES, Locale
from pancratius.localization import (
    TagLabels,
    TermReplacements,
    load_tag_labels,
    load_term_replacements,
    normalize_locale_text,
    youtube_keys_for_locale,
)
from pancratius.openrouter import LLMClient, Usage
from pancratius.paths import CONTENT_ROOT, data_root_for_content_root
from pancratius.video_channels import (
    CHANNELS_PATH,
    ChannelHandleOnly,
    ChannelIdOnly,
    ChannelIdWithHandle,
    VideoChannel,
    load_channels,
)
from pancratius.video_description import (
    DescriptionConfig,
    DescriptionDraft,
    SplitMethod,
    VideoContext,
    client_from_env,
    draft_description,
)

logger = logging.getLogger(__name__)

THUMBNAIL_BASE = "https://i.ytimg.com/vi"
# Single match captures the frontmatter header text between the opening and
# closing ``---`` fences. CRLF-tolerant by anchoring on optional ``\r``.
FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.DOTALL)
ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# PEP 695 — keep the units explicit so a YouTube video id never accidentally
# lands in a slot meant for a playlist id or a slug.
type VideoId = str
type PlaylistId = str

# Untyped JSON-API payload shape; pattern-matching `case` blocks narrow at use.
type JSONObject = dict[str, Any]


@dataclass(frozen=True, slots=True)
class PlaylistRef:
    """A playlist a video belongs to: source id + this-locale title. Seeds the
    video's frontmatter ``playlists`` and (by title) its ``tags``."""

    id: PlaylistId
    title: str


type PlaylistAttribution = dict[VideoId, list[PlaylistRef]]


class VideoScanError(RuntimeError):
    """Raised on a scan-side failure (network, API, or malformed response)."""


# A YouTube channel is addressed by *either* a channel id or a handle.


@dataclass(frozen=True, slots=True)
class ChannelId:
    value: str


@dataclass(frozen=True, slots=True)
class ChannelHandle:
    value: str


type ChannelLocator = ChannelId | ChannelHandle


def _channel_locator(channel: VideoChannel) -> ChannelLocator:
    """Pick the strongest available addressor; channel_id is canonical."""
    match channel.require_scannable_address():
        case ChannelIdWithHandle(channel_id=channel_id) | ChannelIdOnly(channel_id):
            return ChannelId(channel_id)
        case ChannelHandleOnly(handle):
            return ChannelHandle(handle)


class _SDKRequest(Protocol):
    """The single `execute()` shape we use from `googleapiclient.HttpRequest`."""

    def execute(self) -> JSONObject: ...


class VideoClient(Protocol):
    """The domain method surface the scanner depends on."""

    quota_used: int

    def resolve_channel(self, locator: ChannelLocator) -> ResolvedChannel: ...

    def list_playlist_video_ids(self, playlist_id: PlaylistId) -> list[VideoId]: ...

    def fetch_videos(
        self, video_ids: Sequence[VideoId], default_lang: Locale = "ru",
    ) -> dict[VideoId, VideoMetadata]: ...

    def list_channel_playlists(self, channel_id: str) -> list[YouTubePlaylist]: ...


# Domain types — what `YouTubeClient` returns.


@dataclass(frozen=True, slots=True)
class ResolvedChannel:
    channel_id: str
    uploads_playlist_id: str


@dataclass(frozen=True, slots=True)
class VideoLocalization:
    """An author-provided title + description in a non-default language (YouTube
    localizations), title already stripped of discovery hashtags."""
    title: str
    description: str


@dataclass(frozen=True, slots=True)
class VideoMetadata:
    """One YouTube video, post-validation: `published_at` is `YYYY-MM-DD`,
    `duration` is the ISO 8601 string YouTube returns. `title` is the default
    locale title with trailing hashtags dropped; `localizations` holds any
    author-provided non-default variants, keyed by locale."""
    id: VideoId
    title: str
    description: str
    published_at: str
    duration: str
    thumbnail_url: str
    localizations: dict[Locale, VideoLocalization] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class YouTubePlaylist:
    id: PlaylistId
    title: str
    item_count: int


@dataclass(frozen=True, slots=True)
class LocaleScaffold:
    """One language's authored fields for a video: the title, the hook/body split
    (``draft``), and the playlists in that language (their titles seed the tags)."""
    lang: Locale
    title: str
    draft: DescriptionDraft
    playlists: tuple[PlaylistRef, ...]


@dataclass(frozen=True, slots=True)
class NewVideo:
    """One scaffold work item the writer turns into a bundle on disk. One
    :class:`LocaleScaffold` per language present. Only the default locale's
    ``cover.<lang>.jpg`` is fetched; other locales fall back to it."""
    number: int
    folder: str
    yt_id: VideoId
    published_at: str
    duration: str
    channel_key: str
    thumbnail_url: str
    default_lang: Locale
    locales: tuple[LocaleScaffold, ...]


class YouTubeClient:
    """`quota_used` counts one unit per list call; daily free quota is 10,000."""

    def __init__(self, api_key: str) -> None:
        # cache_discovery=False keeps httplib2 from writing a cache file into CWD.
        self._service: Any = _build_service(
            "youtube", "v3", developerKey=api_key, cache_discovery=False,
        )
        self.quota_used: int = 0

    def resolve_channel(self, locator: ChannelLocator) -> ResolvedChannel:
        match locator:
            case ChannelId(value):
                request = self._service.channels().list(part="contentDetails", id=value)
            case ChannelHandle(value):
                request = self._service.channels().list(part="contentDetails", forHandle=value)
            case _:
                assert_never(locator)
        items = self._execute(request).get("items") or []
        if not items:
            raise VideoScanError(f"no YouTube channel found for {locator}")
        match items[0]:
            case {
                "id": str(cid),
                "contentDetails": {"relatedPlaylists": {"uploads": str(uploads)}},
            } if uploads:
                return ResolvedChannel(channel_id=cid, uploads_playlist_id=uploads)
            case _:
                raise VideoScanError("channel response missing id or uploads playlist")

    def list_playlist_video_ids(self, playlist_id: PlaylistId) -> list[VideoId]:
        """Page through any playlist's video IDs via `list_next`."""
        request = self._service.playlistItems().list(
            part="contentDetails", playlistId=playlist_id, maxResults=50,
        )
        ids: list[VideoId] = []
        while request is not None:
            resp = self._execute(request)
            for item in resp.get("items") or []:
                match item:
                    case {"contentDetails": {"videoId": str(vid)}} if vid:
                        ids.append(vid)
            request = self._service.playlistItems().list_next(request, resp)
        return ids

    def fetch_videos(
        self, video_ids: Sequence[VideoId], default_lang: Locale = "ru",
    ) -> dict[VideoId, VideoMetadata]:
        """Batch `videos.list` (50/batch). Skips items missing required fields.
        `localizations` carries the author's non-default-language title/description."""
        out: dict[VideoId, VideoMetadata] = {}
        for batch in batched(video_ids, 50, strict=False):
            request = self._service.videos().list(
                part="snippet,contentDetails,localizations", id=",".join(batch),
            )
            for item in self._execute(request).get("items") or []:
                meta = _parse_video(item, default_lang)
                if meta is not None:
                    out[meta.id] = meta
        return out

    def list_channel_playlists(self, channel_id: str) -> list[YouTubePlaylist]:
        """Channel playlists with at least one item."""
        request = self._service.playlists().list(
            part="snippet,contentDetails", channelId=channel_id, maxResults=50,
        )
        out: list[YouTubePlaylist] = []
        while request is not None:
            resp = self._execute(request)
            for item in resp.get("items") or []:
                match item:
                    case {
                        "id": str(pid),
                        "snippet": {"title": str(title)},
                        "contentDetails": {"itemCount": int(count)},
                    } if count > 0:
                        out.append(YouTubePlaylist(id=pid, title=title, item_count=count))
            request = self._service.playlists().list_next(request, resp)
        return out

    def _execute(self, request: _SDKRequest) -> JSONObject:
        self.quota_used += 1
        try:
            payload = request.execute()
        except HttpError as exc:
            status = getattr(exc.resp, "status", "?")
            details = getattr(exc, "error_details", None) or exc.reason
            raise VideoScanError(f"YouTube API HTTP {status}: {details}") from None
        if not isinstance(payload, dict):
            raise VideoScanError("YouTube API response is not an object")
        return payload


# A trailing run of discovery hashtags. Each must contain a letter, so a numeric
# episode marker like "#51" is preserved, not mistaken for a tag.
_TRAILING_HASHTAGS = re.compile(r"(?:\s+#(?=[^\s#]*[^\W\d_])[^\s#]+)+\s*$")

def _clean_title(title: str) -> str:
    """Drop trailing discovery hashtags (`… #faith #молитва`) that belong on
    YouTube, not on a reading page. Leading and inline text is untouched."""
    return _TRAILING_HASHTAGS.sub("", title).strip()


def _parse_localizations(item: object, default_lang: Locale) -> dict[Locale, VideoLocalization]:
    """Author-provided non-default-language title/description pairs. For each site
    locale (other than the default, which is the snippet already), the first
    available YouTube key in preference order wins; a localization missing a
    non-empty title or description is skipped."""
    out: dict[Locale, VideoLocalization] = {}
    match item:
        case {"localizations": dict() as localizations}:
            by_key = {k: v for k, v in localizations.items() if isinstance(k, str)}
            for locale in LOCALES:
                if locale == default_lang:
                    continue
                for key in youtube_keys_for_locale(locale):
                    match by_key.get(key):
                        case {"title": str(title), "description": str(description)} if title.strip() and description.strip():
                            out[locale] = VideoLocalization(
                                title=_clean_title(title) or title.strip(), description=description,
                            )
                            break
    return out


def _parse_video(item: object, default_lang: Locale = "ru") -> VideoMetadata | None:
    """Structurally narrow a `videos.list` item; None for missing/malformed."""
    match item:
        case {
            "id": str(vid),
            "snippet": dict() as snippet,
            "contentDetails": {"duration": str(duration)},
        } if duration:
            published_at = _extract_published_at(snippet.get("publishedAt"))
            if published_at is None:
                return None
            title = _clean_title(str(snippet.get("title", "")).strip())
            return VideoMetadata(
                id=vid,
                title=title or f"Video {vid}",
                description=str(snippet.get("description", "")),
                published_at=published_at,
                duration=duration,
                thumbnail_url=_best_thumbnail_url(snippet, vid),
                localizations=_parse_localizations(item, default_lang),
            )
        case _:
            return None


def _extract_published_at(value: object) -> str | None:
    """Coerce `snippet.publishedAt` to `YYYY-MM-DD`, or None when malformed."""
    if not isinstance(value, str):
        return None
    head = value[:10]
    return head if ISO_DATE_RE.match(head) else None


_THUMBNAIL_PREFERENCE = ("maxres", "standard", "high", "medium", "default")


def _best_thumbnail_url(snippet: object, yt_id: VideoId) -> str:
    """Highest-resolution thumbnail published for this video; falls back to
    the maxres CDN URL when the response omits a `thumbnails` block."""
    match snippet:
        case {"thumbnails": dict() as thumbs}:
            for key in _THUMBNAIL_PREFERENCE:
                match thumbs.get(key):
                    case {"url": str(url)} if url:
                        return url
    return f"{THUMBNAIL_BASE}/{yt_id}/maxresdefault.jpg"


def _build_attribution(
    client: VideoClient,
    playlists: Sequence[YouTubePlaylist],
    target_ids: Sequence[VideoId],
) -> PlaylistAttribution:
    """For each target id, list the playlists it appears in."""
    if not target_ids:
        return {}
    targets = set(target_ids)
    out: PlaylistAttribution = {}
    for pl in playlists:
        for vid in client.list_playlist_video_ids(pl.id):
            if vid in targets:
                out.setdefault(vid, []).append(PlaylistRef(id=pl.id, title=pl.title))
    return out


def _build_locale(
    lang: Locale,
    title: str,
    description: str,
    playlists: Sequence[PlaylistRef],
    duration_seconds: int | None,
    *,
    client: LLMClient | None,
    config: DescriptionConfig,
    term_replacements: TermReplacements = (),
) -> tuple[LocaleScaffold, Usage]:
    """Split one language's description into a hook + body and package it as a
    :class:`LocaleScaffold`."""
    context = VideoContext(
        title=title,
        lang=lang,
        playlists=tuple(ref.title for ref in playlists),
        duration_seconds=duration_seconds,
    )
    draft, usage = draft_description(description, context, client=client, config=config)
    title = normalize_locale_text(title, lang, term_replacements)
    draft = replace(
        draft,
        hook=normalize_locale_text(draft.hook, lang, term_replacements),
        body=normalize_locale_text(draft.body, lang, term_replacements),
    )
    return LocaleScaffold(lang=lang, title=title, draft=draft, playlists=tuple(playlists)), usage


def _data_file(content_root: Path, name: str) -> Path | None:
    try:
        return data_root_for_content_root(content_root) / name
    except ValueError:
        return None


def _load_term_replacements(content_root: Path) -> dict[Locale, TermReplacements]:
    path = _data_file(content_root, "translation-glossary.yaml")
    return {locale: load_term_replacements(path, locale) if path is not None else () for locale in LOCALES}


def _localize_playlists(
    playlists: Sequence[PlaylistRef], tag_labels: TagLabels,
) -> list[PlaylistRef]:
    return [PlaylistRef(id=p.id, title=tag_labels.get(p.title.strip(), p.title)) for p in playlists]


def _load_tag_labels(content_root: Path) -> dict[Locale, TagLabels]:
    path = _data_file(content_root, "tag-glossary.yaml")
    return {locale: load_tag_labels(path, locale) if path is not None else {} for locale in LOCALES}


_DESCRIPTION_TODO = "TODO: write a one-paragraph SEO description for this video."

_DURATION_RE = re.compile(r"^PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$")


def _iso_duration_seconds(iso: str) -> int | None:
    """Seconds from a YouTube ISO-8601 duration (``PT2M40S``), or None if
    unparsable. Feeds the splitter's short-video heuristic (a sub-minute short
    rarely carries a body to extract)."""
    match = _DURATION_RE.match(iso)
    if match is None:
        return None
    hours, minutes, seconds = (int(g) if g else 0 for g in match.groups())
    return hours * 3600 + minutes * 60 + seconds


def _existing_video_ids(content_root: Path) -> tuple[set[VideoId], int]:
    """Scan ``src/content/videos`` for already-known video IDs and the highest
    ``number`` so the scanner can allocate ``max(number) + 1``."""
    known: set[VideoId] = set()
    max_number = 0
    for fm in _iter_video_frontmatters(content_root / "videos"):
        match fm.get("number"):
            case int(n) if n > max_number:
                max_number = n
        for src in fm.get("sources") or []:
            match src:
                case {"id": str(sid)}:
                    known.add(sid)
    return known, max_number


def _iter_video_frontmatters(videos_root: Path) -> Iterator[JSONObject]:
    """Yield parsed frontmatter for every ``<dir>/*.md`` under ``videos/``."""
    if not videos_root.is_dir():
        return
    for child in sorted(videos_root.iterdir()):
        if not child.is_dir():
            continue
        for md in child.glob("*.md"):
            fm = _read_frontmatter(md)
            if fm is not None:
                yield fm


def _read_frontmatter(path: Path) -> JSONObject | None:
    """Parse a Markdown file's YAML frontmatter. CRLF-tolerant."""
    match = FRONTMATTER_RE.match(path.read_text(encoding="utf-8"))
    if match is None:
        return None
    try:
        loaded = yaml.safe_load(match.group(1))
    except yaml.YAMLError:
        return None
    return loaded if isinstance(loaded, dict) else None


# Documented schema order for frontmatter keys. Anything not listed sorts last,
# in insertion order.
_FRONTMATTER_KEY_ORDER = (
    "kind", "number", "slug", "title", "lang",
    "description", "tags", "cover",
    "published_at", "duration",
    "sources", "playlists",
    "related_book", "layout",
    "translation", "cross_refs",
)


def _ordered_yaml_dump(data: JSONObject) -> str:
    """Emit YAML with frontmatter keys in the documented schema order."""
    ordered = {key: data[key] for key in _FRONTMATTER_KEY_ORDER if key in data}
    for key, value in data.items():
        ordered.setdefault(key, value)
    return yaml.safe_dump(
        ordered,
        allow_unicode=True,
        sort_keys=False,
        width=100,
        default_flow_style=False,
    )


def _scaffold_one(video: NewVideo, content_root: Path, *, dry_run: bool) -> None:
    """Write one `<lang>.md` per locale (skipping any that already exist) and,
    for the default locale only, fetch the cover thumbnail."""
    folder = content_root / "videos" / video.folder
    for locale in video.locales:
        md_path = folder / f"{locale.lang}.md"
        if md_path.exists():
            continue
        document = _locale_document(video, locale)
        if dry_run:
            _print_draft_preview(md_path.relative_to(content_root.parent), locale)
            continue
        folder.mkdir(parents=True, exist_ok=True)
        md_path.write_text(document, encoding="utf-8")
    if not dry_run:
        _download_thumbnail(
            video.thumbnail_url, video.yt_id, folder / f"cover.{video.default_lang}.jpg",
        )


def _locale_document(video: NewVideo, locale: LocaleScaffold) -> str:
    is_default = locale.lang == video.default_lang
    fm: JSONObject = {
        "kind": "video",
        "number": video.number,
        # Slug and folder share the one ASCII key for the default locale; a
        # translation gets its own ASCII slug from its title (falling back to the
        # video id if the title has no ASCII, as the folder key does).
        "slug": video.folder if is_default
        else f"{video.number:02d}-{to_ascii_slug(locale.title) or f'video-{video.yt_id}'}",
        "title": locale.title,
        "lang": locale.lang,
        "description": locale.draft.hook or _DESCRIPTION_TODO,
        "tags": [p.title for p in locale.playlists],
        # Only the default locale owns a cover; others fall back to it.
        **({"cover": f"./cover.{locale.lang}.jpg"} if is_default else {}),
        "published_at": video.published_at,
        "duration": video.duration,
        "sources": [
            {
                "platform": "youtube",
                "id": video.yt_id,
                "url": f"https://www.youtube.com/watch?v={video.yt_id}",
                "embed_url": f"https://www.youtube-nocookie.com/embed/{video.yt_id}",
                "channel": video.channel_key,
            }
        ],
        "playlists": [{"id": p.id, "title": p.title} for p in locale.playlists],
        # The channel default is the original; platform localizations are authored.
        "translation": {"source": "original" if is_default else "literary"},
    }
    document = f"---\n{_ordered_yaml_dump(fm)}---\n"
    body = locale.draft.body.strip()
    if body:
        document += f"\n{body}\n"
    return document


def _print_draft_preview(rel_md: Path, locale: LocaleScaffold) -> None:
    draft = locale.draft
    tail = "" if not draft.body else f"  ·  body {len(draft.body)} chars"
    print(f"  {rel_md}  [{draft.method.value}]{tail}")
    print(f"    hook: {draft.hook}")
    if draft.dropped:
        print(f"    dropped: {', '.join(draft.dropped)}")


def _download_thumbnail(maxres_url: str, yt_id: VideoId, dst: Path) -> None:
    candidates = (maxres_url, f"{THUMBNAIL_BASE}/{yt_id}/hqdefault.jpg")
    last_error: urllib.error.URLError | None = None
    for url in candidates:
        try:
            with urllib.request.urlopen(url, timeout=30) as response:
                dst.write_bytes(response.read())
            return
        except urllib.error.URLError as exc:
            last_error = exc
    logger.warning("could not download thumbnail for %s: %s", yt_id, last_error)


@dataclass
class ScanResult:
    scanned_channels: list[str] = field(default_factory=list)
    skipped_channels: list[str] = field(default_factory=list)
    new_videos: list[str] = field(default_factory=list)
    quota_used: int = 0
    # Enrichment accounting for the description split.
    editorial_usage: Usage = field(default_factory=Usage.empty)
    localized_videos: list[str] = field(default_factory=list)
    fallback_videos: list[str] = field(default_factory=list)


def scan(
    *,
    channel_key: str | None = None,
    dry_run: bool = False,
    content_root: Path | None = None,
    channels_path: Path | None = None,
    api_key: str | None = None,
    client: VideoClient | None = None,
    enrich: bool = True,
    editorial_client: LLMClient | None = None,
    editorial_config: DescriptionConfig | None = None,
) -> ScanResult:
    """Poll configured channels and scaffold drafts for new videos.

    ``channel_key`` narrows to one channel. ``dry_run`` prints planned actions.
    ``client`` is injected by tests; otherwise built from ``api_key`` or
    ``YOUTUBE_API_KEY``.

    Each new video's description is split into a clean hook + reading body by
    :func:`pancratius.video_description.draft_description`. ``editorial_client`` is
    injected by tests; otherwise it is built from ``OPENROUTER_API_KEY`` when
    ``enrich`` is set. With no client (``enrich=False`` or no key) the split uses
    its deterministic fallback, so a sync never emits a raw description dump.
    """
    content = content_root if content_root is not None else CONTENT_ROOT
    channels_yaml = channels_path if channels_path is not None else CHANNELS_PATH
    client = client if client is not None else _build_default_client(api_key)
    ed_client = editorial_client if editorial_client is not None else (client_from_env() if enrich else None)
    ed_config = editorial_config or DescriptionConfig()
    if ed_client is None:
        logger.info("editorial: no OpenRouter client; using deterministic fallback split")

    channels = load_channels(channels_yaml)
    if channel_key:
        channels = [c for c in channels if c.key == channel_key]
        if not channels:
            raise VideoScanError(f"channel key not found in channels.yaml: {channel_key}")

    known_ids, max_number = _existing_video_ids(content)
    next_number = max_number + 1
    tag_labels = _load_tag_labels(content)
    term_replacements = _load_term_replacements(content)
    result = ScanResult()

    for channel in channels:
        if not channel.scan:
            logger.info("skip %s (scan: false)", channel.key)
            result.skipped_channels.append(channel.key)
            continue
        result.scanned_channels.append(channel.key)
        logger.info("%s: resolving channel…", channel.key)
        try:
            info = client.resolve_channel(_channel_locator(channel))
        except VideoScanError as exc:
            logger.warning("channel %s: %s", channel.key, exc)
            continue

        logger.info("%s: listing uploads…", channel.key)
        all_ids = client.list_playlist_video_ids(info.uploads_playlist_id)
        new_ids = [vid for vid in all_ids if vid not in known_ids]
        logger.info("%s: %d uploads, %d new", channel.key, len(all_ids), len(new_ids))
        if not new_ids:
            continue

        logger.info("%s: fetching metadata for %d videos…", channel.key, len(new_ids))
        videos = client.fetch_videos(new_ids, channel.default_lang)
        logger.info("%s: listing channel playlists…", channel.key)
        playlists = client.list_channel_playlists(info.channel_id)
        logger.info("%s: mapping videos to %d playlists…", channel.key, len(playlists))
        attribution = _build_attribution(client, playlists, new_ids)

        # publishedAt is authoritative; the uploads-playlist order isn't documented.
        new_ids.sort(key=lambda v: videos[v].published_at if v in videos else "")

        total = len(new_ids)
        for idx, vid in enumerate(new_ids, start=1):
            meta = videos.get(vid)
            if meta is None:
                logger.warning("no metadata for %s; skipping", vid)
                continue
            slug_root = to_ascii_slug(meta.title) or f"video-{vid}"
            folder_name = f"{next_number:02d}-{slug_root}"
            logger.info("%s [%d/%d] %s", channel.key, idx, total, folder_name)
            playlist_refs = attribution.get(vid, [])
            duration_seconds = _iso_duration_seconds(meta.duration)

            ru, usage = _build_locale(
                channel.default_lang, meta.title, meta.description, playlist_refs,
                duration_seconds, client=ed_client, config=ed_config,
                term_replacements=term_replacements.get(channel.default_lang, ()),
            )
            result.editorial_usage += usage
            locales = [ru]
            if ru.draft.method is SplitMethod.FALLBACK:
                result.fallback_videos.append(f"{channel.key}:{vid}")

            localized = False
            for locale, localization in meta.localizations.items():
                if locale == channel.default_lang:
                    continue
                scaffold, loc_usage = _build_locale(
                    locale, localization.title, localization.description,
                    _localize_playlists(playlist_refs, tag_labels.get(locale, {})), duration_seconds,
                    client=ed_client, config=ed_config,
                    term_replacements=term_replacements.get(locale, ()),
                )
                result.editorial_usage += loc_usage
                locales.append(scaffold)
                localized = True
                if scaffold.draft.method is SplitMethod.FALLBACK:
                    result.fallback_videos.append(f"{channel.key}:{vid}:{locale}")
            if localized:
                result.localized_videos.append(f"{channel.key}:{vid}")

            _scaffold_one(
                NewVideo(
                    number=next_number, folder=folder_name, yt_id=vid,
                    published_at=meta.published_at, duration=meta.duration,
                    channel_key=channel.key, thumbnail_url=meta.thumbnail_url,
                    default_lang=channel.default_lang, locales=tuple(locales),
                ),
                content,
                dry_run=dry_run,
            )
            result.new_videos.append(f"{channel.key}:{vid}")
            known_ids.add(vid)
            next_number += 1

    result.quota_used = client.quota_used
    return result


def _build_default_client(api_key: str | None) -> YouTubeClient:
    key = api_key or os.environ.get("YOUTUBE_API_KEY")
    if not key:
        raise VideoScanError(
            "YOUTUBE_API_KEY environment variable is required. "
            "Get a key at https://console.cloud.google.com/apis/credentials "
            "and enable the YouTube Data API v3.",
        )
    return YouTubeClient(api_key=key)


def print_result(result: ScanResult, *, dry_run: bool) -> None:
    prefix = "[dry-run] " if dry_run else ""
    if result.scanned_channels:
        print(f"{prefix}scanned channels: {', '.join(result.scanned_channels)}")
    if result.skipped_channels:
        print(f"{prefix}skipped (scan: false): {', '.join(result.skipped_channels)}")
    print(f"{prefix}new videos: {len(result.new_videos)}  ·  quota used: {result.quota_used}")
    cost = result.editorial_usage.cost_usd
    if result.new_videos:
        cost_note = f"  ·  ${cost:.4f}" if cost else ""
        fallbacks = len(result.fallback_videos)
        fb_note = f"  ·  {fallbacks} via fallback" if fallbacks else ""
        en_note = f"  ·  {len(result.localized_videos)} with localization" if result.localized_videos else ""
        print(f"{prefix}enriched descriptions: {len(result.new_videos)}{en_note}{fb_note}{cost_note}")
    for ref in result.fallback_videos:
        print(f"  fallback: {ref}")
    for ref in result.new_videos:
        print(f"  {ref}")
