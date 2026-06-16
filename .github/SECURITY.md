# Security Policy

## Reporting a vulnerability

Please report security issues privately when you can: open the repository's
**Security** tab and choose **Report a vulnerability**, which opens a private
advisory visible only to the maintainers. A regular GitHub issue is also fine if
private reporting isn't available to you.

Please include what you found, where (URL / file / commit), and a minimal way to
reproduce it. We aim to acknowledge a report within **5 business days** and to
agree on a disclosure timeline once the issue is confirmed.

## Supported versions

The site is continuously deployed from `main`; only `main` is supported. Fixes
land on `main` and ship on the next deploy — there are no maintained release
branches.

## Scope

Pancratius is a **static site** (Astro → static HTML, no server runtime, no user
accounts, no database, no user-submitted data) plus a **local content tool**
(the `pancratius` Python CLI) that maintainers run to import and build library
content. The realistic security surface is therefore:

- **Stored injection through imported content.** Library content originates from
  trusted, admin-authored DOCX, but the import boundary still sanitizes embedded
  SVG (`pancratius/svg_sanitize.py`) to keep `<script>`/`on*`/`javascript:` and
  similar gadgets out of served assets. A bypass of that boundary is in scope.
- **Client-side rendering of build data.** Interactive surfaces (e.g. the
  conceptosphere) hydrate from build-generated JSON. Reports showing
  untrusted-input reaching a dangerous sink are in scope.
- **Supply chain & CI.** Dependency or GitHub Actions issues that could affect a
  build or deploy. Third-party action pins and dependency lockfiles are kept
  current by Dependabot; CodeQL scans the source on every push.

Out of scope: findings against third-party hosting/CDN infrastructure we do not
control, and theoretical issues with no path to impact on a static, data-less
site (please still tell us if you are unsure — we would rather hear it).
