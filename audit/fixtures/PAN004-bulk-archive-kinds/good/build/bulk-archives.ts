// Fixture (PAN004-bulk-archive-kinds / good): KIND_DIRS keys are book/poem only —
// a subset of the work kinds, so all-md.zip ships works only. The rule is silent.
const KIND_DIRS = {
  book: "books",
  poem: "poetry",
};

export type ArchiveKind = keyof typeof KIND_DIRS;
