import assert from "node:assert/strict";
import { describe, test } from "node:test";

import {
  buildCalendar,
  groupByMonthDesc,
  todayISO,
  type Calendar,
  type DatedItem,
} from "../../src/lib/message-calendar.ts";

// The calendar is generic over its payload, so the tests use a plain marker —
// no `astro:content` types, no casts.
interface Post { id: number; }
function item(id: number, iso: string): DatedItem<Post> {
  return { iso, order: id, value: { id } };
}

// `assert.ok` narrows away null/undefined without a non-null assertion.
function present<T>(v: T): NonNullable<T> {
  assert.ok(v != null);
  return v;
}

const monthAt = (cal: Calendar<Post>, i: number) => present(cal.months[i]);
const daysOf = (cal: Calendar<Post>, i: number) =>
  monthAt(cal, i).cells.filter((c): c is NonNullable<typeof c> => c !== null);
const dayNamed = (cal: Calendar<Post>, i: number, day: number) =>
  present(daysOf(cal, i).find(d => d.day === day));

describe("buildCalendar — month range", () => {
  test("spans a continuous run from earliest item to today's month", () => {
    const cal = buildCalendar([item(1, "2024-01-15")], "2024-03-10");
    assert.deepEqual(cal.months.map(m => m.key), ["2024-01", "2024-02", "2024-03"]);
  });

  test("extends forward to today when today is past the latest item", () => {
    const cal = buildCalendar([item(1, "2024-01-15"), item(2, "2024-02-20")], "2024-05-10");
    assert.deepEqual(cal.months.map(m => m.key), ["2024-01", "2024-02", "2024-03", "2024-04", "2024-05"]);
  });

  test("crosses a year boundary (Dec → Jan rollover)", () => {
    const cal = buildCalendar([item(1, "2023-11-15")], "2024-02-10");
    assert.deepEqual(cal.months.map(m => m.key), ["2023-11", "2023-12", "2024-01", "2024-02"]);
  });

  test("empty input still yields a single current-month frame", () => {
    const cal = buildCalendar<Post>([], "2024-03-10");
    assert.equal(cal.months.length, 1);
    assert.equal(monthAt(cal, 0).key, "2024-03");
    assert.equal(cal.initialIndex, 0);
  });
});

describe("buildCalendar — grid skeleton (Monday-first)", () => {
  test("a month beginning on Monday has no leading padding", () => {
    // 2024-01-01 was a Monday.
    const cal = buildCalendar([item(1, "2024-01-10")], "2024-01-20");
    const c = monthAt(cal, 0).cells;
    assert.equal(present(c[0]).day, 1);
    assert.equal(c.length % 7, 0);
  });

  test("a month beginning mid-week gets the right leading nulls", () => {
    // 2024-02-01 was a Thursday → Monday-indexed weekday 3 → 3 pad cells.
    const cal = buildCalendar([item(1, "2024-02-10")], "2024-02-20");
    const c = monthAt(cal, 0).cells;
    assert.equal(c[0], null);
    assert.equal(c[1], null);
    assert.equal(c[2], null);
    assert.equal(present(c[3]).day, 1);
  });

  test("leap February carries 29 day cells", () => {
    const cal = buildCalendar([item(1, "2024-02-10")], "2024-02-20");
    assert.equal(daysOf(cal, 0).length, 29);
  });

  test("non-leap February carries 28 day cells", () => {
    const cal = buildCalendar([item(1, "2023-02-10")], "2023-02-20");
    assert.equal(daysOf(cal, 0).length, 28);
  });
});

describe("buildCalendar — items, today, and the open-on month", () => {
  test("flags today and buckets multiple items onto one day, highest order first", () => {
    const cal = buildCalendar([item(1, "2024-03-15"), item(2, "2024-03-15")], "2024-03-20");
    const day15 = dayNamed(cal, 0, 15);
    assert.equal(day15.items.length, 2);
    assert.deepEqual(day15.items.map(p => p.id), [2, 1]);
    assert.equal(dayNamed(cal, 0, 20).isToday, true);
    assert.equal(day15.isToday, false);
  });

  test("opens on the current month when it has items", () => {
    const cal = buildCalendar([item(1, "2024-03-05"), item(2, "2024-01-05")], "2024-03-20");
    assert.equal(monthAt(cal, cal.initialIndex).key, "2024-03");
  });

  test("opens on the newest month with items when the current month is empty", () => {
    const cal = buildCalendar([item(1, "2024-01-05")], "2024-03-20");
    assert.equal(monthAt(cal, cal.initialIndex).key, "2024-01");
  });

  test("opens safely when today precedes every item (negative current index)", () => {
    // Future-dated items relative to the build clock → the computed current
    // index is negative; the fallback must still land on a real month.
    const cal = buildCalendar([item(1, "2024-05-10")], "2024-03-01");
    assert.equal(monthAt(cal, cal.initialIndex).key, "2024-05");
  });
});

describe("groupByMonthDesc", () => {
  test("orders months newest-first and days newest-first, ties by order desc", () => {
    const groups = groupByMonthDesc([
      item(1, "2024-01-05"),
      item(2, "2024-03-15"),
      item(3, "2024-03-15"),
      item(4, "2024-03-20"),
    ]);
    assert.deepEqual(groups.map(g => g.key), ["2024-03", "2024-01"]);
    assert.deepEqual(present(groups[0]).items.map(i => i.value.id), [4, 3, 2]);
    assert.deepEqual(present(groups[1]).items.map(i => i.value.id), [1]);
  });

  test("orders months across a year boundary", () => {
    const groups = groupByMonthDesc([item(1, "2023-12-20"), item(2, "2024-01-05")]);
    assert.deepEqual(groups.map(g => g.key), ["2024-01", "2023-12"]);
  });
});

describe("todayISO", () => {
  test("returns a zero-padded YYYY-MM-DD string", () => {
    assert.match(todayISO(), /^\d{4}-\d{2}-\d{2}$/);
  });
});
