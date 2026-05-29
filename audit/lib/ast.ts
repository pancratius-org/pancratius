// Shared TypeScript-AST infrastructure for rules whose false-positive cost is
// high enough to warrant a parser instead of regex (docs/audit-harness.md →
// "Surface-Specific Implementation Guidance → TypeScript and Astro"). Rules feed
// it source they've already read through the RuleContext; it never touches `fs`.
//
// The one wrinkle is `.astro`: only the leading frontmatter fence is TypeScript,
// so we extract that block and parse it — but we pad it with blank lines so the
// parser's reported line numbers still line up with the original .astro file.

import ts from "typescript";

/**
 * Parse a module's source to a `ts.SourceFile`, or null when there's nothing to
 * parse. `.ts`/`.mts`/`.mjs` are parsed directly. `.astro` files are parsed from
 * their leading frontmatter fence only (the block between the opening `---` line
 * and the matching closing `---`); blank lines are prepended so a position from
 * the parsed slice maps to the SAME 1-based line in the original .astro file.
 * Returns null for an `.astro` file with no frontmatter fence.
 */
export function parseModule(relPath: string, source: string): ts.SourceFile | null {
  let toParse = source;

  if (relPath.endsWith(".astro")) {
    const fence = extractAstroFrontmatter(source);
    if (fence === null) return null;
    toParse = fence;
  }

  return ts.createSourceFile(relPath, toParse, ts.ScriptTarget.Latest, /*setParentNodes*/ true);
}

/**
 * Pull the leading frontmatter fence out of `.astro` source, returned as TS with
 * the SAME line geometry as the original file. The fence opens with `---` on
 * line 1 and closes with the next line that is exactly `---`; the code between
 * them therefore starts on line 2. We replace the opening `---` line with a
 * blank line and drop everything from the closing fence onward, so each line of
 * returned code keeps its original line number. Returns null if there's no fence.
 *
 * Known rare limitation: a line equal to `---` *inside a template literal* in the
 * frontmatter would close the fence early (we match the closing fence by line
 * text, not by lexing the TS). Frontmatter almost never contains such a literal,
 * so we accept this over a full tokenizer.
 */
function extractAstroFrontmatter(source: string): string | null {
  const lines = source.split("\n");
  if (lines[0]?.trimEnd() !== "---") return null;

  let close = -1;
  for (const [i, line] of lines.entries()) {
    if (i === 0) continue;
    if (line.trimEnd() === "---") {
      close = i;
      break;
    }
  }
  if (close === -1) return null;

  // Blank out the opening fence line (line 1) so line numbers are preserved,
  // keep the frontmatter body lines as-is, and stop before the closing fence.
  const kept = ["", ...lines.slice(1, close)];
  return kept.join("\n");
}

/**
 * Names of functions a module EXPORTS — both `export function foo` and
 * `export const foo = (…) => …` / `export const foo = function (…) {…}`. Used to
 * tie a rule to a source-of-truth symbol so it fails loud if that symbol is
 * renamed, rather than silently passing.
 *
 * Scope: NAMED exported functions only. `export default function foo` is not
 * covered (it has no stable named binding to match against) — fine for the
 * source-of-truth selectors this guards, which are always named exports.
 */
export function findExportedFunctionNames(sf: ts.SourceFile): Set<string> {
  const names = new Set<string>();

  for (const stmt of sf.statements) {
    for (const name of exportedFunctionNamesInStatement(stmt)) names.add(name);
  }

  return names;
}

function exportedFunctionNamesInStatement(stmt: ts.Statement): string[] {
  if (!hasExportModifier(stmt)) return [];
  if (ts.isFunctionDeclaration(stmt)) return stmt.name ? [stmt.name.text] : [];
  if (!ts.isVariableStatement(stmt)) return [];

  return stmt.declarationList.declarations
    .map(exportedFunctionVariableName)
    .filter((name) => name !== null);
}

function exportedFunctionVariableName(decl: ts.VariableDeclaration): string | null {
  if (!ts.isIdentifier(decl.name) || !decl.initializer) return null;
  return isFunctionValue(decl.initializer) ? decl.name.text : null;
}

function isFunctionValue(node: ts.Node): boolean {
  return ts.isArrowFunction(node) || ts.isFunctionExpression(node);
}

function hasExportModifier(node: ts.Node): boolean {
  if (!ts.canHaveModifiers(node)) return false;
  return (ts.getModifiers(node) ?? []).some((m) => m.kind === ts.SyntaxKind.ExportKeyword);
}

/**
 * The node whose body is the `getStaticPaths` route logic, or null if the module
 * has no top-level `getStaticPaths` export. Handles both `export const
 * getStaticPaths = …` (unwrapping `satisfies`/`as`/parentheses to reach the
 * arrow or function expression) and `export function getStaticPaths`. The
 * returned node is what a route-shape rule scans for offending calls.
 */
export function getStaticPathsInitializer(sf: ts.SourceFile): ts.Node | null {
  for (const stmt of sf.statements) {
    const initializer = staticPathsInitializerInStatement(stmt);
    if (initializer) return initializer;
  }
  return null;
}

function staticPathsInitializerInStatement(stmt: ts.Statement): ts.Node | null {
  if (!hasExportModifier(stmt)) return null;
  if (isNamedFunctionDeclaration(stmt, "getStaticPaths")) return stmt.body ?? null;
  if (!ts.isVariableStatement(stmt)) return null;

  for (const decl of stmt.declarationList.declarations) {
    const initializer = staticPathsInitializerInDeclaration(decl);
    if (initializer) return initializer;
  }
  return null;
}

function isNamedFunctionDeclaration(stmt: ts.Statement, name: string): stmt is ts.FunctionDeclaration {
  return ts.isFunctionDeclaration(stmt) && stmt.name?.text === name;
}

function staticPathsInitializerInDeclaration(decl: ts.VariableDeclaration): ts.Expression | null {
  if (!ts.isIdentifier(decl.name) || decl.name.text !== "getStaticPaths" || !decl.initializer) {
    return null;
  }
  return unwrap(decl.initializer);
}

/** Peel `satisfies`/`as` assertions and parentheses to the underlying expression. */
function unwrap(node: ts.Expression): ts.Expression {
  let current = node;
  while (
    ts.isSatisfiesExpression(current) ||
    ts.isAsExpression(current) ||
    ts.isParenthesizedExpression(current)
  ) {
    current = current.expression;
  }
  return current;
}

/**
 * Every `CallExpression` within `node` whose callee is a BARE identifier
 * matching any of `calleeNames`. Lets a rule treat a set of local aliases (e.g.
 * `import { displayWorkEntry as dwe }`) as the same selector. Member-access
 * callees like `ns.foo(…)` are still NOT matched — a namespace import is a
 * known gap.
 */
export function findIdentifierCallsAny(node: ts.Node, calleeNames: ReadonlySet<string>): ts.Node[] {
  const calls: ts.Node[] = [];

  const visit = (n: ts.Node): void => {
    if (ts.isCallExpression(n) && ts.isIdentifier(n.expression) && calleeNames.has(n.expression.text)) {
      calls.push(n);
    }
    ts.forEachChild(n, visit);
  };

  visit(node);
  return calls;
}

/**
 * Local names bound by a named import whose ORIGINAL (imported) name is
 * `importedName`, across all `import` declarations in `sf` — regardless of which
 * module it comes from (the imported name is assumed distinctive enough). Both
 * `import { foo }` (local === imported) and `import { foo as bar }` (local is the
 * alias) are returned. Lets a rule recognize bare-identifier selector calls even
 * when the selector was imported under an alias. Namespace imports (`import * as
 * ns`) are NOT covered: `ns.foo(…)` is a member-access callee and remains a
 * known gap.
 */
export function findLocalNamesForImport(sf: ts.SourceFile, importedName: string): Set<string> {
  const locals = new Set<string>();

  for (const stmt of sf.statements) {
    if (!ts.isImportDeclaration(stmt)) continue;
    const named = stmt.importClause?.namedBindings;
    if (!named || !ts.isNamedImports(named)) continue;
    for (const el of named.elements) {
      // `el.propertyName` is the original name when aliased (`{ foo as bar }`);
      // otherwise `el.name` is both the imported and the local name.
      const imported = el.propertyName?.text ?? el.name.text;
      if (imported === importedName) locals.add(el.name.text);
    }
  }

  return locals;
}

/**
 * Initializer nodes of every `PropertyAssignment` named `name` within `node`
 * (e.g. all `params:` value subtrees). Only `PropertyAssignment` (`name: value`)
 * is handled; shorthand (`{ params }`) and spreads are skipped — document that
 * here so a caller knows shorthand won't be reported.
 */
export function findPropertyValues(node: ts.Node, name: string): ts.Node[] {
  const values: ts.Node[] = [];

  const visit = (n: ts.Node): void => {
    if (ts.isPropertyAssignment(n) && getPropertyName(n.name) === name) {
      values.push(n.initializer);
    }
    ts.forEachChild(n, visit);
  };

  visit(node);
  return values;
}

/** Text of a property name for the forms we care about; null for computed names. */
function getPropertyName(name: ts.PropertyName): string | null {
  if (ts.isIdentifier(name) || ts.isStringLiteral(name) || ts.isNumericLiteral(name)) {
    return name.text;
  }
  return null;
}

/**
 * All bare `Identifier` nodes within `node` whose text is `name`. Declaration-
 * position occurrences (the `X` in `const X = …`, and a binding-name identifier)
 * are EXCLUDED, so the result is references/uses of `name`, not its declaration.
 * Property names in `{ name: … }` and `obj.name` member accesses are not bare
 * identifier references and are likewise not returned.
 */
export function findIdentifierRefs(node: ts.Node, name: string): ts.Node[] {
  const refs: ts.Node[] = [];

  const visit = (n: ts.Node): void => {
    if (ts.isIdentifier(n) && n.text === name && !isDeclarationOrNonRefName(n)) {
      refs.push(n);
    }
    ts.forEachChild(n, visit);
  };

  visit(node);
  return refs;
}

/**
 * True when an identifier sits in a position that is NOT a value-reference: a
 * binding name (`const X`, params, etc.), the key of a property assignment, the
 * `.X` of a member access, or the imported/local name of an import specifier.
 */
function isDeclarationOrNonRefName(id: ts.Identifier): boolean {
  const p = id.parent;
  return NON_REF_IDENTIFIER_POSITIONS.some((isNonRef) => isNonRef(p, id));
}

const NON_REF_IDENTIFIER_POSITIONS: readonly ((parent: ts.Node, id: ts.Identifier) => boolean)[] = [
  (parent, id) => ts.isVariableDeclaration(parent) && parent.name === id,
  (parent, id) => ts.isBindingElement(parent) && parent.name === id,
  (parent, id) => ts.isParameter(parent) && parent.name === id,
  (parent, id) => ts.isPropertyAssignment(parent) && parent.name === id,
  (parent, id) => ts.isPropertyAccessExpression(parent) && parent.name === id,
  (parent) => ts.isImportSpecifier(parent),
];

/**
 * For each `CallExpression` within `node` whose callee is a BARE identifier with
 * exactly `calleeName`, the first argument WHEN it is a string literal — as the
 * literal's `value` plus the enclosing call `node` (so a caller can report the
 * line of the call, not the argument). Calls whose first argument is not a string
 * literal (a variable, an expression, missing) are skipped. Member-access callees
 * (`ns.getCollection(…)`) are not matched, mirroring `findIdentifierCalls`.
 */
export function findCallStringArgs(
  node: ts.Node,
  calleeName: string,
): { value: string; node: ts.Node }[] {
  const out: { value: string; node: ts.Node }[] = [];

  const visit = (n: ts.Node): void => {
    if (
      ts.isCallExpression(n) &&
      ts.isIdentifier(n.expression) &&
      n.expression.text === calleeName
    ) {
      const first = n.arguments[0];
      if (first && ts.isStringLiteralLike(first)) {
        out.push({ value: first.text, node: n });
      }
    }
    ts.forEachChild(n, visit);
  };

  visit(node);
  return out;
}

/**
 * The object-literal initializer of a top-level `export? const constName = { … }`,
 * with any `satisfies`/`as`/parenthesis wrapper peeled off, or null when there is
 * no such const or its initializer isn't an object literal. The building block
 * for `objectLiteralKeysOf` / `objectLiteralStringValuesOf`.
 */
function constObjectLiteral(
  sf: ts.SourceFile,
  constName: string,
): ts.ObjectLiteralExpression | null {
  const init = constInitializer(sf, constName);
  return init && ts.isObjectLiteralExpression(init) ? init : null;
}

function constArrayLiteral(
  sf: ts.SourceFile,
  constName: string,
): ts.ArrayLiteralExpression | null {
  const init = constInitializer(sf, constName);
  return init && ts.isArrayLiteralExpression(init) ? init : null;
}

function constInitializer(sf: ts.SourceFile, constName: string): ts.Expression | null {
  for (const stmt of sf.statements) {
    if (!ts.isVariableStatement(stmt)) continue;
    for (const decl of stmt.declarationList.declarations) {
      if (isConstDeclarationNamed(decl, constName)) return unwrap(decl.initializer);
    }
  }
  return null;
}

function isConstDeclarationNamed(
  decl: ts.VariableDeclaration,
  constName: string,
): decl is ts.VariableDeclaration & { initializer: ts.Expression } {
  return ts.isIdentifier(decl.name) && decl.name.text === constName && decl.initializer !== undefined;
}

/**
 * Property-name keys of the object literal assigned to `export? const constName`,
 * unwrapping `satisfies`/`as`. Covers `name: …` (PropertyAssignment) and
 * `{ name }` shorthand; computed keys and spreads are skipped. Returns [] when
 * the const or its object literal isn't found.
 */
export function objectLiteralKeysOf(sf: ts.SourceFile, constName: string): string[] {
  const obj = constObjectLiteral(sf, constName);
  if (!obj) return [];
  const keys: string[] = [];
  for (const prop of obj.properties) {
    if (ts.isPropertyAssignment(prop)) {
      const key = getPropertyName(prop.name);
      if (key !== null) keys.push(key);
    } else if (ts.isShorthandPropertyAssignment(prop)) {
      keys.push(prop.name.text);
    }
  }
  return keys;
}

/**
 * String-literal VALUES of the object literal assigned to `export? const
 * constName`, unwrapping `satisfies`/`as`. Only `name: "literal"` assignments
 * contribute; non-string-literal values, shorthand, and spreads are skipped.
 * Returns [] when the const or its object literal isn't found.
 */
export function objectLiteralStringValuesOf(sf: ts.SourceFile, constName: string): string[] {
  const obj = constObjectLiteral(sf, constName);
  if (!obj) return [];
  const values: string[] = [];
  for (const prop of obj.properties) {
    if (ts.isPropertyAssignment(prop) && ts.isStringLiteralLike(prop.initializer)) {
      values.push(prop.initializer.text);
    }
  }
  return values;
}

export function arrayLiteralStringValuesOf(sf: ts.SourceFile, constName: string): string[] {
  const arr = constArrayLiteral(sf, constName);
  if (!arr) return [];
  const values: string[] = [];
  for (const element of arr.elements) {
    const unwrapped = unwrap(element);
    if (ts.isStringLiteralLike(unwrapped)) values.push(unwrapped.text);
  }
  return values;
}

/**
 * Position-range containment: does `ancestor` lexically enclose `descendant`?
 * Uses source offsets, so it works across helper boundaries without walking
 * parent links. (A node trivially contains itself.)
 */
export function nodeContains(ancestor: ts.Node, descendant: ts.Node): boolean {
  return descendant.getStart() >= ancestor.getStart() && descendant.getEnd() <= ancestor.getEnd();
}

/**
 * Argument subtrees of every CallExpression within `node` whose callee is a
 * property access ending in `.<method>` (e.g. every `.filter(…)` argument).
 * Returns the argument expression nodes themselves (a predicate function, a
 * value, …) so a caller can test containment against them.
 */
export function findMethodCallArguments(node: ts.Node, method: string): ts.Node[] {
  const args: ts.Node[] = [];

  const visit = (n: ts.Node): void => {
    if (
      ts.isCallExpression(n) &&
      ts.isPropertyAccessExpression(n.expression) &&
      n.expression.name.text === method
    ) {
      for (const a of n.arguments) args.push(a);
    }
    ts.forEachChild(n, visit);
  };

  visit(node);
  return args;
}

/**
 * How many times `name` is introduced as a binding NAME within `scope` —
 * counting `const/let/var` declarators, function/arrow parameters, destructuring
 * binding elements, and function/class declaration names. A caller that follows a
 * binding by NAME (e.g. "does `X` flow into params?") uses this to refuse when the
 * name is declared more than once: a name-based reference scan can't tell a
 * re-declared/shadowed binding from the one it meant, so >1 means "ambiguous —
 * don't attribute," which keeps a name-based data-flow check free of shadowing
 * false positives at the cost of a (documented) miss.
 */
export function nameDeclarationCount(scope: ts.Node, name: string): number {
  let count = 0;
  const visit = (n: ts.Node): void => {
    if (ts.isIdentifier(n) && n.text === name && isBindingDeclarationName(n)) count += 1;
    ts.forEachChild(n, visit);
  };
  visit(scope);
  return count;
}

/** True when an identifier is the NAME introduced by a binding (not a reference). */
function isBindingDeclarationName(id: ts.Identifier): boolean {
  const p = id.parent;
  return (
    (ts.isVariableDeclaration(p) && p.name === id) ||
    (ts.isParameter(p) && p.name === id) ||
    (ts.isBindingElement(p) && p.name === id) ||
    (ts.isFunctionDeclaration(p) && p.name === id) ||
    (ts.isClassDeclaration(p) && p.name === id)
  );
}

/**
 * The name of the nearest `const`/`let`/`var` binding whose initializer subtree
 * contains `call`, walking up parent links and stopping at the first function
 * boundary (we only care about same-scope `const X = displayWorkEntry(…)` capture).
 * Returns null when the call isn't part of a simple identifier-named binding
 * initializer (e.g. it's an inline argument, or the binding name is destructured).
 */
export function enclosingBindingName(call: ts.Node): string | null {
  let n: ts.Node = call.parent;
  while (!ts.isSourceFile(n)) {
    // Don't escape the function the call lives in.
    if (
      ts.isFunctionDeclaration(n) ||
      ts.isFunctionExpression(n) ||
      ts.isArrowFunction(n) ||
      ts.isMethodDeclaration(n)
    ) {
      return null;
    }
    if (
      ts.isVariableDeclaration(n) &&
      ts.isIdentifier(n.name) &&
      n.initializer &&
      nodeContains(n.initializer, call)
    ) {
      return n.name.text;
    }
    n = n.parent;
  }
  return null;
}
