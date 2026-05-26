"""Fixture (PAN017-import-work-kinds / bad): CORPUS_WORK_KINDS is the correct SoT
(book/poem), but the sibling CLI hardcodes a --kind choices list that re-admits
`project` — the retired-capability regression PAN015/PAN017 forbid. The audit
execs this module, so it must import."""

SEGMENT_OF: dict[str, str] = {"book": "books", "poem": "poetry", "project": "projects"}
CORPUS_WORK_KINDS: tuple[str, ...] = ("book", "poem")
