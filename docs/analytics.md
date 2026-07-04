# Analytics

Umami Cloud, one tracker tag in the shared head
(`src/components/UmamiAnalytics.astro`). Cookieless and anonymous; the reader
is never identified. The site wants to know what is read, sought, and taken
away.

## Collection

- **One website id, two hostnames.** Both mirrors report into one Umami
  website; the dashboard's *Hostname* filter separates `pancratius.ru` from
  `pancratius.org`. Caveat: the visitor hash may include the hostname, so
  visitor-cardinality metrics (Visitors, retention, first-click attribution)
  are per-mirror, not per-person; event counts split cleanly.
- **Production only.** `data-domains` lists the production hostnames (from
  `src/lib/origins.ts` defaults, ignoring per-deploy preview overrides), so
  localhost, previews, and CI never report.
- **Non-default tracker settings, each with one reason.**
  `data-exclude-hash`: the conceptosphere keeps its mode in the URL hash;
  without it every mode flip would mint a pageview. `data-performance`: Core
  Web Vitals ride the same beacon. Query strings are kept — UTM depends on
  them. Everything else is Umami defaults.
- **Search URLs.** A completed search reflects its query in the URL, so each
  executed search is one `/…/search/?q=…` pageview plus the `search` event.
  Partial keystrokes reach neither.

## The observer layer

Components never talk to Umami. They expose semantic markup; one module —
`src/lib/analytics.ts`, bound from the head component — observes it:

- **Anchors:** `data-track-event` + `data-track-<prop>`, handled by a
  delegated listener that never touches navigation. Umami's own
  `data-umami-event` must never sit on an anchor — its handler re-navigates
  via `location.href` and breaks `download` links. A unit test enforces it.
- **Buttons:** native `data-umami-event` + `data-umami-event-<prop>`.
- **Page state** (reading depth, completed searches, 404): observed from the
  DOM.

Event data keys are single words; the URL already carries work, locale, and
section, so data repeats only what property breakdowns need.

## Events

| Event | Fires when | Data |
| --- | --- | --- |
| `work-read` | end of a work body seen after ≥ 30 s on the page | `kind slug locale` |
| `work-progress` | reading depth 25 / 50 / 75 % reached by scrolling | `kind slug locale progress` |
| `work-download` | a work's format link | `kind slug locale format` |
| `archive-download` | bulk corpus archive | `archive locale format` |
| `video-play` | embedded player opened | `number platform locale` |
| `video-watch` | outbound watch / mirror link | `number platform locale` |
| `channel-open` | Telegram / email / video-channel link | `channel locale` |
| `language-switch` | switcher option | `from to` |
| `feed-subscribe` | RSS link | `locale` |
| `search` | a search completes (true match count) | `query results locale` |
| `share` | share button (intent; the sheet may be cancelled) | `locale` |
| `support-copy` | support page: value copied | `channel kind locale` |
| `support-open` | support page: link channel opened | `channel locale` |
| `not-found` | 404 page rendered | `path` |

`work-read` means different things by length. For a book it's "finished on
the site" — rare by nature. For a work shorter than the viewport (many poems,
messages) the end shows at landing, so it's really "held open and visible for
30 s". `work-progress` fills the gap for long works: it counts only depth
reached by scrolling — landing position excluded, sub-viewport works emit
none — so "does anyone get past the first quarter of this book" has an answer.
Everything else (visit duration, per-page dwell, referrers, countries,
devices) is Umami's automatic collection.

## Dashboard setup

Reports live in Umami Cloud; this is their contract. Goals and journey steps
take exact URLs or event names; funnels additionally take URL wildcards.

- **Goals:** `work-read`, `work-download`, `feed-subscribe`.
- **Funnels:** `/ru/` → `/ru/books/*` → `work-read` → `work-download`; the
  video bridge `/ru/videos/*` → `/ru/books/*` → `work-read`. Set a generous
  step window (hours — reading takes time); steps are a sequence, not tied
  to the same work.
- **Journey:** 3–7 steps from `/ru/` or `/en/`; events appear inside paths.
- **Retention:** the first-visit-day cohort grid, as-is.
- **Attribution:** conversion `work-read`, first-click model; breakdowns by
  referrer and UTM.
- **UTM for off-site links:** `utm_source` = platform (`youtube`,
  `telegram`), `utm_medium` = placement (`description`, `post`),
  `utm_campaign` = what is promoted (`video-31`, a book slug). Referrers
  alone under-attribute — YouTube apps and Telegram clients often strip
  them.
- **Self-exclusion:** localStorage `umami.disabled = 1` on both hostnames,
  per browser profile.

Not used: replays and heatmaps (paid Cloud features behind a separate
~190 KB recorder script), `identify()`, revenue. Accepted undercounts:
ad blockers (no proxy/evasion), middle-click opens. The `.ru` apex
meta-refresh can lose the referrer — share locale URLs, not the bare apex.
