// Reader-facing strings for the conceptosphere surface.
//
// Same shape for every locale, same pattern as Header/Footer/SimilarPair:
// the locale picks the dictionary at the route boundary, then the strings
// are passed down as props (server) and into the `#cs-config` JSON island
// (client). No locale lookup at runtime; the runtime is a pure consumer.
//
// Graph data (node labels, book titles, community labels) remains Russian
// because the corpus is Russian. Only chrome localises.

import type { Locale } from "@/lib/i18n";

/**
 * Every reader-facing string the conceptosphere surface needs.
 *
 * Two-tier organisation:
 *   * `modes.{concepts,books}` — copy that changes between graph modes
 *     (h1, lede, methodology, search placeholder, counts noun).
 *   * top-level keys — everything else, including ARIA labels, headings,
 *     and the small pieces of UI prose the runtime stitches into panels.
 */
export interface ConceptosphereStrings {
  /** `Intl.NumberFormat` locale tag — used by the runtime for `toLocaleString`. */
  numberLocale: string;

  /** Per-mode chrome copy. */
  modes: Record<"concepts" | "books", {
    /** Page H1 + headline. */
    h1:   string;
    /** Italic lede under the H1. */
    lede: string;
    /** Methodology footer paragraph. */
    meth: string;
    /** Mode toggle button label. */
    toggleLabel: string;
    /** Search input placeholder. */
    searchPlaceholder: string;
    /** Singular noun used in the counts line: `${nNodes} ${countsNoun} · …`. */
    countsNoun: string;
  }>;

  /** Counts line — composed at runtime as `${n} {countsNoun} · ${e} {edgesNoun} · ${c} {communitiesNoun}`. */
  edgesNoun:       string;
  communitiesNoun: string;
  /** Compact disclosure for the methodology text. */
  methodSummary:   string;

  /** Stage / canvas landmark labels. */
  stageAriaLabel: string;
  graphAriaLabel: string;
  /** Inside <noscript>: paragraph shown when JS is disabled. */
  noscriptText:   string;
  /** Visually-hidden search label. */
  searchSrLabel:  string;
  /** Mode-toggle group ARIA label (role="tablist"). */
  modeToggleAriaLabel: string;

  /** Cluster legend. */
  legendTitle:         string;
  legendClearLabel:    string;

  /** Side panel. */
  sidePanelAriaLabel:  string;
  sidePanelCloseLabel: string;
  /** Pseudo-cluster name when a community label is missing (`Кластер 3`). Single `{n}` placeholder. */
  clusterFallbackLabel: string;
  /** Side panel: stat row labels. */
  statFrequency:    string;
  statCentrality:   string;
  statConnections:  string;
  /** Side panel: concept-mode books heading + suffix on per-book count. */
  conceptTopBooksHeading: string;
  /** Suffix appended to a book count, e.g. "12 mentions". Singular form is fine: it follows the numeric value. */
  mentionsSuffix:   string;
  /** Side panel: books-mode top concepts heading. */
  bookTopConceptsHeading: string;
  /** Side panel: similar-by-tfidf heading. */
  similarByConceptsHeading: string;
  /** Side panel: similar-by-embedding heading. */
  similarByMeaningHeading: string;
  /** Foot of the similar lists, explaining the ★ marker. */
  convergenceFoot:  string;
  /** ARIA + title text for the ★ marker. */
  convergenceLabel: string;
  /** Wraps a cover-image link in books-mode hero. */
  openBookLabel:    string;
  /** Similarity row caption: "сходство 84%". `{pct}` placeholder. */
  similarityCaption: string;
  /** Kind prefix for non-book similar rows ("стихотворение · сходство 84%"). */
  kindLabels: Record<"book" | "poem" | "project", string>;

  /** Mobile fallback. */
  mobileListAriaLabel:   string;
  mobileNote:            string;
  mobileFootPrompt:      string;
  mobileFootLink:        string;
  mobileChipsConceptsAria: string;
  mobileChipsBooksAria:    string;
  mobileChipAll:           string;
  mobileEmpty:             string;
  mobileGroupSizeAriaPrefix: string;
  /** Concept fallback when no top_books exist. */
  conceptNotBoundToBook:   string;
  /** Book-row number prefix: "№ 7". */
  bookNumberPrefix:        string;
  /** Concept-row appearances summary heading (mobile). */
  mobileAppearsInHeading:  string;
  /** Error surface shown when the graph payload fetch fails. */
  loadErrorPrefix:         string;
}

const RU: ConceptosphereStrings = {
  numberLocale: "ru-RU",

  modes: {
    concepts: {
      h1:   "Концептосфера",
      lede: "Карта понятий Панкратиуса. Точка — концепт; связь — совместное появление.",
      meth: "Граф построен по совместной встречаемости лемм в окне 4 слов; рёбра отфильтрованы по NPMI (≥0.18) и минимальному весу. Кластеры — Leiden (модулярность); раскладка — ForceAtlas2 с пост-обработкой против перекрытия.",
      toggleLabel:       "Концепты",
      searchPlaceholder: "Найти концепт",
      countsNoun:        "узлов",
    },
    books: {
      h1:   "Книгосфера",
      lede: "Карта книг Панкратиуса. Точка — книга; связь — сходство по ключевым понятиям.",
      meth: "Книги связаны TF-IDF косинусом по векторам концептов (универсальные термины исключены); рёбра прорежены до 5 ближайших соседей с двух сторон. Кластеры — Leiden (модулярность); раскладка — ForceAtlas2.",
      toggleLabel:       "Книги",
      searchPlaceholder: "Найти книгу или концепт",
      countsNoun:        "книг",
    },
  },

  edgesNoun:       "связей",
  communitiesNoun: "кластеров",
  methodSummary:   "Как это устроено?",

  stageAriaLabel: "Граф концептосферы",
  graphAriaLabel: "Концептосфера — интерактивный граф",
  noscriptText:   "Для интерактивного графа нужен включённый JavaScript. Полный список концептов и книг доступен ниже без графа.",
  searchSrLabel:  "Найти",
  modeToggleAriaLabel: "Вид графа",

  legendTitle:      "Кластеры",
  legendClearLabel: "Сбросить фильтр",

  sidePanelAriaLabel:  "Сведения об узле",
  sidePanelCloseLabel: "Закрыть",
  clusterFallbackLabel: "Кластер {n}",
  statFrequency:    "Частота",
  statCentrality:   "Центральность",
  statConnections:  "Связей",
  conceptTopBooksHeading: "Чаще всего встречается в",
  mentionsSuffix:         "упоминаний",
  bookTopConceptsHeading: "Главные концепты",
  similarByConceptsHeading: "Похожие · по концептам",
  similarByMeaningHeading:  "Похожие · по смыслу",
  convergenceFoot:  "— в обоих списках",
  convergenceLabel: "в обоих списках",
  openBookLabel:    "Открыть книгу",
  similarityCaption: "сходство {pct}%",
  kindLabels: {
    book:    "книга",
    poem:    "стихотворение",
    project: "проект",
  },

  mobileListAriaLabel:     "Концептосфера — список",
  mobileNote:              "Граф доступен на большом экране; здесь те же данные в виде списка.",
  mobileFootPrompt:        "Открыть полный список → ",
  mobileFootLink:          "Книги",
  mobileChipsConceptsAria: "Кластеры — концепты",
  mobileChipsBooksAria:    "Кластеры — книги",
  mobileChipAll:           "Все",
  mobileEmpty:             "Ничего не найдено.",
  mobileGroupSizeAriaPrefix: "узлов в кластере",
  conceptNotBoundToBook:   "Концепт не закреплён за отдельной книгой.",
  bookNumberPrefix:        "№",
  mobileAppearsInHeading:  "Чаще всего встречается в",
  loadErrorPrefix:         "Не удалось загрузить граф",
};

const EN: ConceptosphereStrings = {
  numberLocale: "en-US",

  modes: {
    concepts: {
      h1:   "Concept map",
      lede: "A map of Pancratius's concepts. A dot is a concept; a connection is co-occurrence.",
      meth: "The graph is built from lemma co-occurrence within a 4-word window; edges are filtered by NPMI (≥0.18) and minimum weight. Clusters are Leiden communities (modularity); the layout is ForceAtlas2 with overlap post-processing. Node labels remain in the original Russian — the underlying corpus is Russian.",
      toggleLabel:       "Concepts",
      searchPlaceholder: "Find a concept",
      countsNoun:        "nodes",
    },
    books: {
      h1:   "Book sphere",
      lede: "A map of Pancratius's books. A dot is a book; a connection is similarity by key concepts.",
      meth: "Books are linked by TF-IDF cosine over concept vectors (universal terms excluded); edges are pruned to the 5 nearest neighbours on either side. Clusters are Leiden communities (modularity); the layout is ForceAtlas2. Book titles remain in the original Russian.",
      toggleLabel:       "Books",
      searchPlaceholder: "Find a book or concept",
      countsNoun:        "books",
    },
  },

  edgesNoun:       "edges",
  communitiesNoun: "clusters",
  methodSummary:   "How it works",

  stageAriaLabel: "Conceptosphere graph",
  graphAriaLabel: "Conceptosphere — interactive graph",
  noscriptText:   "An interactive graph needs JavaScript enabled. The full list of concepts and books is available below without the graph.",
  searchSrLabel:  "Find",
  modeToggleAriaLabel: "Graph view",

  legendTitle:      "Clusters",
  legendClearLabel: "Clear filter",

  sidePanelAriaLabel:  "Node details",
  sidePanelCloseLabel: "Close",
  clusterFallbackLabel: "Cluster {n}",
  statFrequency:    "Frequency",
  statCentrality:   "Centrality",
  statConnections:  "Connections",
  conceptTopBooksHeading: "Most often appears in",
  mentionsSuffix:         "mentions",
  bookTopConceptsHeading: "Top concepts",
  similarByConceptsHeading: "Similar · by concepts",
  similarByMeaningHeading:  "Similar · by meaning",
  convergenceFoot:  "— present in both lists",
  convergenceLabel: "present in both lists",
  openBookLabel:    "Open the book",
  similarityCaption: "{pct}% similar",
  kindLabels: {
    book:    "book",
    poem:    "poem",
    project: "project",
  },

  mobileListAriaLabel:     "Conceptosphere — list",
  mobileNote:              "The graph is available on a larger screen; here the same data appears as a list.",
  mobileFootPrompt:        "Open the full list → ",
  mobileFootLink:          "Books",
  mobileChipsConceptsAria: "Clusters — concepts",
  mobileChipsBooksAria:    "Clusters — books",
  mobileChipAll:           "All",
  mobileEmpty:             "Nothing found.",
  mobileGroupSizeAriaPrefix: "nodes in cluster",
  conceptNotBoundToBook:   "This concept isn't tied to a specific book.",
  bookNumberPrefix:        "No.",
  mobileAppearsInHeading:  "Most often appears in",
  loadErrorPrefix:         "Failed to load the graph",
};

export const STRINGS: Record<Locale, ConceptosphereStrings> = {
  ru: RU,
  en: EN,
};

export function conceptosphereStrings(locale: Locale): ConceptosphereStrings {
  return STRINGS[locale];
}
