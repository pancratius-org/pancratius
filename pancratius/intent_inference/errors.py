"""Contract errors raised by production intent-inference loaders."""

from __future__ import annotations


class IntentInferenceError(ValueError):
    """Base class for production intent-inference contract failures."""


class RegisterArtifactError(IntentInferenceError):
    """A register scorer artifact is missing fields, malformed, or incompatible."""

