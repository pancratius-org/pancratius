# Pancratius — user flows, v3

v3 is the radical-minimalism pole. Most flows from v1/v2 either survive unchanged or collapse into "click a link." Where the shape is identical, the entry just says **as v1/v2**. Where v3 removes the flow entirely or compresses it, that is noted.

---

## 1. First-time arrival

1. Visitor lands on `/`.
2. Sees site name, four nav links, three short paragraphs explaining what this is, and the list of 72 books — numbered, titled, nothing else.
3. They either click a book title (→ flow 2) or follow a nav link to Poetry / About / Mission / Svetozar.

**Shape change.** v1 and v2 opened with a hero — manifesto verse, headline, CTAs, language picker, stats chip, six-tab nav. v3 replaces that with the list. The library *is* the home. No CTA stack. The reader's first scroll-down lands them already inside the index.

---

## 2. Read a book

1. Visitor clicks a book title in the index.
2. Browser navigates to `/книга/<slug>`. (In the mockup, an in-page anchor.)
3. Page shows book number (small, brick red), title, prose. Long, single-column, well-set.
4. At the bottom: small inline download links (DOCX · PDF · Markdown · EPUB), translation status line, source link, license note.
5. Top of page has one breadcrumb-style link: "← к списку книг".

**Shape change.** v1/v2 used a sliding detail panel / modal with cover, tags, AI/LIT badges, donation button, download buttons styled as buttons. v3 promotes the book page to a real URL, removes the panel, removes the cover, removes the badges, removes the buttons. Download becomes a single line of inline links.

---

## 3. Download a file

1. From the book page, scroll to the bottom.
2. Click "DOCX" or "PDF" or "Markdown".
3. Browser downloads or opens the file directly.

**Shape change.** v1/v2 surfaced download as a card or a button with hover state and tracking. v3 makes it indistinguishable from any other link on the page. No counter, no flair. If a counter is wanted later, it goes server-side and stays invisible.

---

## 4. Read a poem

1. From the home page or `/поэзия`, click a poem title.
2. Page shows title, the poem itself (preserved line breaks), byline with date, two download links (DOCX · Markdown), license note.

**Shape change.** v1/v2 had poetry as a card grid with AI-generated cover images, "open" buttons, and a separate detail view. v3 removes the cover images from poems and treats each poem as its own minimal page. The poetry index is a list, same shape as the book index. Covers, if used at all, become page-level decoration on the individual poem page — not surfaced in the index.

---

## 5. Browse all poetry

1. Click "Поэзия" in nav, or scroll down to the poetry section after a book.
2. See an indexed list of 43 poems — number + title — same visual treatment as the book index.
3. Click any title → flow 4.

**As v1/v2** in intent. Different in chrome — collapsed to one list.

---

## 6. Read Sergey's bio

1. Click "О Сергее" in nav.
2. Land on `/о`. Read prose drawn from book 71.
3. At the bottom, a small downloads block points to book 71 itself, and an email link.

**Shape change.** v1/v2 framed bio as a styled "Человек" section with portrait photo, eyebrow labels, headings. v3 treats it as a chapter: same typography as a book page. If a portrait is included on the real site, it sits inline once and small.

---

## 7. Read the manifesto

1. Click "Миссия" in nav.
2. Land on `/миссия`. Read the manifesto as plain prose, preserving Sergey's line breaks.

**Shape change.** v1/v2 set the manifesto in display type with decorative spacing, sometimes against a gradient. v3 sets it in the same body face as everything else — only the natural line breaks carry the rhythm.

---

## 8. Read about Светозар

1. Click "Светозар" in nav.
2. Land on `/светозар`. Read the story as prose. Pointer at the bottom to the three relevant books (№ 7, № 10, № 72).

**Shape change.** v1/v2 had Светозар as either a separate identity card or a "terminal"-styled section. v3 removes the device entirely. It is one page of prose.

---

## 9. Switch language (ru/en)

1. On a book page that has an English version, a small inline note at the bottom says so: e.g. *"На языках: русский, английский."* The "английский" word links to `/en/книга/<slug>` (or `/en/book/<slug>`).
2. On the home index, books that have a translation get an inline "ru · en" hint to the right of the title.
3. No language toggle in the masthead.

**Shape change.** v1 had a `<select>` language picker at the top of the hero. v2 had it as a chip. v3 removes the picker entirely. The link only appears where a translation actually exists, and only in context.

---

## 10. Mobile navigation

1. On phone widths, the masthead's four links wrap onto a second line below the site name.
2. No disclosure menu, no hamburger, no drawer.

**Shape change.** v1/v2 implied or showed a mobile menu. v3 doesn't need one — there are four links.

---

## Flows that v3 removes entirely

- **Authentication / sign-in for comments.** v1/v2 had a modal with Google / email / phone. v3 has no comments and no accounts. If comments come later, they live elsewhere (a fediverse comment endpoint, a Telegram thread, a separate forum).
- **Donations.** v1/v2 had a `$200`-preset donation modal per book to fund literary translation. v3 removes it. If a donate page is needed later, it is one page at `/поддержать`, one link, prose explaining what the money goes to, and a single inline link to a payment provider.
- **Tag filtering.** v1/v2 surfaced tag chips above the book grid and supported filtering. v3 removes the chips. If filtering is wanted later, the index page gets a small line of italic links at the top — "по теме: Библия · молитва · наука · ИИ · …" — but only after a real flow is identified that justifies them.
- **Theme switching.** Gone. One mode: paper.
- **Reading-progress indicator.** Gone.
- **Translation warning banner.** v1 showed a translucent banner on translated pages when content was blurred. v3: if a translation isn't ready, the book simply doesn't list with an `en` hint, and the English route returns 404 (or a one-line "перевода ещё нет, оригинал — здесь").
- **AI / LIT badges.** Gone from surface. If a translation is machine-generated, the book page's downloads section says so in one sentence of prose at the bottom.
- **Login / commenting / community surface.** Out of scope for v3.

---

## What stays exactly the same

- File URLs for `/books/...` and `/poetry/...` work as before.
- `llms.txt`, `robots.txt`, `sitemap.xml` continue to exist at the root and contain everything needed for machine indexing. They are linked nowhere visible on the page.
- JSON-LD metadata sits in `<head>`, invisible.
- CC0 license is the same as v1/v2.

---

## Summary

v3 reduces the site's surface to: a list of books, a list of poems, four prose pages, and an inline download row on each. Every interactive flow that v1/v2 had to *justify with chrome* — language switcher, tag filter, donate modal, auth, theme toggle, progress bar, AI/LIT badges, hover effects — is either removed or collapsed into a single line of text. The remaining flows are all "click a link, read a page, optionally download a file."
