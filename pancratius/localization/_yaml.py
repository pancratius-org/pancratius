from __future__ import annotations

from collections.abc import Mapping
from typing import cast

type YAMLMapping = Mapping[object, object]


def as_mapping(value: object) -> YAMLMapping | None:
    return cast(YAMLMapping, value) if isinstance(value, Mapping) else None
