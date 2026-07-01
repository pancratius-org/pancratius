"""The junk vocabulary, in one place.

Two consumers share it: QA rejects a hook or body that still contains any of
these (a leak), and the deterministic fallback uses :func:`footer_start` to find
where the trailing promo block begins and cut it. Kept as data — building blocks
first, then the patterns composed from them — so "what counts as junk" is
greppable and edited once.
"""

from __future__ import annotations

import re

# ── building blocks (composed below; never let these drift out of sync) ──

# A URL or bare domain: scheme, www, a known messenger/short host, or any
# domain-like token ending in a common TLD. Anchored on a TLD so Russian
# abbreviations ("т.е.", "и т.д.") and "e.g."/"etc." never match.
_URL = (
    r"https?://|www\.\w|\bt\.me/"
    r"|\b[a-z0-9][a-z0-9-]*\.(?:ru|com|org|net|me|to|tv|io|app|dev|info|xyz)\b"
)
# Decorative/promo pictographs: the pictograph plane (1F000–1FAFF), the arrows
# block, and a few common dingbats added explicitly. The misc-symbols block
# (2600–27BF) is deliberately EXCLUDED because it holds religious and text
# symbols (✝ U+271D, ☦ U+2626, ✡ U+2721, …) that belong in this spiritual corpus.
_EMOJI = r"[\U0001F000-\U0001FAFF←-⇿✅✉❤⬅➡▶◀☑⭐✨]"
# A raw HTML tag. The reading body is Markdown prose (no lineated wrappers — a
# video is not a work), so any `<tag>` is an injection; `< b` (a comparison)
# needs a letter immediately after `<`, so it does not match.
_HTML = r"</?[a-z][a-z0-9]*(?:\s[^<>]*)?>"
# A 16-digit bank card in any grouping: spaced, double-spaced, dotted, dashed, or
# continuous.
_CARD = r"\d{4}(?:[\s.\-]*\d{4}){3}"
# An @handle glued to any punctuation (":@x", "(@x)", "@x").
_HANDLE = r"@[A-Za-z0-9_]{2,}"

# ── junk that must never survive into a clean hook or body ──
# Names are the codes the QA verdict reports.
JUNK_PATTERNS: dict[str, re.Pattern[str]] = {
    "url": re.compile(_URL),
    "hashtag": re.compile(r"(?:^|\s)#[^\s#]+"),
    "handle": re.compile(_HANDLE),
    "email": re.compile(r"\b[\w.+-]+@[\w-]+\.[a-z]{2,}", re.I),
    "card_number": re.compile(_CARD),
    "promo_phrase": re.compile(
        r"Поддержать проект|поддержать канал|Следующее\s*[—–-]\s*здесь"
        r"|Книги автора|Слуша(?:ть|й) (?:книг|аудио)|карты? для перевод",
        re.I,
    ),
    "emoji": re.compile(_EMOJI),
    "html": re.compile(_HTML, re.I),
}


def junk_categories(text: str) -> list[str]:
    """The names of every junk pattern that matches ``text`` (empty = clean)."""
    return [name for name, pat in JUNK_PATTERNS.items() if pat.search(text)]


# Inline junk TOKENS (not the phrase patterns) for the fallback to strip from an
# otherwise-kept line — an author sentence with a URL glued inside it.
_INLINE_JUNK = re.compile(
    rf"(?:{_URL})|(?:{_HANDLE})|(?:{_CARD})|(?:{_HTML})|#[^\s#]+|(?:{_EMOJI})"
    r"|\b[\w.+-]+@[\w-]+\.[a-z]{2,}",
    re.I,
)


def scrub(text: str) -> str:
    """Remove inline junk tokens (URLs, handles, cards, hashtags, emoji, HTML,
    e-mails) and collapse the whitespace they leave behind."""
    return re.sub(r"\s{2,}", " ", _INLINE_JUNK.sub(" ", text)).strip()


# ── footer detection (deterministic fallback only) ──

# A line that belongs to the trailing promo footer: an anchored promo marker, or
# a line that already carries junk. The message's own prose never matches at the
# LINE start, so `footer_start` cuts only a genuine trailing block, not a body
# sentence that merely opens with a word like "Дзен".
_FOOTER_MARKER = re.compile(
    r"^\s*(?:"
    + _URL
    + r"|" + _EMOJI
    + r"|Это послание\s*[—–-]\s*из серии"
    + r"|Следующее\s*[—–-]\s*здесь"
    + r"|Книги автора"
    + r"|Поддержать проект"
    + r"|(?:Канал в )?Telegram\b"
    + r"|Дзен\b|Контакт\b"
    + r"|(?:RUB|EUR|USD)[:\s]"
    + r")",
    re.I,
)


def _is_footer_line(line: str) -> bool:
    return not line.strip() or bool(_FOOTER_MARKER.match(line)) or bool(junk_categories(line))


def footer_start(lines: list[str]) -> int:
    """Index of the first line of the trailing promo footer — i.e. the start of
    the last contiguous run of footer/junk/blank lines. Returns ``len(lines)``
    when the description ends in real prose (no footer to cut). Scanning from the
    end keeps a mid-message line that merely starts with a footer word from
    truncating the real text."""
    i = len(lines)
    while i > 0 and _is_footer_line(lines[i - 1]):
        i -= 1
    return i


# A line that is purely an SEO keyword pile — topic/tag lists the author stuffs
# for discovery ("Видео для тех, кого интересуют …; духовность, осознанность,
# bible, Богородица"). Used only as a fallback hint; the model does the real
# judgement.
SEO_KEYWORD_LINE = re.compile(
    r"(?:Видео для тех, ко[гт]|Это послание от Бога о\b|подходит для тех, кто)",
    re.I,
)
