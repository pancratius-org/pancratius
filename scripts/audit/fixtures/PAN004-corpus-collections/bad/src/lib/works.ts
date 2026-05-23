// Fixture (PAN004-corpus-collections / bad): the corpus builder reads the
// projects collection through an ALIASED import (`getCollection as gc`).
// "projects" is a non-work collection (not a value of COLLECTION_OF), so it leaks
// projects into the work-pair corpus. The rule must resolve the alias and fire —
// a bare-name scan would miss this, so this fixture is the alias-resolution
// regression.
import { getCollection as gc } from "astro:content";

export const COLLECTION_OF = { book: "books", poem: "poetry" } as const;

export function getAllWorkPairs(): unknown[] {
  const books = gc("books");
  const poetry = gc("poetry");
  const projects = gc("projects");
  return [...books, ...poetry, ...projects];
}
