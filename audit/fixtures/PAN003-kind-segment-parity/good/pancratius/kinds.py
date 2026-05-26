"""Fixture (PAN003-kind-segment-parity / good): Python kind->segment SoT that
AGREES with the sibling src/lib/kinds.ts. The audit execs this module, so it
must import."""

SEGMENT_OF: dict[str, str] = {"book": "books", "poem": "poetry", "project": "projects"}
