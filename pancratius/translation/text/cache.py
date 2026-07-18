"""On-disk response cache for the chunk translation pipeline.

Semantics: an entry is one completed chunk after its configured translation
passes. Blank or partial outcomes are never cached.

Storage: one JSON file per entry under ``cache_dir/{sha256_hex}.json``.
Absent or corrupt files are treated as a cache miss — never raise.

Keys include every stage model that affects the stored outcome, the brief, the
context part, and the chunk source text.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path

from pancratius.translation.text.config import ModelId
from pancratius.translation.text.document import UnitId

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CacheEntry:
    """A cached chunk outcome: every unit mapped to its translated text."""

    unit_translations: dict[UnitId, str]


@dataclass(frozen=True, slots=True)
class BriefCacheEntry:
    """A cached profile outcome: the brief text and serialized BookProfile JSON."""

    brief: str
    profile_json: str


class TranslationCache:
    """Read/write cache for chunk translations and profile briefs.

    All writes are atomic (tmp + os.replace) so a partial write never leaves a
    corrupt file. All reads degrade gracefully on absent or malformed files."""

    def __init__(self, cache_dir: Path) -> None:
        self._dir = cache_dir

    def _ensure_dir(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)

    # --- key derivation ---------------------------------------------------------

    @staticmethod
    def _key(payload: object) -> str:
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(encoded.encode()).hexdigest()

    def chunk_key(
        self,
        draft_model: ModelId,
        revise_model: ModelId | None,
        brief: str,
        context_source_texts: tuple[str, ...],
        source_texts: tuple[str, ...],
    ) -> str:
        return self._key(
            [
                "chunk-v3",
                draft_model,
                revise_model,
                brief,
                list(context_source_texts),
                list(source_texts),
            ]
        )

    def brief_key(
        self,
        model_id: ModelId,
        source_text: str,
        *,
        title_ru: str,
        description_ru: str,
        tags_ru: tuple[str, ...],
    ) -> str:
        return self._key([model_id, source_text, title_ru, description_ru, list(tags_ru)])

    # --- chunk read/write -------------------------------------------------------

    def get_chunk(self, key: str) -> CacheEntry | None:
        path = self._dir / f"{key}.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return CacheEntry(unit_translations=dict(data["unit_translations"]))
        except FileNotFoundError:
            return None
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            logger.debug("cache miss (corrupt): %s — %s", path.name, exc)
            return None

    def put_chunk(self, key: str, entry: CacheEntry) -> None:
        self._ensure_dir()
        path = self._dir / f"{key}.json"
        tmp = path.with_suffix(".tmp")
        payload = json.dumps({"unit_translations": entry.unit_translations},
                             ensure_ascii=False, indent=None, separators=(",", ":"))
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, path)

    # --- brief read/write -------------------------------------------------------

    def get_brief(self, key: str) -> BriefCacheEntry | None:
        path = self._dir / f"{key}.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return BriefCacheEntry(brief=str(data["brief"]), profile_json=str(data["profile_json"]))
        except FileNotFoundError:
            return None
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            logger.debug("cache miss (corrupt): %s — %s", path.name, exc)
            return None

    def put_brief(self, key: str, entry: BriefCacheEntry) -> None:
        self._ensure_dir()
        path = self._dir / f"{key}.json"
        tmp = path.with_suffix(".tmp")
        payload = json.dumps({"brief": entry.brief, "profile_json": entry.profile_json},
                             ensure_ascii=False, indent=None, separators=(",", ":"))
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, path)
