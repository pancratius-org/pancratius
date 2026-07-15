"""Small, strict decoders for JSON/TOML values at persistence boundaries."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import cast


class WireTypeError(ValueError):
    pass


def mapping(value: object, *, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise WireTypeError(f"{field} must be an object with string keys")
    return cast(Mapping[str, object], value)


def sequence(value: object, *, field: str) -> Sequence[object]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise WireTypeError(f"{field} must be an array")
    return value


def string(value: object, *, field: str) -> str:
    if not isinstance(value, str):
        raise WireTypeError(f"{field} must be a string")
    return value


def integer(value: object, *, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise WireTypeError(f"{field} must be an integer")
    return value


def number(value: object, *, field: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise WireTypeError(f"{field} must be a number")
    return float(value)
