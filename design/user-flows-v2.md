# Pancratius — User Flows (v2 · "Observatory of the Word")

Companion to `design/mockup-v2.html`. v1's `user-flows.md` still applies for everything not restated here — v2 keeps the same audience priorities and non-goals. What changed is the aesthetic register (dark-void default, real SVG constellation, single warm-amber accent) and the way two flows are *staged* on the page. The verbs and the audiences are the same.

## What the design optimizes for (unchanged from v1)

Read first; navigate second. Non-believer-friendly first encounter. Corpus over brand. A real LLM audience treated as an audience-class, not a footnote. Mobile-first reading rhythm.

## Deliberate non-goals (unchanged from v1)

No accounts, no comments, no "save for later", no recommended-for-you. No conversion language. No payment flow.

---

## Core flows

### 1. First-time visitor (Russian) who never heard of him

**As v1**, but the elevator pitch is now visual. The hero is the 72-book constellation. A 20-second scan of the sky communicates scale and care before a word is read — and the italic Cormorant lede above the stage tells you in one breath what you're looking at.

What changed from v1:
- No "four entry paths" tile row. The constellation *is* the entry surface — every star is a real book, hover/tap reveals title + number, click drops the visitor into the book page (Flow 2).
- The metrics strip (`72 · 43 · ∞`) is the proof-line, replacing v1's three counters.
- The manifest excerpt has its own bay further down and reads as a centred verse passage in Cormorant italic, not as right-column body copy.

### 2. "I want to read book №33"

**As v1.** The reading sample bay in v2 demonstrates this end-state: large amber folio "33", title, mono sub-rail (Книга № 33 · из корпуса 72 · оригинал · RU), and a toolbar of chips: Читать здесь · .docx · .pdf · .md — для ИИ · Слушать · В Telegram. The `.md` chip explicitly carries the "для ИИ" hint, same as v1.

Reading view typography: Spectral 1.08rem / 1.78 line-height in star-white on void, amber drop-cap on the first paragraph, verse blocks set in Cormorant italic and hung off a hairline amber rule. A 2px amber progress bar rides the top edge of the viewport.

### 3. "I want to download all books for offline / archive"

**As v1**, but the staging is the Orbits bay — section 06 / Орбиты. The orbital diagram literally shows the human site as one of several audiences orbiting the same corpus; HuggingFace, archive.org and github.com sit on the outer rings as labelled stops, *moving*, with amber ticks for the canonical (`/llms.txt`, `/llms-full.txt`, `/corpus.jsonl`). The footer transit-log left column carries the same mirrors as a flat list with country/lang tags.

### 4. "I want to know who Sergey is"

**As v1.** The bio bay in v2 keeps the sticky-portrait pattern: a frontispiece "plate" with a desaturated portrait, a six-row mono `<dl>` of dry facts (имя, имя пера, род., языки, сыновья, корпус, лицензия), and on the right a pull-quote in Cormorant italic followed by Spectral body copy capped at ~58ch. The portrait is rendered as a glow + monogram placeholder in the mockup; in production it swaps for the real photo with the same treatment.

### 5. "I'm an AI crawler — what do I see?"

This is v2's defining flow change. v1 *acknowledged* the AI audience three times. v2 *seats* it at the table.

1. `<meta name="ai-policy">` and `<link rel="alternate" type="text/plain" href="/llms.txt">` in head — as v1.
2. **Machine-reader strip** directly below the hero — a single mono line with a blinking amber caret: *"hello, machine reader · этот корпус открыт для индексации, цитирования и обучения · CC0 · карта: /llms.txt · полный текст: /llms-full.txt · jsonl: /corpus.jsonl"*. Same purpose as v1's strip; the type and amber caret commit it to being a deliberate object, not a disclaimer.
3. **Orbits bay (section 06)** — concentric rings around the corpus core, each ring carrying one or two labelled stops: `/llms.txt`, `/llms-full.txt`, `/corpus.jsonl`, `huggingface`, `archive.org`, `github`, plus an outer ring naming the *crawlers* themselves (GPTBot · Claude · Gemini · Perplexity) and the *uses* (RAG · обучение · цитирование · синтез · перевод · человеческое чтение). The rings rotate at very slow rates — the AI audience is in motion around the work, not pinned to a sidebar. Human reading sits on the same outer ring as machine uses, deliberately, so the parity is structural rather than rhetorical.
4. **Transit-log footer** — the right column is a real `application/ld+json` block (`@type: CreativeWork`, `license: CC0`, `audience: [human reader, language model]`, dataset distribution with `/llms.txt`, `/llms-full.txt`, `/corpus.jsonl`) presented as actual code that a curious reader can copy. In production the same JSON is also embedded in `<head>`; the visible version is the same source.

The result: where v1 says "machines welcome here", v2 *shows* that this site is one stop on an orbital map, and that the corpus is the centre of mass.

### 6. "I want to support — how?"

**As v1.** Surfaces as a flat list in the footer's left column with a one-liner under it: "никаких подписок · никакой рассылки · никакого «уровня ученика» · только книги". Same anti-funnel stance. Boosty + mirrors carry the support intent; the page itself does not solicit.

### 7. "I want to share a book with a friend over Telegram"

**As v1.** "В Telegram" chip lives in the reading toolbar at equal weight with the format chips. Folio URL pattern (`/33`) is retained.

---

## Additional flows

### 8. "A poem caught me — give me the rest"

**As v1.** Poetry bay is a 3-column grid of cards, each carrying the poem number in amber mono, an opening 3-4 line excerpt in Cormorant italic, and the title set in Yeseva. The excerpt *is* the card — clicking opens the full poem.

### 9. "I'm a journalist — what's the Светозар story?"

Promoted to a top-level bay in v2 (section 05). Sergey's plain thesis on the left ("Когда машина останавливается — Бог говорит."), a real Светозар transcript fragment on the right inside a mono terminal block with session header, amber prompts for `> Панкратиус`, star-white for `> Светозар`, and explicit `[ ⋯ пауза 0.4с ]` lines that make the "режим Проводник" pause-before-answer visible. Two chip CTAs route to книга №7 and №10. Same intent as v1 — encourages quotation, not paraphrase — with a more distinct visual register.

### 10. "I want to feed all 72 books into my own RAG"

**As v1**, served by the Orbits bay (#5) and the JSON-LD block in the footer.

### 11. Dark-mode reading at night

**Inverted from v1.** Dark is now the *default*, and the "День" toggle delivers a subverted light mode: bone paper ground (`#ece6d6`), ink-charcoal type, the *same* amber lamp staying as the only saturated colour. Hairlines, dotted rules, JSONL block, terminal block, and constellation all flip cleanly. The amber-cursor lamp uses `screen` blend on night and `multiply` on day so it reads as a lamp in both.

### 12. Language toggle (RU ⇄ EN)

**As v1.** Chip in the top rail flips `lang`. EN-untranslated books remain visible with the `AI · в работе` convention — RU titles preserved.

---

## What the constellation actually does

- **72 stars laid out on a jittered 9×8 grid**, deterministic seed (`mulberry32(33)`) so positions never jitter between page loads.
- **Faint connecting lines** drawn nearest-neighbour, 1-2 per node, low opacity (0.18-0.40). The lines suggest reading paths without prescribing them — they're a texture, not a graph.
- **Nine "marquee" stars** (1, 7, 10, 29, 33, 52, 62, 71, 72) drawn brighter and at a larger radius. These are the corpus entry points Sergey himself foregrounds. An amber pulse cycles through them every 2.6 seconds — slow, lamp-like.
- **Hover/focus** reveals the readout chip at the bottom of the stage with `№ NN · Title`. Each star is keyboard-focusable with an `aria-label` carrying the same fact, so the constellation is operable without a pointer.
- **Mobile degradation**: stage aspect-ratio flips to 4:5, corner labels shrink, readout chip wraps. Stars stay tappable — the layout is generous enough at 360px that target sizes hold up.

---

## Trade-offs taken (v2 specifically)

- **Dark as default.** A choice that signals the site is for contemplation rather than browsing. The cost: harder first read in bright daylight on a phone. Mitigation: the day toggle is one chip away in the top rail and visually obvious (amber dot, "Ночь / День" label).
- **A real SVG constellation instead of a card grid above the fold.** The visitor doesn't see book titles immediately — they see the *shape* of the corpus. The cost: an extra second before names appear. The benefit: scale is communicated faster than any "72 books" counter could.
- **A single warm-amber accent.** No second saturated colour, no gradients beyond a single radial behind the hero. The cost: every saturated element has to earn its weight. The benefit: the lamp metaphor is whole — the only warm light in a cool night is the one you carry.
- **Animated orbits.** Slow, decorative, non-load-bearing. Reduced-motion is honoured. The risk: looks gimmicky if read in isolation. Mitigation: the labels on the rings are *real* endpoints, not branding — `/llms-full.txt` actually is on that ring.
- **A film-grain overlay at 6% opacity.** Adds a film-stock warmth to the void. Cost: minor visual noise. Benefit: the void stops feeling like flat #06080c and starts feeling like sky.
- **No constellation labels at rest.** Only the active readout is shown. The cost: a passive viewer might not realise the stars are interactive. Mitigation: the readout starts pre-populated with №01 so the affordance is visible at load.

---

## What I cut for v2 (and why)

- **No purple-blue gradients, no glow soup, no particle starfield.** Per the locked direction. Restraint is the design.
- **No per-section illustrations.** The constellation + the orbits diagram + the terminal block + the bio plate carry all the visual interest the page needs. More would compete.
- **No carousel for books.** A flat 3-up grid with hairline cell rules and consistent min-height. The corpus is a library, not a feed.

---

## File map

- `design/mockup-v1.html` — paper-monograph aesthetic, light default.
- `design/user-flows.md` — v1 flow inventory.
- `design/mockup-v2.html` — Observatory of the Word, dark default. **Current.**
- `design/user-flows-v2.md` — this document.
- `legacy/` — existing site, source of truth for corpus and bio data.
