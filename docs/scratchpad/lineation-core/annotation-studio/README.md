# annotation-studio

The human adjudication desk for lineation — a single-file, dependency-free, offline web tool for
deciding `prose` vs `lineated` on the hard lines an LLM panel disagreed on.

## What it is

`adjudicate.html` loads a task payload built by `lineation_core.teacher` and shows, per region, the
evidence (a feature-rich line listing, and for a vision task the rendered page/candidate images),
with every votable line addressed by an opaque task-local key (`L001`). You choose `prose`/`lineated`
per line and download a `responses.json`.

The keys are opaque by design: the `LineId` each maps to lives only in the task manifest, and
`lineation_core.teacher.responses` resolves it back before anything is committed — so a source
ordinal never passes through the UI. The tool is therefore key-scheme-agnostic and is used verbatim,
unmodified by the pipeline.

## The loop

1. `lineation_core.teacher.tasks` builds a task → a payload this tool loads;
2. adjudicate here → download `responses.json`;
3. `lineation_core.teacher.responses` resolves the keys to `LineId`s and `promote` commits the labels.
