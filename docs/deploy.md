# Deploy runbook

Operational wiring for the two origins. Architecture/contracts live in
[`architecture.md`](./architecture.md) and [`i18n-routing.md`](./i18n-routing.md);
this file is the actionable detail (exact rules, host config, smoke checks).

## Origins

One static `dist/` serves two regional mirrors, deployed by `.github/workflows/main.yml`:

| Origin | Host | Apex `/` | Default language |
|--------|------|----------|------------------|
| `pancratius.ru` | beget (rsync over SSH) | serves the baked `301 → /ru/` | Russian |
| `pancratius.org` | Cloudflare Pages | edge rule `→ /en/` (below) | English |

Every page is locale-prefixed (`/ru/…`, `/en/…`). The bare `/` carries no content;
each host redirects it to its default-locale home.

## Apex redirects

- **`.ru` (beget): no config.** `dist/index.html` is an instant `301 → /ru/` baked by
  `src/pages/index.astro`; beget serves it as-is.
- **`.org` (Cloudflare): one zone Redirect Rule** (Rules → Redirects), required —
  without it `.org/` serves the baked `→ /ru/` and lands English visitors in Russian:

  - When: `(http.host eq "pancratius.org" and http.request.uri.path eq "/")`
  - Then: Static redirect → `https://pancratius.org/en/`, status `301`, preserve query.

  It runs at the edge before the Pages origin, so the baked stub is never reached on
  `.org`. It is host-scoped, so it does not affect `.ru` while both share the project.

## Post-deploy smoke check

Run after a deploy or any apex/redirect change (a missing `.org` rule fails silently):

- `pancratius.ru/` → `301`/redirect to `/ru/`; `pancratius.ru/ru/` serves Russian.
- `pancratius.org/` → `301` to `/en/`; `pancratius.org/en/` serves English.
- `pancratius.ru/robots.txt` lists both sitemaps; `sitemap-ru.xml` is reachable.
- a known work resolves on both prefixes (e.g. `/ru/books/<ru-slug>/`, `/en/books/<en-slug>/`).

## rsync `--delete` safety

The beget deploy uses `--delete` with `EXCLUDE: "/.*"`, which protects every top-level
remote dotfile/dir the build doesn't own — notably `.well-known/` (ACME/TLS renewal)
and `.ssh/`. Do not narrow this exclude to ship a dotfile; the apex redirect is baked
into `dist/index.html`, so nothing dot-prefixed needs to transfer.
