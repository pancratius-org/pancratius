// Fixture (PAN004-bulk-archive-kinds / bad): KIND_DIRS has a `project` key, which
// is not a work kind — projects would ship in all-md.zip. The rule fires.
const KIND_DIRS = {
  book: "books",
  poem: "poetry",
  project: "projects",
};

export type ArchiveKind = keyof typeof KIND_DIRS;
