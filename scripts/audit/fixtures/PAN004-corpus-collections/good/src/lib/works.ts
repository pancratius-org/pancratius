// Fixture (PAN004-corpus-collections / good): the work-pair corpus builder reads
// ONLY the work collections (books, poetry). getCollection is stubbed — the rule
// only inspects its string-literal first argument.
declare function getCollection(name: string): unknown[];

export const COLLECTION_OF = { book: "books", poem: "poetry" } as const;

export function getAllWorkPairs(): unknown[] {
  const books = getCollection("books");
  const poetry = getCollection("poetry");
  return [...books, ...poetry];
}
