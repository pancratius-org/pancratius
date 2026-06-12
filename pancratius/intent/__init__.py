"""Learned intent enrichment for the import pipeline: the register feature
producer, exported student artifacts, and the enrichment passes the conversion
pipeline composes after normalization. Imports `ir`, never the reverse."""

from pancratius.intent.runtime import apply_verse_register

__all__ = ("apply_verse_register",)
