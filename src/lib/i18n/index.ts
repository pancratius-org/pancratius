// Locale config, URL shape, pluralization, and navigation.
//
// Every URL on the site comes through this boundary. Route files compose URLs
// by calling these helpers; they never concatenate locale path strings by hand.

export type { CorpusWorkKind, RoutedKind } from "../kinds";
export type { Locale } from "../locales";
export { DEFAULT_LOCALE, LOCALES } from "../locales";

export * from "./locale-meta";
export * from "./navigation";
export * from "./numbers";
export * from "./plural";
export * from "./routing";
