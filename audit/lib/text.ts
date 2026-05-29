// Small text helpers for rules that scan source as strings: turning a character
// offset into a 1-based line number, and pulling the matching line for evidence.
// Nothing clever — just so every rule reports `file:line` the same way.

/** 1-based line number of a character offset within `text`. */
function lineAt(text: string, index: number): number {
  let line = 1;
  for (let i = 0; i < index && i < text.length; i++) {
    if (text.charCodeAt(i) === 10 /* \n */) line += 1;
  }
  return line;
}

/** The full source line containing character offset `index`, trimmed of EOL. */
export function lineTextAt(text: string, index: number): string {
  const start = text.lastIndexOf("\n", index - 1) + 1;
  let end = text.indexOf("\n", index);
  if (end === -1) end = text.length;
  return text.slice(start, end).replace(/\r$/, "");
}

/** Collapse whitespace and cap length, for putting source into an `observed`. */
export function snippet(s: string, max = 120): string {
  const one = s.replace(/\s+/g, " ").trim();
  return one.length > max ? `${one.slice(0, max - 1)}…` : one;
}

/** Yield every regex match with its 1-based line number. `re` must be global. */
export function* matchesWithLines(
  text: string,
  re: RegExp,
): Generator<{ match: RegExpExecArray; line: number }> {
  if (!re.global) throw new Error("matchesWithLines requires a global regex");
  let m: RegExpExecArray | null;
  re.lastIndex = 0;
  while ((m = re.exec(text)) !== null) {
    yield { match: m, line: lineAt(text, m.index) };
    if (m.index === re.lastIndex) re.lastIndex += 1; // guard zero-width matches
  }
}
