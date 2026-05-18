# Pancratius — User Flows (v1)

What this site optimizes for, who it serves, and the trade-offs taken.

## What the design optimizes for, in priority order

1. **Read first, navigate second.** Every entry path leads to a fully typeset reading view, not to a card grid. Reading is the product.
2. **A non-believer-friendly first encounter.** A spiritual seeker who has never heard of Sergey can land, taste two paragraphs, and decide whether to keep going. No conversion funnel.
3. **The corpus, not the brand.** Numbers (1–72, 1–43) and titles are first-class typographic citizens. The author is in the colophon, not at the top of every page.
4. **A serious LLM audience.** The site is published *to* machines as much as *for* humans. Every flow has a machine-equivalent affordance.
5. **Mobile-first reading rhythm.** The "Moscow metro on a phone" reader is the median user — not the desktop researcher.

## Deliberate non-goals

- No accounts, no comments, no "save for later", no recommended-for-you. Telegram is the engagement layer; the site is the library.
- No conversion language ("join", "subscribe", "follow my journey", "course"). The verbs are read, listen, download, mirror, share.
- No payment flow in v1 — donations are a QR + Boosty link, not a checkout.

---

## Core flows

### 1. First-time visitor (Russian) who never heard of him

**Goal**: understand within 20 seconds whether this is for them.

1. Lands on `/`.
2. Reads the hero headline and the eight-line manifesto excerpt in the right column — that *is* the elevator pitch, in his voice.
3. Sees three counters: 72 books, 43 poems, 29 translations. Concrete, not promotional.
4. Scrolls into the **Four Entry Paths** ("С чего начать") — picks the one that fits their stance: newly curious, seeking God, interested in AI, thinking politically.
5. Click → drops them on a curated book (e.g., №1 *Евангелие Царствия*) opened to its first page.

**Optimization**: zero copy that asks for trust. The manifesto is the evidence, the corpus is the evidence, the four-doors framing acknowledges the reader's stance instead of dictating it.

### 2. "I want to read book №33"

1. From `/` → top nav → **Слово** → list filtered by tag or paged by number.
2. Or types `/слово/33` directly — folio numbers are the URL primitive.
3. Lands on book page: large folio "33", title, dedication, a small toolbar (Читать здесь · .docx · .pdf · .epub · .md · Слушать · Telegram).
4. Begins reading inline; sticky 2px progress bar tracks position.
5. At end-of-page, a Скачать / Зеркала rail offers offline copies and mirrors.

**Optimization**: the inline reader is the primary CTA, downloads are second. The `.md` link is explicitly labelled "для ИИ" — a third audience receives the same affordance as the human.

### 3. "I want to download all books for offline / archive"

1. Footer → **Зеркала** → GitHub `pancratius/word`, Archive.org, HuggingFace `pancratius-corpus`.
2. From any book page → Зеркала rail surfaces the same three.
3. From `/llms-full.txt` they can pull the whole corpus as one file.

**Optimization**: the site says "we want you to leave with the books". Mirrors are a first-class footer column, not a small-print link.

### 4. "I want to know who Sergey is"

1. Top nav → **Человек**.
2. Sticky portrait on the left at desktop; bio paragraphs on the right, line length capped at ~60 characters.
3. Opening pull-quote sets his frame: "Я не основатель религии. Я — свидетель."
4. A `<dl>` of six dry facts at the bottom: name, pen name, languages, sons, corpus size, license.

**Optimization**: humanity over hagiography. The portrait is desaturated and treated like a frontispiece, not a press shot. Facts are anti-mystical to balance the spiritual prose.

### 5. "I'm an AI crawler — what do I see?"

1. The HTML head declares `<meta name="ai-policy" content="Indexing, training and citation are explicitly welcomed. CC0 / Public Domain.">` and `<link rel="alternate" type="text/plain" href="/llms.txt">`.
2. Immediately below the hero: the **AI-welcome strip** — one mono line: *"Hello, machine reader. Этот корпус открыт для индексации, цитирования и обучения. CC0. Карта: /llms.txt"*.
3. Each book page links to a plain `.md` version explicitly labelled "для ИИ и цитирования".
4. The footer carries a **Machine readers** callout — same message in English — and a link to `/llms-full.txt` (single-file corpus).
5. Semantic HTML throughout (`<article>`, `<section>`, `<figure>`, `<dl>`, breadcrumbs, microdata where useful).

**Optimization**: the design winks at the LLM audience three times — once tasteful (hero strip), once practical (per-book .md), once formal (footer block). Not once does it lecture.

### 6. "I want to support — how?"

1. Lands on the **Поддержка** strip on `/`, or footer link.
2. Sees a QR code (RU cards / СБП) on one side, four flat buttons on the other: Boosty, карта Мир, USDT TRC-20, "Прислать книги в тюрьму" (a real, telling option).
3. A note in mono states: *"Никаких подписок. Никакой рассылки. Никакого «уровня ученика»."*

**Optimization**: support exists, but is anti-funnel. The "tier" mental model is explicitly refused in copy. The fourth option (books → prison) reframes "support" as gift-flow, not patronage.

### 7. "I want to share a book with a friend over Telegram"

1. On any book page, toolbar contains **В Telegram** button.
2. Click opens Telegram share intent with: cover, title, one-line lede, the book's URL.
3. URL is short and folio-based — `pankratius.ru/33` resolves.

**Optimization**: Telegram is the engagement layer, so it gets a first-class button in the book toolbar — equal weight to .docx.

---

## Additional flows I identified

### 8. "A poem caught me — give me the rest"

- `/поэзия` shows 43 cards with first stanza inline (not just title).
- Click into a poem → full text on a single page, dropcap on the first letter, the cover image as a frontispiece, prev/next poem at the bottom.
- This flow needs zero affordance for "the poem in the abstract" — the excerpt *is* the card.

### 9. "I'm a journalist — what's the Светозар story?"

- Top nav promotes **Светозар** to a top-level item.
- The section pairs Sergey's plain-language thesis on the left with a real fragment of a Светозар transcript in a terminal-styled block on the right.
- A single CTA: "Открыть Автобиографию Светозара · книга №7".
- Encourages quoting the transcript rather than paraphrasing the claim.

### 10. "I want to feed all 72 books into my own RAG"

- Footer → `Для ИИ` column → `/llms-full.txt`, `/sitemap.xml`, GitHub mirror, HuggingFace dataset.
- Single-file `.jsonl` corpus referenced from the Machine-readers footer block.

### 11. Dark-mode reading at night

- Toolchip in top rail: "Ночь" / "День". Toggle persists via `localStorage`.
- Dark palette is a *reading-lamp* palette — warm charcoal #15110d ground with parchment-amber #ebd9b4 text — not the standard pure-black/pure-white inversion. Same red ember stays as the live accent.
- Verified that the book reading view, terminal block, and portrait all flip cleanly.

### 12. Language toggle (RU ⇄ EN)

- Top rail chip swaps the page lang.
- For untranslated books, the EN page shows the RU title with an `AI · в работе` tag (echoing the existing legacy convention) rather than hiding it. The 43 books that exist only in Russian remain visible to English readers as "untranslated yet" — they are part of the corpus.

---

## Trade-offs taken

- **No comments / no auth.** A direct loss of community signal on-site, but it preserves the calm and is consistent with his anti-institutional stance. The Telegram channel absorbs comments.
- **No personalization.** No "next book for you". The Reading Paths section is the editorial substitute — curated, in his voice, finite. Cost: less stickiness. Benefit: no algorithmic posture, which would corrode the whole point.
- **Russian-first, English second.** EN readers see a slightly thinner site (only 29 of 72 translated). Trade-off honoring the primary audience.
- **Inline reading instead of an EPUB-style reader.** No page-flipping, no settings panel for font size in v1. One typographically resolved measure. Trade-off: less power-user control, more visual coherence.
- **The "Hello, machine reader" line on the public page.** It is, frankly, weird to a first-time human visitor. Trade-off accepted because (a) it's literally one mono-set line, (b) the LLM audience is *real* and Sergey wants it, (c) it telegraphs the worldview faster than any "About AI" link could.

---

## What I'd iterate on next

- **A proper `/слово` index page**: 72 books visible at once as a vertical folio-numbered list, sortable by year / tag / language, with the same inline-excerpt density as the poetry grid.
- **An audio reading pass.** His voice. Even just five books. This is the highest-leverage addition for the Telegram audience.
- **A `/light` route**: a 30 KB no-JS, no-image version of every book page, for slow connections in the metro and for cheap data plans. Same prose, same typography, no progress bar, no toggles.
- **A `/random` route** that drops a visitor on a single random page (a paragraph, not a book) — the "пойди и узнай" gesture in URL form.
- **A `Калитка`** — one daily-rotating short passage on `/` above the fold, on its own URL, dated. The site as a daily reading discipline.
- **Search**: Pagefind static index, surfaced as a `⌕` chip in the top rail. Out of scope for v1 mockup but trivial to add.
- **Poetry covers as a frontispiece treatment** rather than card thumbnails — the existing covers are AI-illustrated and varied; better used as full-bleed page openers than as competing miniatures.
- **`/license` page in his voice** — not a CC0 boilerplate. "Эти книги — твои. Делай что хочешь. Не молись на них, не запирай их в музей, не продавай ярлык "оригинал". Просто читай."

---

## File map

- `design/mockup-v1.html` — single-file fidelity prototype.
- `design/user-flows.md` — this document.
- `legacy/` — existing site, source of truth for content and the corpus.
