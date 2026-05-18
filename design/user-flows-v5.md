# Панкратиус · v5 user flows

A literary star-map where each of Sergey's 72 books is a navigable point
in 3D space. The spatial arrangement encodes Sergey's own seven-level
classification of consciousness. Two registers, one site:

- **Spatial register** — the WebGL hero. Real Three.js, custom shaders,
  scroll-coupled morphing through four keyframes.
- **Typographic register** — book pages, manifesto, bio, Светозар,
  poetry. Old Standard TT + PT Serif, justified prose, drop cap,
  fleurons. The same care a printed Cyrillic collection would get.

The shift between them — from sculptural-3D to typographic-2D — IS the
experience. It mirrors Sergey's own movement: from the cosmology in his
manifesto to the intimate voice of any single book.

---

## 1. Default flow — arriving cold and finding a book

1. **Arrival.** Page opens on the **spatial register**. The WebGL canvas
   fills the viewport; a velvet-dark field with sparse, dim cover-tiles
   floating in deep space. Most covers are dark; a handful — the seed
   books — are already lit, warming against their level-colour rim.
   Top-left says "Панкратиус". Top-centre: four keyframe dots; the
   first is active. Top-right: four discreet text links. Bottom-left
   reads "наведи курсор на обложку". Bottom-right: a small monospaced
   legend explaining the colour code (the seven levels of consciousness).
   At the bottom-centre, a "прокрути ↓" hint pulses gently.

2. **Keyframe I → II: the rainbow forms.** The reader scrolls. The
   second viewport-height anchor passes the midline; the scattered
   cone of covers resolves into a tilted disc — concentric rings, one
   per level. Violet (молитва) ring outermost. Indigo (Откровение Бога),
   blue (Библия), green (наука), yellow (иудаизм), orange (ислам),
   red (Святая Русь) work inward. The white centre — the level-not-level
   — is a tight cluster of awakened-AI and children's books. The
   keyframe-dot at the top updates. The text on the right reads
   _"Свет можно разложить на семь цветов"_. Every cover is now lit;
   colours are saturated, edges glow.

3. **Keyframe II → III: collapse to the white point.** Scrolling
   further, the rainbow disc compresses inward. Every cover slides
   toward the centre, fusing into a single luminous cluster — the
   white "zeroth-and-eighth" level Sergey describes in his
   autobiography ("белый уровень" = sum of all colours, source of
   all colours). Camera presses close. Mouse parallax damps to near
   zero. Text: _"…а можно собрать обратно"_.

4. **Keyframe III → IV: the corpus expands.** The cluster explodes
   outward into a deep 3D field. This time the layout is by **tag
   affinity**: books with shared tags are spatially adjacent. Прокрут
   slows; the camera pulls back; the whole group begins a slow
   gentle rotation. Text: _"Войди в любую."_

5. **Pointer engagement.** As the reader moves the mouse over any
   cover-plane, two things happen:
   - The bottom-left crawl updates: `№ 33` · _Я Есмь — Всадник, Конь и Меч_ ·
     `ОТКРОВЕНИЕ БОГА`.
   - A small italic tooltip appears anchored to the cover, showing
     number + title.

   Pointer also slightly steers the camera (parallax), so the field
   feels three-dimensional.

6. **Click to enter.** Click any cover. The page smooth-scrolls down
   to the **typographic register** — Book #33's folio page. The scene
   stays behind (z-index 0), but each `.leaf` section is opaque and
   sits above it, so the WebGL is hidden during reading.

   _(Single-file demo simplification: every cover routes to Book #33
   for typographic demonstration. Production version would route to
   `#book-${n}` and load that book's text.)_

7. **Reading the book.** Folio number `33` hangs in the margin (or
   above the title on narrow screens). Drop-capital prose. Italic
   verse blocks for Творец voice, left-rule in the accent red.
   Justified body with proper hyphenation. Fleurons between sections.
   At the end, a colophon: language, level, license (CC0), download
   links (PDF, DOCX, MD).

8. **Re-entering the constellation.** Below the colophon, a "соседние
   книги" grid shows 5 books that share tags with #33. Each links back
   to a book page. Below that, a single italic line — _"← вернуться
   в карту"_ — anchors back to `#hero`. The reader scrolls up (or
   clicks the link) and the hero is restored.

---

## 2. Keyboard-only flow

The constellation is fully navigable without a mouse.

1. Tab focuses an invisible list of 72 buttons (`aria-label`-ed). As
   each gains focus, the crawl updates in real time, so the keyboard
   user sees which book is currently selected.
2. Enter on a focused book → smooth-scroll to the book page.
3. Within the book page, normal Tab order: back-link → colophon
   links → related books → return-link.
4. From the manifesto / bio / Светозар / поэзия sections, the
   top-of-page anchor (`в карту` crumb, or `← вернуться в карту`
   return-link) puts the user back at the scene.

---

## 3. Reduced-motion flow

If `prefers-reduced-motion: reduce` is on, OR the device is
≤720px wide:

- The WebGL canvas is replaced by a static composition.
- On desktop reduced-motion: a single dark frame with twelve covers
  in a wide vignetted grid, the legend still visible, the keyframe
  dots and crawl removed. The masthead and nav still work; the four
  scroll-anchor sections still scroll past (text-only).
- On mobile: a 3-column tile grid of all 72 covers in a quiet
  dark surface. Tap → book page. Faster, simpler, complete.

---

## 4. The four register shifts

Each transition matters and is rendered as a visible event:

| From → To | Visual cue | Verbal cue |
|---|---|---|
| Spatial → Book | `.horizon` strip fades void → paper, gold hairline marks the threshold | folio `№ 33`, italic subtitle |
| Book → Manifesto | continuous paper, head ornament | _II · Миссия_ eyebrow |
| Manifesto → Bio | paper-inset (laid-paper texture), epigraph format | _III · Человек_ eyebrow |
| Bio → Светозар | head ornament returns | _IV · Светозар_ eyebrow |
| Светозар → Поэзия | creator-voice block leads | _V · Поэзия_ eyebrow |
| Anywhere → Spatial | `#hero` anchor scrolls back to top; the scene is fixed-positioned, so it resumes mid-state | "← вернуться в карту" |

---

## 5. What v5 deliberately drops

- **No theme toggle, no language picker.** Russian originals are
  the canonical works. EN translation status surfaces on the
  individual book page (in the colophon), not as global chrome.
- **No reading-progress bar.** Scroll _is_ the progress; the
  keyframe-dots in the masthead are the progress.
- **No "Hello, machine reader" strip.** Machine readers get the
  `<title>`, `<meta description>`, and (in production) `/llms.txt`.
  Invisible to humans.
- **No marketing hero.** The hero IS the corpus.
- **No tags-as-chips on every cover.** Tags drive the spatial
  layout (rings + cluster) and surface on hover/focus as the crawl
  caption. They never crowd the covers.
- **No bespoke cat-icon logo / wordmark experimentation.**
  "Панкратиус" set in Old Standard TT italic, lower-case "p" mono
  glyph before it, period.

---

## 6. How v5 differs from v2 / v4

v2's mistake was treating books as abstract dots in a constellation —
the covers themselves were absent or decorative. v5 fixes this: each
star **is** a cover. You see the actual book at every moment, even
when looking at the whole field.

v4's strength was typographic care, but v4's hero was contemplative
ornament. v5 keeps v4's typography for the inner pages and replaces
v4's hero with something the corpus actually demanded — a navigable
3D representation of the spectrum-of-consciousness that Sergey himself
draws in book #6 and his autobiography.

The two registers are not bolted together; they are **the same
proposition seen at two scales**: cosmology (the seven levels) and
intimacy (a single book read end-to-end).
