"""Loader for ``src/content/videos/channels.yaml``.

Python adapter mirroring the zod schema in ``src/content.config.ts``. The
site reads channels through Astro; the scanner reads them through here.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from pancratius.locales import DEFAULT_LOCALE, Locale, is_locale
from pancratius.paths import CONTENT_ROOT

CHANNELS_PATH = CONTENT_ROOT / "videos" / "channels.yaml"


def _clean_locator(value: str, label: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{label} must be non-empty")
    return cleaned


@dataclass(frozen=True, slots=True)
class ChannelHandleOnly:
    handle: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "handle", _clean_locator(self.handle, "handle"))


@dataclass(frozen=True, slots=True)
class ChannelIdOnly:
    channel_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "channel_id", _clean_locator(self.channel_id, "channel_id"))


@dataclass(frozen=True, slots=True)
class ChannelIdWithHandle:
    channel_id: str
    handle: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "channel_id", _clean_locator(self.channel_id, "channel_id")
        )
        object.__setattr__(self, "handle", _clean_locator(self.handle, "handle"))


@dataclass(frozen=True, slots=True)
class ChannelUrlOnly:
    pass


type ScannableVideoChannelAddress = ChannelHandleOnly | ChannelIdOnly | ChannelIdWithHandle
type VideoChannelAddress = ScannableVideoChannelAddress | ChannelUrlOnly


@dataclass(frozen=True, slots=True)
class VideoChannel:
    key: str
    platform: str
    address: VideoChannelAddress
    url: str
    title: dict[Locale, str]
    copy: dict[Locale, str]
    badge: dict[Locale, str] | None
    scan: bool
    # The locale the scanner writes into `<lang>.md` for entries from this channel.
    default_lang: Locale

    def require_scannable_address(self) -> ScannableVideoChannelAddress:
        match self.address:
            case ChannelHandleOnly() | ChannelIdOnly() | ChannelIdWithHandle():
                return self.address
            case ChannelUrlOnly():
                raise ChannelsError(
                    f"channel {self.key}: scan requires channel_id or handle"
                )


class ChannelsError(RuntimeError):
    """Raised when the channels file is missing or malformed."""


def load_channels(path: Path = CHANNELS_PATH) -> list[VideoChannel]:
    """Return the configured channels, in file order.

    The YAML is a list of objects keyed by `id` (the channel key). Handle/channel
    id fields become an explicit address variant; `scan: true` requires one of
    them. Missing required fields raise ``ChannelsError``.
    """
    if not path.exists():
        raise ChannelsError(f"channels file not found: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ChannelsError(f"{path}: expected a list at the top level, got {type(raw).__name__}")
    channels: list[VideoChannel] = []
    for index, entry in enumerate(raw):
        try:
            channels.append(_parse_one(entry))
        except KeyError as exc:
            raise ChannelsError(f"{path}[{index}]: missing required field {exc}") from None
        except ChannelsError as exc:
            raise ChannelsError(f"{path}[{index}]: {exc}") from None
    return channels


def _parse_one(entry: object) -> VideoChannel:
    if not isinstance(entry, Mapping):
        raise ChannelsError("expected a mapping")
    # `Mapping` is invariant in K; `Any` here is the load-bearing escape from
    # what would otherwise need a runtime cast on every key access.
    data: Mapping[Any, object] = entry
    key = str(data["id"])
    title = data["title"]
    copy = data["copy"]
    if not isinstance(title, Mapping):
        raise ChannelsError(f"channel {key}: `title` must be a per-locale mapping")
    if not isinstance(copy, Mapping):
        raise ChannelsError(f"channel {key}: `copy` must be a per-locale mapping")
    badge = data.get("badge")
    handle = str(raw_handle).strip() if (raw_handle := data.get("handle")) else ""
    channel_id = (
        str(raw_channel_id).strip() if (raw_channel_id := data.get("channel_id")) else ""
    )
    default_lang = str(data.get("default_lang", DEFAULT_LOCALE))
    if not is_locale(default_lang):
        raise ChannelsError(f"channel {key}: unsupported default_lang {default_lang!r}")
    address = _channel_address(handle=handle, channel_id=channel_id)
    scan = bool(data.get("scan", True))
    if scan and isinstance(address, ChannelUrlOnly):
        raise ChannelsError(f"channel {key}: scan requires channel_id or handle")
    return VideoChannel(
        key=key,
        platform=str(data["platform"]),
        address=address,
        url=str(data["url"]),
        title=_locale_map(title),
        copy=_locale_map(copy),
        badge=_locale_map(badge) if isinstance(badge, Mapping) else None,
        scan=scan,
        default_lang=default_lang,
    )


def _channel_address(*, handle: str, channel_id: str) -> VideoChannelAddress:
    if channel_id and handle:
        return ChannelIdWithHandle(channel_id=channel_id, handle=handle)
    if channel_id:
        return ChannelIdOnly(channel_id=channel_id)
    if handle:
        return ChannelHandleOnly(handle=handle)
    return ChannelUrlOnly()


def _locale_map(raw: Mapping[Any, object]) -> dict[Locale, str]:
    out: dict[Locale, str] = {}
    for key, value in raw.items():
        locale = str(key)
        if not is_locale(locale):
            raise ChannelsError(f"unsupported locale key {locale!r}")
        out[locale] = str(value)
    return out


def write_channels(channels: list[VideoChannel], path: Path = CHANNELS_PATH) -> None:
    """Re-serialize the channels list. Used by tests; production never writes
    ``channels.yaml`` (yaml.safe_dump would drop the authored comment header)."""
    payload = [_channel_to_dict(c) for c in channels]
    path.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False, width=120),
        encoding="utf-8",
    )


def _channel_to_dict(c: VideoChannel) -> dict[str, Any]:
    entry: dict[str, Any] = {"id": c.key, "platform": c.platform}
    match c.address:
        case ChannelHandleOnly(handle):
            entry["handle"] = handle
            entry["channel_id"] = ""
        case ChannelIdOnly(channel_id):
            entry["channel_id"] = channel_id
        case ChannelIdWithHandle(channel_id=channel_id, handle=handle):
            entry["handle"] = handle
            entry["channel_id"] = channel_id
        case ChannelUrlOnly():
            entry["channel_id"] = ""
    entry["url"] = c.url
    if c.badge:
        entry["badge"] = dict(c.badge)
    entry["title"] = dict(c.title)
    entry["copy"] = dict(c.copy)
    entry["scan"] = c.scan
    if c.default_lang != DEFAULT_LOCALE:
        entry["default_lang"] = c.default_lang
    return entry
