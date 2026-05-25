"""The Pancratius library door — the `pancratius` console script (docs/tooling.md).

This package is intentionally thin: it is the *door*, not the rooms. The owning
logic lives under ``scripts/`` and ``scripts/lib/``; ``pancratius.cli`` dispatches
to one clean entry per owner.
"""
