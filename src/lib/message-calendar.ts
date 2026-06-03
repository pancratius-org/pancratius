// The месяцеслов — pure, payload-agnostic calendar model.
//
// Split from `messages.ts` the same way `video-format.ts` is split from
// `videos.ts`: this module imports no `astro:content`, so it is unit-testable
// under `node --test` and free of build-graph concerns. It is also generic over
// its payload `T` — a calendar has no business knowing about `CollectionEntry`.
// `messages.ts` instantiates `T = LocalizedMessagePair` and re-exports the
// public surface; route files keep importing from `messages.ts`.
//
// Both surfaces read this: the index renders each month as a grid panel and
// steps through them; the detail-page sidebar renders the month-grouped list.
// Dates are plain `YYYY-MM-DD` strings — parsed into integer fields rather than
// `Date` objects so timezone never shifts a post off its day. The one place a
// `Date` appears (`daysInMonth`, weekday-of-1st) feeds the grid skeleton, never
// a post's own day.

/** A dated payload the calendar arranges. `order` breaks within-day ties
 *  (higher sorts first = newer); `value` is carried opaquely into the result. */
export interface DatedItem<T> {
  iso:   string;   // "YYYY-MM-DD"
  order: number;
  value: T;
}

interface YMD { y: number; m: number; d: number; }

/** Parse a validated `YYYY-MM-DD` string into integer fields (m is 1..12). */
function parseISODate(iso: string): YMD {
  const [y, m, d] = iso.split("-").map(Number);
  if (y === undefined || m === undefined || d === undefined) {
    throw new Error(`malformed date ${JSON.stringify(iso)}`);
  }
  return { y, m, d };
}

/** Build-time "today" as `YYYY-MM-DD` (UTC). The live calendar re-derives the
 *  client's own today in the browser; this is the SSR default. */
export function todayISO(): string {
  const now = new Date();
  const y = now.getUTCFullYear();
  const m = String(now.getUTCMonth() + 1).padStart(2, "0");
  const d = String(now.getUTCDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}

function monthKey(y: number, m: number): string {
  return `${y}-${String(m).padStart(2, "0")}`;
}

/** Days in a Gregorian month (m: 1..12). */
function daysInMonth(y: number, m: number): number {
  return new Date(Date.UTC(y, m, 0)).getUTCDate();
}

/** Weekday of the 1st, Monday-indexed (0 = Mon … 6 = Sun). */
function firstWeekdayMondayIndexed(y: number, m: number): number {
  const sundayIndexed = new Date(Date.UTC(y, m - 1, 1)).getUTCDay(); // 0 = Sun
  return (sundayIndexed + 6) % 7;
}

export interface CalendarDay<T> {
  day:     number;
  iso:     string;
  items:   T[];
  isToday: boolean;
}
interface CalendarMonth<T> {
  key:       string;   // "YYYY-MM"
  year:      number;
  month:     number;   // 1..12
  /** Row-major 7-column grid; null cells are leading/trailing padding. */
  cells:     (CalendarDay<T> | null)[];
  itemCount: number;
  hasToday:  boolean;
}

export interface Calendar<T> {
  months: CalendarMonth<T>[];
  /** Index into `months` the carousel should open on (current month, else the
   *  newest month that actually has posts). */
  initialIndex: number;
}

/**
 * The continuous run of months from the earliest item to `max(today, latest
 * item)`, each with its items bucketed onto days. A continuous range (not only
 * months-with-items) is what makes it read as a real calendar you leaf through,
 * rather than a list wearing a grid costume.
 */
export function buildCalendar<T>(items: readonly DatedItem<T>[], today: string): Calendar<T> {
  const byDay = bucketByDay(items);
  const t = parseISODate(today);
  const dated = items.map(i => monthOrdinal(parseISODate(i.iso)));
  if (dated.length === 0) {
    // No items: a single current month so the surface still renders a frame.
    return { months: [buildMonth(t.y, t.m, byDay, today)], initialIndex: 0 };
  }
  const minOrdinal = Math.min(...dated);
  const maxOrdinal = Math.max(...dated, monthOrdinal(t));
  const months: CalendarMonth<T>[] = [];
  for (let ord = minOrdinal; ord <= maxOrdinal; ord++) {
    months.push(buildMonth(Math.floor(ord / 12), (ord % 12) + 1, byDay, today));
  }
  return { months, initialIndex: pickInitialIndex(months, monthOrdinal(t) - minOrdinal) };
}

/** Bucket items onto their day, highest `order` first within a day. */
function bucketByDay<T>(items: readonly DatedItem<T>[]): Map<string, T[]> {
  const grouped = new Map<string, DatedItem<T>[]>();
  for (const item of items) {
    const list = grouped.get(item.iso);
    if (list) list.push(item);
    else grouped.set(item.iso, [item]);
  }
  const out = new Map<string, T[]>();
  for (const [iso, list] of grouped) {
    list.sort((a, b) => b.order - a.order);
    out.set(iso, list.map(i => i.value));
  }
  return out;
}

/** Open on the current month when it has items; otherwise the newest month
 *  that does (an empty current month is a poor first impression). */
function pickInitialIndex<T>(months: readonly CalendarMonth<T>[], currentIndex: number): number {
  const current = months[currentIndex];
  if (current && current.itemCount > 0) return currentIndex;
  for (let i = months.length - 1; i >= 0; i--) {
    const month = months[i];
    if (month && month.itemCount > 0) return i;
  }
  return Math.max(0, Math.min(currentIndex, months.length - 1));
}

function monthOrdinal({ y, m }: { y: number; m: number }): number {
  return y * 12 + (m - 1);
}

function buildMonth<T>(
  y: number,
  m: number,
  byDay: ReadonlyMap<string, T[]>,
  today: string,
): CalendarMonth<T> {
  const lead = firstWeekdayMondayIndexed(y, m);
  const total = daysInMonth(y, m);
  const cells: (CalendarDay<T> | null)[] = [];
  for (let i = 0; i < lead; i++) cells.push(null);
  let itemCount = 0;
  let hasToday = false;
  for (let day = 1; day <= total; day++) {
    const iso = `${monthKey(y, m)}-${String(day).padStart(2, "0")}`;
    const items = byDay.get(iso) ?? [];
    itemCount += items.length;
    const isToday = iso === today;
    if (isToday) hasToday = true;
    cells.push({ day, iso, items, isToday });
  }
  while (cells.length % 7 !== 0) cells.push(null);
  return { key: monthKey(y, m), year: y, month: m, cells, itemCount, hasToday };
}

// ─────────────────────────────────────────────────────────────────────
// Month-grouped archive — the sidebar list and the index's linear fallback.
// ─────────────────────────────────────────────────────────────────────

interface ArchiveEntry<T> {
  iso:   string;
  value: T;
}
export interface MonthGroup<T> {
  key:   string;   // "YYYY-MM"
  year:  number;
  month: number;   // 1..12
  items: ArchiveEntry<T>[];   // newest day first
}

/** Group items into months, newest month first, newest day first, ties by
 *  `order` desc. */
export function groupByMonthDesc<T>(items: readonly DatedItem<T>[]): MonthGroup<T>[] {
  const groups = new Map<string, { group: MonthGroup<T>; ordered: DatedItem<T>[] }>();
  for (const item of items) {
    const { y, m } = parseISODate(item.iso);
    const key = monthKey(y, m);
    const bucket = groups.get(key) ?? { group: { key, year: y, month: m, items: [] }, ordered: [] };
    bucket.ordered.push(item);
    groups.set(key, bucket);
  }
  const ordered = [...groups.values()].sort((a, b) => (a.group.key < b.group.key ? 1 : -1));
  for (const { group, ordered: rows } of ordered) {
    rows.sort((a, b) => (a.iso < b.iso ? 1 : a.iso > b.iso ? -1 : b.order - a.order));
    group.items = rows.map(r => ({ iso: r.iso, value: r.value }));
  }
  return ordered.map(o => o.group);
}
