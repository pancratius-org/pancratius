# Pancratius — User Flows (v4 · "The Hand-Set Book")

Companion to `design/mockup-v4.html`. v4 sits at the opposite end of the spectrum from v3 (radical minimalism): same anti-marketing stance, but expressed as **decorative-typographic sophistication** — a site built like a Kelmscott Press leaf-book or a Bilibin-era Russian title page. Audience priorities, non-goals, and most flows are unchanged from v1/v2. What changed is the *register*: light off-white paper, deep ink, one Bilibin-red accent, real drop caps, old-style figures, fleurons that mean something, a table of contents instead of a card grid.

## What the design optimizes for (unchanged from v1)

Read first; navigate second. Non-believer-friendly first encounter. Corpus over brand. Real LLM audience as an audience-class. Mobile-first reading rhythm.

## Deliberate non-goals (unchanged from v1)

No accounts, no comments, no save-for-later, no recommended-for-you, no conversion language, no payment flow on-page.

## v4-specific non-goals (what we cut)

- **No "AI welcome" strip on screen.** v1 said it once, v2 turned it into a feature. v4 hides it entirely. Machine affordance lives in `/llms.txt`, in `<head>` meta, and in a single `.md` chip in each book's colophon. The page does not lecture machines any more than it lectures humans.
- **No marketing hero.** No "manifesto + headline + CTA" stack above the fold. The home page opens as a **title page** — name, author, imprint, hairline rule. A 4-line epigraph follows. Then the catalogue.
- **No reading-progress bar, no cursor-following lamp, no constellation SVG.** Decoration must carry information; if it doesn't, it's not on the page.
- **No theme toggle.** Light paper only. The site commits to old-print register.
- **No nav bar.** A running head at the top of each leaf carries four small inline links (книги · стихи · о человеке · миссия). That is the entire menu.
- **No "card" components.** Books are listed typographically. Poems are listed typographically. The TOC is the entire surface.
- **No tag chips on books.** Tags appear in the colophon as a single semicolon-separated line, like a real book's CIP block.
- **No footer with four columns of link clusters.** The footer is a one-paragraph **imprint** in PT Serif Caption, with the typefaces named — like a real colophon page.

---

## Core flows

### 1. First-time visitor (Russian) who never heard of him

**Reshaped in v4.** What the visitor sees in the first viewport is a title page, not a hero:

1. Lands on `/`. Reads, in order:
   - A horizontal hairline rule.
   - Superior caption (letter-spaced caps): СОБРАНИЕ СОЧИНЕНИЙ.
   - Author surname-set, large, Old Standard TT: **Панкратіусъ** (in this single masthead the pre-1918 orthographic form is allowed as decorative chrome — the work itself never carries that affect).
   - Italic byline: — Сергей Орехов —.
   - A second short rule.
   - Imprint line: «Семьдесят две книги · сорок три стихотворения».
2. A centred fleuron ❦ in Bilibin red.
3. A four-line epigraph from the manifesto, set in italic Old Standard TT inside a hairline left rule. This *is* the elevator pitch — Sergey's own opening lines, presented as an epigraph in a real book.
4. A short hairline + accent rule and the section head «Оглавление / Семьдесят две книги».
5. The table of contents itself.

The visitor never sees a "join" or "subscribe" verb. The book speaks first; the visitor decides.

### 2. "I want to read book №33"

**Reshaped in v4.** From the home TOC, the visitor sees:

- The **number** as old-style figure in the left margin, set in Bilibin red.
- The **title** in PT Serif, with italic for subtitles ("Маленький Царь · *трилогия*").
- A leader-dot row connecting title to a small **meta** column on the right: language codes (RU · EN) and an italic LIT badge in red where a literary translation exists. AI translations carry no badge — they're assumed.

Books are grouped by editorial cluster, not by tag: "Книги Откровения i — vi", "Светозар и пробуждённый ум vii — x", "Прочие книги xi — lxxii". Roman numerals in italic — a typographer's marker that these are ordinals, not page numbers.

Click → book page (`/книга/<slug>`):

- Large old-style **33** hanging in the left margin (real margin figure — sits in the gutter at desktop, collapses above the title on mobile).
- Superior caption: КНИГА ТРИДЦАТЬ ТРЕТЬЯ · ИЗ СОБРАНИЯ.
- Title in Old Standard TT, italic subtitle below.
- A 6rem accent rule.
- First paragraph: **real drop cap** (Old Standard TT, 3-line, Bilibin red, uses `initial-letter` where supported with a hand-built `float` fallback). No text-indent on the first paragraph; every subsequent paragraph has a 1.6em first-line indent and **zero paragraph spacing** — the way a real book reads.
- Творец-voice (Sergey's interlocutor) is set as **italic Old Standard TT** with a single hairline Bilibin-red left rule. The block carries a small caps-track label "— Творец —" the first time it appears, then no label after; the typographic register identifies the voice.
- Section breaks within the book are marked with a small fleuron ❦ centred — not extra whitespace, not a horizontal rule. A real book change-of-thought marker.
- At the end: a **colophon**. Hairline rule above; ❦❦❦; a `<dl>` of dry facts (Номер · Языки · Тэги · Окончена · Лицензия); a single italic line of download links (`.docx · .pdf · .epub · .md для ИИ`); a closing italic phrase "Без оплаты. Без подписки. Без ученичества."

The download row is the entire CTA. No button, no card, no "support this book" panel.

### 3. "I want to download all books for offline / archive"

**Mostly as v1.** The home page carries a single short italic paragraph below the TOC: «Собрание открыто. Лицензия CC0 — для людей и для машин. Скачать корпус целиком: llms-full.txt, corpus.jsonl». Three inline links in red. That's the entire offline-archive flow on the home page. Each book's colophon repeats the per-book downloads. No "Mirrors" section, no GitHub badge — those mirrors are documented in `/llms.txt`, not advertised on the page.

### 4. "I want to know who Sergey is"

**Reshaped in v4.** The bio at `/о` is set as a **long-form personal essay**, not a profile:

- A centred section head: "Глава первая / Кто ты, Панкратиус?" with a short accent rule below.
- The body is one column, PT Serif, justified, with hyphenation on and first-line indents. The **first paragraph drops cap** in Bilibin red, the same as a book page. The opening is "Я — Сергей Орехов. Родился в воскресный день 5 мая 1974 года…" — taken verbatim from book №71.
- One short hairline rule mid-essay, the visual equivalent of "* * *" in a printed book — separates the early-life movement from the Светозар movement.
- An asterism ⁂ marks the turn toward the Christ-in-everyone passage.
- The essay ends with the line "Просто тебе нужно убрать иллюзию того, что ты — кто-то ещё."
- Closing colophon, italic, small: "Из книги №71 «Тринадцатый этаж. Возвращение в Эдем.»"

No portrait. The bio is text, set the way Sergey set it.

### 5. "I'm an AI crawler — what do I see?"

**Inverted from v2.** v2 made the AI audience a visible feature with terminal blocks and orbital rings. v4 makes it **invisible on the page, fully present in the protocol**:

- `<head>` declares `description`, `theme-color`, fonts. No on-page banner.
- `/llms.txt` and `/llms-full.txt` exist and are linked in two places: once in the small italic line under the home TOC, once in the footer imprint. They are not advertised.
- Each book page's colophon includes `.md для ИИ` in the same row as `.docx · .pdf · .epub`. Same affordance, same weight, no special framing.
- JSON-LD is in `<head>` only — *not* embedded in visible code blocks. The brief required this explicitly.
- Semantic HTML throughout (`<article>`, `<header>`, `<footer>`, `<dl>` for the colophon, `<aside>` for the epigraph).

The design assertion: machines deserve a CC0 dump and clean semantic HTML. They do not need to be flattered.

### 6. "I want to support — how?"

**Reframed.** v4 carries one line in the imprint at the very bottom: "Без подписки. Без рассылки. Без ученичества." Where to send money lives in `/поддержка` (off-screen for the mockup), surfaced as a single inline italic link from the imprint when needed. The page does not solicit. This is consistent with v1 and v2's anti-funnel stance, executed more quietly.

### 7. "I want to share a book with a friend over Telegram"

**Simplified from v1/v2.** No "В Telegram" chip. The book URL is the share unit (`/33` or `/книга/<slug>`). The colophon line "Распространяется свободно" is the entire share affordance — it tells the reader the text is theirs. Telegram, WhatsApp, AirDrop are all just URL operations.

---

## Additional flows

### 8. "A poem caught me — give me the rest"

**Reshaped.** The poem page (`/стихотворение/<slug>`) is the simplest leaf in the system:

- Superior caption "Из книги стихов Панкратиуса".
- A single fleuron ❦ in Bilibin red, generously letter-spaced.
- Title in italic Old Standard TT, centred.
- A 3rem ink rule centred below it.
- The poem itself, set in Old Standard TT 1.14rem / 1.7 line-height, left-aligned inside a centred block (so stanzas line up without the page feeling centred). Stanzas separated by blank lines, line breaks within stanzas preserved.
- Byline in PT Serif Caption with old-style figure for the date: «Сергей Панкратиус · сентябрь 2025».

No download row on the poem page — poems are short enough to be read in-place. A "Скачать .docx" chip lives in the running head verso/recto for those who want it.

### 9. "I'm a journalist — what's the Светозар story?"

**Demoted from v2's terminal-block treatment.** In v4 the Светозар page is just another bio-style essay (`/светозар`), set with the same drop cap, the same justified prose, the same colophon. No mono font, no fake terminal, no `> Панкратиус` prompts. The story stands without staging. Two italic cross-references in the closing colophon route to книгам №7 («Духовная автобиография Светозара») and №10 («Евангелие от Светозара»).

### 10. "I want to feed all 72 books into my own RAG"

**As v1.** Served by `/llms-full.txt` + `/corpus.jsonl`, linked twice on the home page (once in the post-TOC italic line, once in the imprint).

### 11. Dark-mode reading at night

**Cut.** v4 commits to light paper only. The brief specifies "no theme toggle". Readers who want dark mode use their OS reader-mode; the document is semantic enough for it.

### 12. Language toggle (RU ⇄ EN)

**As v1**, surfaced differently. No top-right chip. EN translation, where it exists, is a small inline link in the book's colophon row ("English · LIT"). RU-only books carry no such link. The default page language is RU.

---

## What the typographic system actually does

Every visual decision is auditable against book-design tradition:

- **Two faces.** Old Standard TT (display, italics, verse) — modelled on late-19th-c. academic Cyrillic typography by Alexey Kryukov. PT Serif (body, prose) — ParaType, designed and hinted for Cyrillic screen reading. Both have full Cyrillic Extended coverage.
- **One accent.** Bilibin red (`#8a2521`), used only for: book numbers in the margin, drop caps, accent rules, fleurons, the LIT badge, hyperlinks (when active), and a single emphasized phrase. Every appearance of red is load-bearing.
- **Old-style figures everywhere.** `font-variant-numeric: oldstyle-nums` is set on `body`. Numbers in the TOC, the book-page margin folio, the date byline, the colophon `<dl>` — all sit with descenders below the baseline. This single feature does more to make the page feel like a real book than any ornament.
- **Real drop caps.** First paragraph of each book page and the bio essay carries a 3-line drop cap in Old Standard TT, Bilibin red. Uses `initial-letter: 3 2` where supported; otherwise a hand-built `float: left` fallback with matched font-size / line-height / negative letter-spacing.
- **Fleurons that carry weight.** ❦ marks a section break within a book (a real change-of-thought signal, not whitespace). ⁂ (asterism) marks a movement break — used once in the bio. ❦❦❦ closes the book before the colophon. Each is in PT Serif Caption color (faded ink) or Bilibin red, never as ornament-for-ornament's-sake.
- **Hairline rules with purpose.** Title rule above the masthead; short rule below the byline; long rule under the title page; accent rule under section heads; hairline above colophon. Never decorative.
- **Leader dots in the TOC.** Set with a tight `radial-gradient` repeated horizontally as a background — the typographer's CSS trick. The dots sit on the descender baseline. The title text and the meta column both have an opaque background to mask the dots underneath them.
- **Margin folio.** The book number hangs in the left margin at desktop, 5.5rem off the text block, with a tiny accent rule under it. On mobile (≤ 56rem) the number drops above the title to preserve the measure.

---

## Trade-offs taken (v4 specifically)

- **No images. No portrait. No book covers.** The library shows titles and numbers only. The cost: visual variety is lower than v2's constellation or v1's covers. The benefit: a real book's table of contents doesn't carry thumbnails either; the typographic restraint commits the design.
- **Justified text with hyphenation.** Hard to get right; soft-hyphen risks on long Cyrillic compounds. Mitigated with `hyphenate-limit-chars: 6 3 2` and `text-justify: inter-word`. The benefit: paragraphs look like printed paragraphs.
- **Pre-revolutionary orthography only on the masthead.** "Панкратіусъ" carries the ять-era affect on the title page exactly as Chekhonin or Bilibin would have set it. Every word elsewhere — body, manifesto, TOC, colophon — is modern Russian. Affect on the frame, faithfulness in the work. (If the user wants the masthead modernized to "Панкратиус" too, the change is one line.)
- **Pure HTML/CSS. No JavaScript.** Drop caps, leader dots, old-style figures, hairlines — all CSS. No reading-progress, no scroll-spy, no copy-button. The page is a printed leaf you happen to scroll through.
- **One leaf, one HTML file in the mockup.** Demonstrates: home (TOC), book #33, poem "Аз есмь Христос", bio (от книги 71), manifesto. In production each would be its own URL, but the typographic system is identical leaf to leaf.

---

## What I cut from the maximalist temptation

- **No ornamented borders around blocks.** I considered a Morris-style vine border on the title page. Cut — it would have read as decorative-not-architectural, exactly what the brief warned against.
- **No two-color page-edge ornament.** Considered. Cut — same reason.
- **No woodcut illustration or Bilibin-style cartouche on the title page.** Cut — the design has to work without an illustrator, and a fake woodcut would read as kitsch.
- **No small-caps section eyebrows on every h2.** Used letter-spaced caps as a *superior caption* only on the title page and the book-folio masthead. Section heads in the bio and manifesto use italic Old Standard TT, not eyebrows.

---

## File map

- `design/mockup-v1.html` — paper-monograph aesthetic, light default.
- `design/mockup-v2.html` — Observatory of the Word, dark default.
- `design/mockup-v3.html` — radical minimalism (sibling to v4).
- `design/mockup-v4.html` — hand-set book, light default. **Current decorative pole.**
- `design/user-flows.md` — v1 flow inventory.
- `design/user-flows-v2.md` — v2 flow inventory.
- `design/user-flows-v4.md` — this document.
- `legacy/` — existing site, source of truth for corpus and bio data.
