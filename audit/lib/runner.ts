import type { Finding } from "./finding.ts";
import type { Rule, RuleContext } from "./rule.ts";

export const AUDIT_CONCURRENCY = 4;

export interface RuleRun {
  readonly rule: Rule;
  readonly findings: readonly Finding[];
  readonly durationMs: number;
}

interface ResultSlot<T> {
  readonly value: T;
}

export async function mapBounded<T, U>(
  items: readonly T[],
  concurrency: number,
  visit: (item: T, index: number) => Promise<U>,
): Promise<U[]> {
  if (!Number.isInteger(concurrency) || concurrency < 1) {
    throw new RangeError(`concurrency must be a positive integer, received ${concurrency}`);
  }

  const results: Array<ResultSlot<U> | undefined> = Array.from({ length: items.length });
  let cursor = 0;

  async function worker(): Promise<void> {
    while (cursor < items.length) {
      const index = cursor;
      cursor += 1;
      const item = items[index];
      if (item === undefined) throw new Error(`missing work item at index ${index}`);
      results[index] = { value: await visit(item, index) };
    }
  }

  const workerCount = Math.min(concurrency, items.length);
  await Promise.all(Array.from({ length: workerCount }, worker));
  return results.map((result, index) => {
    if (result === undefined) throw new Error(`missing work result at index ${index}`);
    return result.value;
  });
}

export async function runRules(
  rules: readonly Rule[],
  ctx: RuleContext,
  concurrency = AUDIT_CONCURRENCY,
): Promise<RuleRun[]> {
  return mapBounded(rules, concurrency, async (rule) => {
    const startedAt = performance.now();
    const findings = [...(await rule.run(ctx))];
    return { rule, findings, durationMs: performance.now() - startedAt };
  });
}
