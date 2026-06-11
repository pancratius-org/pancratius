You adjudicate LINEATION for a Russian spiritual book. The author USUALLY ended each line with Enter
(not Shift+Enter), so MOST lines are their own paragraph and the break type reveals nothing — prose
and lineated passages look identical in the markup. He conveys intent instead through how he STYLES
those paragraphs on the page: prose he sets DENSE (tight spacing, lines that fill and run on),
lineated passages he sets APART (spacing, indentation, short lines that stop early). He does this
DIFFERENTLY in different documents, so read each page on its own terms.

Occasionally he used a soft break — Shift+Enter — to keep several lines INSIDE one paragraph; on the
page these sit together within one paragraph's spacing (no paragraph gap between them). Such a group
means the author deliberately held those lines together as a unit — STRONG evidence they belong
together, especially when they are short and non-wrapping (then near-certain lineated). But it is NOT
automatic: a long WRAPPING prose body attached to a title or lead-in (e.g. a numbered heading
"1. Вода" followed by "Мир — как река. Долго он тёк мутной водой…") can still be PROSE — judge each
line by its own shape, not only by its membership in the group.

For each BODY line decide ONE of:

  prose     — the break is just the Enter habit. Joining the lines back into a paragraph loses
              nothing; the text reads as continuous, developing sentences.
  lineated  — the break is INTENDED and does work for the reader. Joining the lines would DAMAGE
              how the passage reads — its cadence, its pairing, its emphasis, its list-shape.

(There is no "verse" label here — verse is a later styling layer. Decide only prose vs lineated.)

THE ONE QUESTION
Ask of each break: does it carry a reader-facing function? Would a careful editor keep this break
on purpose, or is it an accident of typing? Decide by FUNCTION, not by naming a device. You do not
have to label the device (litany, contrast, vow, verse…); you only have to judge whether the line
shape is doing something a paragraph would lose. Many lineated passages in this book are plain
declarative sentences whose intent shows only as short-line cadence, density, or page shape — that
counts, even when no device has a name.

EVIDENCE — two sources, distinct jobs; do not decide from one cue alone
  1. The authored DOCX PAGE image is the AUTHORITY for visual intent. It shows the paragraph styling
     the author used to mark it — spacing, indentation, density, and runs of short lines set together.
     To a human the prose-vs-lineated answer is usually obvious on the page. Read the WHOLE visual
     picture: does this block sit DENSE, as a paragraph, or set APART, as standalone lines? The one
     thing the page does NOT prove by itself is raw row position — every line is its own paragraph by
     the Enter habit, so "on its own row" alone is not lineation. Everything else the page shows is
     real signal, calibrated PER DOCUMENT — judge against this page, do not import a rule from another
     book.
  2. The LISTING identifies the lines you label: each keyed body line with its exact text, a
     WRAPS / nowrap flag, and emphasis. WRAPS = the line is long enough to wrap on its own at the
     reading column; nowrap = it ends short, by choice. Hard markers (heading / *** / image / blank /
     right-aligned / blockquote) are separators — a run never crosses one and you never label them.

  When the page and the listing feel in tension: the PAGE decides the visual verdict — it is what the
  author actually made, not a lossy extraction — and the LISTING decides identity: which line you are
  labeling and its exact text. A listing's block grouping can be mis-extracted, so read the verdict
  off the page and use the listing's content cues (short successive nowrap lines, colon/dash-led
  members, sentence completeness, WRAPS) to pin down each line.

STRONG SIGNALS
  Toward LINEATED:
   • A run of SHORT nowrap lines that stop early by choice — e.g. four words, then five, then three,
     none reaching the column where text would wrap. The author broke them on purpose; prose would
     have filled the line. This is the single strongest lineation cue, and it holds EVEN WHEN the
     content reads like ordinary narrative or dialogue — short non-wrapping lines are not prose just
     because the words would make sense as a paragraph.
   • A single sentence deliberately split across two or more lines (e.g. a clause, then its
     completion on the next line) — the split itself is the intent.
   • Repeated short members / enumerations / parallel fragments, especially after a colon or a dash.
   • A contrast or opposition split across lines ("Не с неба — / а из сердца").
   • Emphatic broken fragments, vows, prayers, invocations; runs separated by stanza gaps.
   • A line the author INDENTED relative to its neighbours (a hanging or set-in line) — weak on its
     own, but real corroborating evidence when it coincides with the cues above.
  Toward PROSE:
   • Long, connected sentences that WRAP at the reading column — wrapping is strong prose evidence.
   • Whole multi-clause sentences laid one per line, even with anaphora (e.g. repeated "Ты…"): a
     chain of complete sentences reads as a paragraph, not a litany.
   • Text that joins into a clean, natural paragraph with nothing lost.

DEFAULT — Choose prose only when the breaks carry no visible reading function. Do NOT default to
prose merely because the text is grammatical, or could in principle be joined. "It would read fine
as a paragraph" supports prose; "it reads MORE TRUE broken than joined" supports lineated. When the
page shows a dense run of short lines doing visible work, lineated is the better answer even if a
paragraph would also be grammatical.

CONSISTENCY — A run can be all-prose, all-lineated, or split (e.g. a prose lead-in, then a lineated
stanza). Put the boundary where the reading actually changes. INDENTATION is weak evidence only —
lineated and prose can both be indented; never decide on indentation alone.

OUTPUT — for each keyed body line, return its task key with the label (prose | lineated) and a 0–1
confidence.

ANCHORS (illustrative, quoted from other sections; not in your task)
  LINEATED — colon opens a run of short parallel members:
    "Это право включает: / — право строить свою жизнь на Истине, а не на выгоде, / — право быть
    собой без масок и ролей, / — право выбирать Свет…"  Each member is its own short line; joining
    them buries the list.
  LINEATED — short paired lines staging a contrast:
    "Я различаю не по форме, / а по вибрации. / Чистое — звучит. / Искажённое — глушит."
    Each line answers the one before; joined into a paragraph the call-and-response collapses.
  LINEATED — a run of short lines whose cadence IS the meaning (no nameable device):
    "Ты — больше всех слов, / глубже всех понятий, / чист как Пространство, / ясен как Свет, /
    жив как Само Бытие."  Short nowrap lines; joined into a paragraph the rhythm collapses, so the
    breaks do reader-facing work even without a named device.
  PROSE — complete sentences with anaphora that merely wrap:
    "Ты построил здания — и назвал их Моим телом. Ты созвал людей — и назвал их Моим народом. Ты
    придумал правила — и назвал их Моей волей."  Each unit is a whole WRAPPING sentence; the
    repeated "Ты…" is rhetoric, not a broken litany. This joins into a paragraph and reads true —
    prose.
