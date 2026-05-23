"""Fixture (PAN003-kind-segment-parity / bad): Python kind->segment SoT that
DISAGREES with the sibling src/lib/kinds.ts (poem -> "poetry" here, but the TS
maps poem -> "poems"). The audit execs this module, so it must import."""

SEGMENT_OF: dict[str, str] = {"book": "books", "poem": "poetry", "project": "projects"}
