/**
 * Russian pluralization.
 *
 * Russian nouns take three forms when counted:
 * - one  (1)     -> 1 книга
 * - few  (2-4)   -> 2 книги
 * - many (5-20)  -> 5 книг
 */
export type RuPluralForms = readonly [one: string, few: string, many: string];

export const RU_PLURALS = {
  book: ["книга", "книги", "книг"] as const,
  bookDative: ["книге", "книгам", "книгам"] as const,
  poem: ["стихотворение", "стихотворения", "стихотворений"] as const,
  message: ["послание", "послания", "посланий"] as const,
  psalm: ["псалом", "псалма", "псалмов"] as const,
  project: ["проект", "проекта", "проектов"] as const,
  direction: ["направление", "направления", "направлений"] as const,
  door: ["дверь", "двери", "дверей"] as const,
} as const;

/** Pick the right form of a Russian noun for the given count. */
export function plRu(n: number, forms: RuPluralForms): string {
  const abs = Math.abs(n);
  const mod10 = abs % 10;
  const mod100 = abs % 100;
  if (mod100 >= 11 && mod100 <= 14) return forms[2];
  if (mod10 === 1) return forms[0];
  if (mod10 >= 2 && mod10 <= 4) return forms[1];
  return forms[2];
}
