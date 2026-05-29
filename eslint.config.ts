// @ts-check

import js from "@eslint/js";
import { defineConfig } from "eslint/config";
import astro from "eslint-plugin-astro";
import tseslint from "typescript-eslint";

const TS_FILES = ["**/*.{ts,tsx}"];
const TS_PROJECTS = ["./tsconfig.json", "./tsconfig.tooling.json", "./tests/tsconfig.json"];

function tsFilesOnly<T extends object>(config: T): T & { files: string[] } {
  return { ...config, files: TS_FILES };
}

export default defineConfig(
  {
    ignores: [
      ".astro/**",
      "audit/fixtures/**",
      "dist/**",
      "node_modules/**",
      "public/data/**",
      "public/pagefind/**",
    ],
  },

  { ...js.configs.recommended, files: ["**/*.{js,mjs,cjs}"] },
  ...astro.configs["flat/recommended"],
  ...tseslint.configs.recommendedTypeChecked.map(tsFilesOnly),

  {
    files: TS_FILES,
    languageOptions: {
      parserOptions: {
        project: TS_PROJECTS,
        tsconfigRootDir: import.meta.dirname,
      },
    },
    rules: {
      "@typescript-eslint/ban-ts-comment": [
        "error",
        {
          "ts-expect-error": "allow-with-description",
          "ts-ignore": "allow-with-description",
          minimumDescriptionLength: 12,
        },
      ],
      "@typescript-eslint/consistent-type-exports": "error",
      "@typescript-eslint/consistent-type-imports": [
        "error",
        {
          prefer: "type-imports",
          fixStyle: "separate-type-imports",
        },
      ],
      "@typescript-eslint/no-explicit-any": "error",
      "@typescript-eslint/no-floating-promises": [
        "error",
        {
          allowForKnownSafeCalls: [
            { from: "package", package: "node:test", name: ["describe", "test"] },
          ],
        },
      ],
      "@typescript-eslint/no-misused-promises": "error",
      "@typescript-eslint/no-deprecated": "error",
      "@typescript-eslint/no-non-null-assertion": "error",
      "@typescript-eslint/no-unnecessary-condition": "error",
      "@typescript-eslint/no-unnecessary-type-conversion": "error",
      "@typescript-eslint/no-base-to-string": "error",
      "@typescript-eslint/no-import-type-side-effects": "error",
      "@typescript-eslint/no-require-imports": "error",
      "@typescript-eslint/no-inferrable-types": "error",
      "@typescript-eslint/prefer-nullish-coalescing": "error",
      "@typescript-eslint/prefer-optional-chain": "error",
      "@typescript-eslint/prefer-readonly": "error",
      "@typescript-eslint/restrict-template-expressions": "error",
      "@typescript-eslint/switch-exhaustiveness-check": "error",
      "@typescript-eslint/no-unnecessary-type-assertion": "error",
      "@typescript-eslint/no-unsafe-argument": "error",
      "@typescript-eslint/no-unsafe-assignment": "error",
      "@typescript-eslint/no-unsafe-call": "error",
      "@typescript-eslint/no-unsafe-member-access": "error",
      "@typescript-eslint/no-unsafe-return": "error",
      "@typescript-eslint/no-unused-vars": [
        "error",
        {
          argsIgnorePattern: "^_",
          caughtErrorsIgnorePattern: "^_",
          destructuredArrayIgnorePattern: "^_",
          varsIgnorePattern: "^_",
        },
      ],
      "@typescript-eslint/no-useless-constructor": "error",
      "complexity": ["error", 10],
      "eqeqeq": ["error", "smart"],
      "max-classes-per-file": ["error", 1],
      "max-depth": ["error", 3],
      "max-lines-per-function": ["error", { max: 125, skipBlankLines: true, skipComments: true }],
      "max-statements": ["error", 20],
      "no-param-reassign": "error",
      "prefer-const": "error",
    },
  },

  {
    files: ["src/**/*.{ts,tsx,astro}"],
    rules: {
      "no-console": ["error", { allow: ["warn", "error"] }],
    },
  },

  {
    files: ["**/*.{js,mjs,cjs}"],
    extends: [tseslint.configs.disableTypeChecked],
  },
);
