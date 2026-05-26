"""Fixture (PAN017-import-work-kinds / good): CORPUS_WORK_KINDS excludes project
and is a subset of SEGMENT_OF's keys. The audit execs this module, so it must import."""

SEGMENT_OF: dict[str, str] = {"book": "books", "poem": "poetry", "project": "projects"}
CORPUS_WORK_KINDS: tuple[str, ...] = ("book", "poem")
