// The reading-flow neighbours of an item within a population ordered by
// `.pair.number` — the prev/next a detail-page pager walks. One implementation
// for books, videos, and messages (which flips prev/next to read chronological).

export interface HasPairNumber {
  pair: { number: number };
}

export function neighbors<T extends HasPairNumber>(
  pairs: readonly T[],
  number: number,
): { prev: T | null; next: T | null } {
  const here = pairs.findIndex(p => p.pair.number === number);
  if (here < 0) return { prev: null, next: null };
  // Out-of-range indices read as undefined (noUncheckedIndexedAccess) → null.
  return {
    prev: pairs[here - 1] ?? null,
    next: pairs[here + 1] ?? null,
  };
}
