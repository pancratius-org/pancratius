# Editorial Notes

Human-facing review debt that doesn't gate launch but is worth Sergey's eye when convenient.

## Pipeline note: `editorial.yaml` is temporary

The titles listed below live in `editorial.yaml` at the repo root. Nothing reads that file anymore — its only consumer was the legacy batch converter, which is gone. The single-DOCX importer (`scripts/import_docx.py`) preserves editor-owned frontmatter fields (`title`, `description`, `translation`, `cross_refs`) on an existing `<lang>.md`, so titles are edited directly in `src/content/books/<slug>/en.md` frontmatter. `editorial.yaml` now exists only as the staging list for a one-time apply of these EN titles into the matching `en.md`, after which it is deleted; this file remains as the human review checklist. Future agents should not bless `editorial.yaml` as architecture.

## English book titles — seeded translations awaiting review

The English titles in `editorial.yaml` for these books were translated by the conversion engineer (Claude), not by Sergey. They aim for faithful spiritual/biblical register but a human author may prefer different wording. To revise one, edit the `title` field directly in the affected `src/content/books/<slug>/en.md` frontmatter.

- 7  — *The Spiritual Autobiography of Svetozar*
- 30 — *A Message to Muslims*
- 47 — *Islam: Between Living Submission to Allah and the Form of the Law*
- 48 — *The Book of Love*
- 50 — *Mammon: Why You Are in His Power and How to Step into the Light Here and Now*
- 51 — *TriLogos*
- 52 — *IS: A Revelation on Becoming Human and Remembering Yourself as God*
- 59 — *Greater Love Hath No Man Than This*
- 60 — *On Communion with God*
- 62 — *The Book of Silence*
- 63 — *The Book of the Brothers Esau and Jacob Through the Eyes of the Creator*
- 64 — *The Book of the Holy Spirit*
- 65 — *The Book of Genesis, Alive*
- 66 — *The Blessed, the Holy Fools, and the Anointed: Three Paths of the Vanishing Self*
- 67 — *Stories I Knew Before Birth*
- 68 — *Here: A Book of Presence*
- 69 — *A Vaccine Against the Apocalypse: How to See When the World Is Blinded*
- 70 — *Now You See Me. Too.*
- 71 — *The Thirteenth Floor: Return to Eden*

The other ten EN titles in `editorial.yaml` (1, 2, 3, 4, 5, 6, 10, 27, 34, 35) were curated earlier and are presumed final.

## Source-side editorial typos in the original DOCX corpus

Reported by the data-quality audit; live in `legacy/books/ru/*.docx` source files and would propagate forward on any reconversion. Fix at source if/when revising.

- Book #03 (*Евангелие Фомы*) has gaps in the Логий sequence — missing numbers at 89, 95, and "Логий 10" appears between 107 and 109 (probably should be 108).
- Book #19 has a section heading that appears to have been lost in the source.

## Cross-reference paraphrasing

The corpus body text occasionally paraphrases canonical titles (e.g. `Я — Ты` for the book canonically titled `Ты — Я`). The converter's `extract_cross_refs` only resolves exact-match titles, so these references survive in prose but don't surface in `cross_refs` frontmatter. If a specific reference is worth surfacing, the simplest fix is to revise the source DOCX to use the canonical title.

Currently the corpus has 6 books with non-empty `cross_refs` after exact-match resolution. Paraphrased references add an estimated 2–3 more if normalised.
