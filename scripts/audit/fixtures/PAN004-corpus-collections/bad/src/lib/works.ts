// Fixture (PAN004-corpus-collections / bad): the corpus builder also reads
// getCollection("projects") — "projects" is a non-work collection (not a value of
// COLLECTION_OF), so it leaks projects into the work-pair corpus. The rule fires.
declare function getCollection(name: string): unknown[];

export const COLLECTION_OF = { book: "books", poem: "poetry" } as const;

export function getAllWorkPairs(): unknown[] {
  const books = getCollection("books");
  const poetry = getCollection("poetry");
  const projects = getCollection("projects");
  return [...books, ...poetry, ...projects];
}
