#!/usr/bin/env node

import { analyzeCssValues, formatCssValueReport, type CssValueOptions } from "./lib/css_values.ts";
import { makeContext } from "./lib/rule.ts";
import { REPO_ROOT } from "./lib/repo.ts";

interface CliOptions extends CssValueOptions {
  help: boolean;
}

function parseArgs(argv: readonly string[]): CliOptions {
  const options: CliOptions = { minCount: 3, limit: 18, examples: 4, help: false };

  for (const arg of argv.slice(2)) {
    if (arg === "--help" || arg === "-h") options.help = true;
    else if (arg.startsWith("--min-count=")) options.minCount = parsePositiveInt(arg, "--min-count=");
    else if (arg.startsWith("--limit=")) options.limit = parsePositiveInt(arg, "--limit=");
    else if (arg.startsWith("--examples=")) options.examples = parsePositiveInt(arg, "--examples=");
    else throw new Error(`unknown argument: ${arg}`);
  }

  return options;
}

function parsePositiveInt(arg: string, prefix: string): number {
  const raw = arg.slice(prefix.length);
  const value = Number.parseInt(raw, 10);
  if (!Number.isSafeInteger(value) || value < 1 || value.toString() !== raw) {
    throw new Error(`${prefix}${raw} must be a positive integer`);
  }
  return value;
}

function usage(): string {
  return [
    "usage: node audit/css-values.ts [--min-count=N] [--limit=N] [--examples=N]",
    "",
    "Diagnostic-only CSS value report. It parses src/**/*.css and Astro <style>",
    "blocks with PostCSS, groups repeated raw design values, and highlights",
    "layout, spacing, typography, and large-pixel literal clusters.",
  ].join("\n");
}

function main(): void {
  const options = parseArgs(process.argv);
  if (options.help) {
    process.stdout.write(`${usage()}\n`);
    return;
  }

  const report = analyzeCssValues(makeContext(REPO_ROOT), options);
  process.stdout.write(`${formatCssValueReport(report, options)}\n`);
}

try {
  main();
} catch (error) {
  process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n\n${usage()}\n`);
  process.exit(2);
}
