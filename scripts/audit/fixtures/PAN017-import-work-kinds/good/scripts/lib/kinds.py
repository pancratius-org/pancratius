"""Fixture (PAN017-import-work-kinds / good): WORK_KINDS excludes project and is
a subset of SEGMENT_OF's keys (SEGMENT_OF keeps project for routing). The audit
execs this module, so it must import."""

SEGMENT_OF: dict[str, str] = {"book": "books", "poem": "poetry", "project": "projects"}
WORK_KINDS: tuple[str, ...] = ("book", "poem")
