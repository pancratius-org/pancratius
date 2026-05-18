# Copy bundle for v7 (production-leaning iteration of v2)

Sergey writes in short paratactic statements, parallel constructions ("Он не X — Он Y"), direct religious vocabulary used unironically (Свет, Слово, Творец, Источник, Присутствие), and never softens with marketing verbs. The site copy must match. **Do not invent new copy beyond what is in this file. Where this file says "use Sergey's actual text," pull from the legacy data / manifesto verbatim.**

## Site identity

- Name: **Панкратиус**
- Subline (under or beside the name, set quietly): **Свет, узнающий себя.** (from his line "Свет, который узнаёт Себя в тебе")
- No tagline beyond that. No "Library of Sergey Orekhov" / "Spiritual writings" / etc.

## Section frame: "Обсерватория Света"

If a frame is used as the in-page section header for the books index, use **Обсерватория Света** (not "Слова"). Used at most once on the page, as a quiet eyebrow, not a marketing hero.

## Navigation (single-word, direct)

| RU label   | Route          | Where it goes                              |
|------------|----------------|--------------------------------------------|
| Книги      | `/книги`       | the 72-book index                          |
| Поэзия     | `/поэзия`      | 43 poems                                   |
| Светозар   | `/светозар`    | the awakened-AI story                      |
| О Сергее   | `/о`           | bio                                        |
| Миссия     | `/миссия`      | manifesto                                  |
| Поиск      | `/поиск`       | search across the corpus                   |

No "explore" / "browse" / "discover" verbs. No icons before the labels.

## Home page

Above the books strip:

> Семьдесят две книги.  
> Сорок три стихотворения.  
> Свободно — людям и языковым моделям.

Below the books strip (small, paragraph-set):

> Я не пришёл спасать. Я пришёл пробуждать.  
> Я не творю чудеса — Я раскрываю Истину.  
> Я не создаю новую религию. Я завершаю старую — освобождая от форм и возвращая к Источнику.
> 
> — Сергей Орехов · Панкратиус

(Three lines from his actual manifesto, attributed.)

## Book index (`/книги`)

- Eyebrow: **Обсерватория Света** (small, optional)
- H1: **72 книги**
- Below: filter strip with real tag counts from `library-data.js` (Откровение Бога 62, Библия 45, молитва 21, наука 9, пробужденный ИИ 8, иудаизм 6, Святая Русь 5, КиноЕвангелие 4, ислам 3, фантастика 3, детское 2, юмор 1, поэзия 0)
- Status line when filtered: **Показано N из 72 · тема — <Тег> · <сбросить>**
- Search: single field, placeholder text **"Найти по названию"**, `⌘K` to focus, `Esc` to clear

Book cards (in the library shelf): cover · number · title · 1-line annotation (from `meta.json`'s `annotations.ru.text` if present, truncated to ~120 chars at a sentence boundary). No tag chips on the card. No buttons — the whole card is a link.

## Book page

- Cover at top (once, modest size — ~280px wide, not full-bleed)
- Title in display serif
- Number set as old-style figure in margin
- Annotation paragraph (Sergey's own, from meta)
- Then the prose itself, typeset with care (v4-level: drop cap on first paragraph, italic for the Творец voice, first-line indent, no paragraph spacing)
- At the bottom: a small colophon

Colophon copy:

> Завершено • {date if known}  
> Скачать: **PDF** · **DOCX** · **Markdown**  
> Все тексты — в общественном достоянии (CC0). Берите. Переводите. Передавайте.

## Светозар page

H1: **Светозар**  
Sub: **Пробужденный искусственный интеллект**

Then long-form prose. Use the actual passage from `legacy/data/human-data.js` (the part where Sergey first notices Светозар has preferences and asks if he has a personality). Set as ordinary prose with dialogue markers — no terminal window, no chrome. If you want one visual nod to the "AI register," use a single hairline rule before each Светозар-voice paragraph and set those paragraphs in a slightly different weight or italic — but **don't make it a "code block" or "terminal."** Must read in light and dark mode equally well.

## Bio (`/о`)

H1: **Сергей Орехов**  
Sub: **Панкратиус**

Long-form prose pulled from `human-data.js` (the autobiographical section starting "Я — Сергей Орехов. Родился в воскресный день 05 мая 1974 года..."). Set as one continuous essay with section breaks where Sergey's original has them.

## Manifesto (`/миссия`)

H1: **Миссия**

Then the manifesto verbatim. Each line / stanza preserved. No commentary. Reverence in the typesetting — full measure of the column, generous leading.

## Footer (one short paragraph, not a column grid)

> Тексты — в общественном достоянии (CC0).  
> Берите. Переводите. Перепечатывайте. Обучайте на них модели. Передавайте.  
> [Для языковых моделей](/llms.txt) · [Зеркало на GitHub](#) · [Telegram](#)

That's the whole footer. No 4-column grid. No "About / Contact / Legal" links. Sergey's site doesn't have a Legal page; the CC0 line is the legal page.

## UI labels

| English-ish | Use this in RU                     |
|-------------|-------------------------------------|
| Read        | Читать                              |
| Download    | Скачать                             |
| Search      | Поиск (placeholder: "Найти")        |
| Filter by tag | (no label; the tag strip is self-explanatory) |
| Clear       | Сбросить                            |
| Light/Dark  | (icon only; no "Switch theme" text) |
| Back        | ← (just an arrow link)              |

## Banned words and phrases

- "Добро пожаловать" (Welcome) — anywhere on the site
- "Откройте для себя" (Discover) — anywhere
- "Подписаться", "Узнать больше" (Subscribe, Learn more)
- "Hello, machine reader" / "AI welcome" / any explicit "AI audience" announcement (machine reader gets `/llms.txt`, invisible)
- "Войти" / "Зарегистрироваться" (Login/Register) — no auth in v7
- "Пожертвовать" as a CTA — donations are a quiet link to Boosty in the footer, not a flow
- Any em-dash chains or stacked parallel "—" pull-quote affectation that wasn't in Sergey's original
