#!/usr/bin/env python3
# Heavy graph stack (networkx/igraph/leidenalg/pymorphy3/scipy/…) lives in the
# project's `graph` optional-dependency group, NOT in per-script PEP-723 inline
# metadata (docs/tooling.md "Dependency model"). Run via the door
# `uv run pancratius data graph generate`, or standalone after `uv sync --extra graph`.
"""conceptosphere.py — extract concept / book graphs for Sergey Orekhov's corpus.

Two modes:
  --mode concepts (default)  → data/pancratius-concepts-graph.json
      Walks every `src/content/**/ru.md`, strips YAML frontmatter, lemmatizes the
      Russian body with pymorphy3, drops stopwords / non-content POS, then
      builds a weighted co-occurrence graph using a sliding window of N tokens
      (default 4, what InfraNodus uses). Communities are detected with Leiden
      (replacement for Louvain — same paradigm, fixes disconnected-community
      bug, slightly higher modularity in practice). Per-node we attach the
      books that feature the concept most strongly.

  --mode books               → data/pancratius-books-graph.json
      Inverse projection of the same bipartite (books × concepts) data: nodes
      are *books*, edges are shared-concept overlap. Edge weight is TF-IDF
      cosine on per-book concept frequency vectors (see `book_book_weight`).
      Pruned to top-K neighbors per book (default 10), then Leiden communities
      on the resulting graph. Per-node we attach top_concepts (most frequent
      lemmas in that book, IDF-weighted).

Run:
    uv run scripts/conceptosphere.py
    uv run scripts/conceptosphere.py --mode books
    uv run scripts/conceptosphere.py --top 400 --window 5 --min-degree 3 --min-weight 2

The script is idempotent: rerun freely. PageRank is used for centrality (cheap
on weighted graphs, behaves better than betweenness with this many nodes —
betweenness is O(VE) and would dominate; PageRank stabilises in ~50 iterations
and the relative ordering matches what InfraNodus surfaces).
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, cast

# A logging sink: ``print`` in normal runs, a no-op lambda under ``--quiet``.
# It is called purely for its side effect, so the honest signature is
# "accepts anything, returns nothing".
LogFn = Callable[..., None]

import igraph as ig
import leidenalg
import networkx as nx
import pymorphy3
import regex as re2
import yaml
from community import community_louvain  # python-louvain (kept for before/after modularity comparison)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO = Path(__file__).resolve().parent.parent
CONTENT = REPO / "src" / "content"
DATA_OUT = REPO / "data" / "pancratius-concepts-graph.json"
DATA_OUT_BOOKS = REPO / "data" / "pancratius-books-graph.json"


# ---------------------------------------------------------------------------
# Tuning configuration
# ---------------------------------------------------------------------------
# Frozen dataclass mirroring the CLI tuning flags. Defaults MUST match the
# argparse defaults in ``main()`` exactly — both the standalone CLI and the
# library door (``generate_graph``) feed the same values into the mode funcs.


@dataclass(frozen=True)
class GraphConfig:
    top: int = 420
    window: int = 4
    min_degree: int = 3
    min_weight: int = 6
    min_freq: int = 14
    edges_per_node: int = 10
    min_npmi: float = 0.18
    books_edges_per_node: int = 5
    books_min_cosine: float = 0.10

# ---------------------------------------------------------------------------
# Stopwords
# ---------------------------------------------------------------------------
# nltk's russian list, hand-extended with corpus noise we've eyeballed in this
# manuscript. The corpus is dialogic ("он сказал", "ты говоришь") so we kill
# the conversational filler aggressively; this is the main lever that turns a
# 5000-noun blob into 300 meaningful concepts.

RU_STOPWORDS_BASE = {
    # nltk russian
    "и", "в", "во", "не", "что", "он", "на", "я", "с", "со", "как", "а", "то",
    "все", "она", "так", "его", "но", "да", "ты", "к", "у", "же", "вы", "за",
    "бы", "по", "только", "ее", "мне", "было", "вот", "от", "меня", "еще",
    "нет", "о", "из", "ему", "теперь", "когда", "даже", "ну", "вдруг", "ли",
    "если", "уже", "или", "ни", "быть", "был", "него", "до", "вас", "нибудь",
    "опять", "уж", "вам", "ведь", "там", "потом", "себя", "ничего", "ей",
    "может", "они", "тут", "где", "есть", "надо", "ней", "для", "мы", "тебя",
    "их", "чем", "была", "сам", "чтоб", "без", "будто", "чего", "раз", "тоже",
    "себе", "под", "будет", "ж", "тогда", "кто", "этот", "того", "потому",
    "этого", "какой", "совсем", "ним", "здесь", "этом", "один", "почти",
    "мой", "тем", "чтобы", "нее", "сейчас", "были", "куда", "зачем", "всех",
    "никогда", "можно", "при", "наконец", "два", "об", "другой", "хоть",
    "после", "над", "больше", "тот", "через", "эти", "нас", "про", "всего",
    "них", "какая", "много", "разве", "три", "эту", "моя", "впрочем", "хорошо",
    "свою", "этой", "перед", "иногда", "лучше", "чуть", "том", "нельзя",
    "такой", "им", "более", "всегда", "конечно", "всю", "между",
    # corpus noise — pronouns, filler, demonstratives, intensifiers, modals
    "это", "этот", "эта", "это", "тот", "та", "те", "сей", "сия", "сие",
    "который", "которая", "которое", "которые", "свой", "свои", "своя", "своё",
    "весь", "вся", "всё", "все", "сам", "сама", "само", "сами", "иной", "оный",
    "такой", "такая", "такое", "такие", "каждый", "каждая", "каждое",
    "никакой", "ничей", "некий", "некоторый", "любой",
    "просто", "очень", "именно", "именно", "также", "ещё", "уже", "вообще",
    "тебе", "себе", "моё", "твоё", "наше", "ваше",
    "мочь", "хотеть", "должен", "должный", "стать", "становиться",
    "являться", "оказываться", "оказаться", "сказать", "говорить", "ответить",
    "спросить", "знать", "видеть", "слышать", "понимать", "думать",
    "идти", "пойти", "ходить", "прийти", "приходить", "уходить",
    "делать", "сделать", "взять", "брать", "дать", "давать", "получить",
    "получать", "посмотреть", "смотреть",
    # auxiliary verbs that survived but carry no concept signal
    "оставаться", "остаться", "переставать", "перестать", "начинать", "начать",
    "продолжать", "продолжить", "происходить", "произойти",
    "называть", "называться", "являть", "представлять", "находиться", "находить",
    "узнавать", "узнать", "почувствовать", "испугаться",
    # quantifier / intensifier adjectives that look like nouns to pymorphy
    "самый", "следующий", "целый", "полный",
    # narrative chrome (chapter headers, generic spatiotemporal placeholder)
    "глава", "часть", "момент",
    # corpus-specific noise (high freq, low structure)
    "режим", "проводник",
    # typographic / OCR / Word artifacts
    "г", "т", "д", "с", "н", "м", "л", "р", "п", "к",
    "ст", "стр", "гл", "тыс", "млн",
    # English / latin chrome from inline phrases
    "the", "of", "a", "to", "and", "in", "is", "it", "you", "that", "for",
    "on", "with", "as", "this", "be", "or", "an", "by", "are", "from",
}

# Lemmas we still want to KEEP even though they could look like filler — these
# carry semantic weight in this corpus.
KEEP_OVERRIDE = {
    "бог", "творец", "светозар", "христос", "иисус", "свет", "дух", "истина",
    "царствие", "царство", "осознанность", "сознание", "пробуждение",
    "любовь", "вера", "знание", "ии", "церковь", "православие", "молитва",
    "сердце", "душа", "ум", "тело", "слово", "имя", "путь", "тьма", "жизнь",
    "смерть", "грех", "святой", "святость", "русь", "россия", "мир", "время",
    "вечность", "истина", "правда", "ложь",
    "панкратиус", "евангелие", "храм", "тишина", "присутствие",
}

# Allowed POS tags from pymorphy3 (OpenCorpora set).
# NOUN — nouns (главный материал).
# VERB / INFN — verbs (we accept content verbs, then strip the curated list
# above of conversational verbs).
# ADJF — full adjectives (we keep a curated list of nominalizing adjectives
# like святой, божественный, осознанный — proper-noun-adjacent).
KEEP_POS = {"NOUN"}
KEEP_POS_VERB = {"VERB", "INFN"}
KEEP_POS_ADJ = {"ADJF", "ADJS", "PRTF", "PRTS"}
# Hard-reject these — pronouns, prepositions, conjunctions, particles,
# interjections, predicatives, comparatives, numerals-as-words. Even when
# pymorphy3 picks a "word" with these tags, they're never the concept we want.
REJECT_POS = {
    "NPRO",   # pronouns: я, ты, он, мы, etc.
    "PREP",   # prepositions
    "CONJ",   # conjunctions
    "PRCL",   # particles
    "INTJ",   # interjections
    "PRED",   # predicatives (нельзя, нужно)
    "COMP",   # comparatives
    "NUMR",   # numerals
}

# ---------------------------------------------------------------------------
# Tokenization
# ---------------------------------------------------------------------------

# Cyrillic letters incl. ё, plus latin (so we catch "ИИ", "ChatGPT", "AI")
TOKEN_RE = re2.compile(r"\p{L}+", flags=re2.UNICODE)
URL_RE = re.compile(r"https?://\S+|www\.\S+")
HTML_TAG_RE = re.compile(r"<[^>]+>")  # nuke inline <img>, <span> etc.
MD_LINK_RE = re.compile(r"!?\[[^\]]*\]\([^\)]*\)")
MD_HEADING_RE = re.compile(r"^#+\s*", flags=re.MULTILINE)
MD_BOLD_ITALIC_RE = re.compile(r"[*_`]+")
# Words that mean nothing in this corpus but pollute the lemma list (HTML
# attribute names, file extensions leaking through alt text, etc.)
NOISE_LEMMAS = {
    "src", "img", "media", "alt", "width", "height", "style", "class",
    "jpg", "jpeg", "png", "svg", "webp", "gif",
    "http", "https", "www", "html", "css",
    "data", "json",
    # russian conversational artifacts we missed in the morphology pass
    "ваш", "наш", "мой", "твой", "его", "её", "их",
    "очень", "просто", "может", "могут", "будут", "будете",
}

# Sentence boundary — naive but enough for windowed co-occurrence. Anything in
# .!?…\n\n breaks the window. (We don't want "конец главы → начало главы" to
# count as co-occurrence.)
SENT_BREAK_RE = re2.compile(r"(?<=[.!?…])\s+|\n{2,}")


def strip_frontmatter(text: str) -> tuple[dict, str]:
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            fm = text[3:end].strip()
            body = text[end + 4 :]
            try:
                meta = yaml.safe_load(fm) or {}
            except yaml.YAMLError:
                meta = {}
            return meta, body
    return {}, text


def clean_markdown(text: str) -> str:
    text = HTML_TAG_RE.sub(" ", text)
    text = URL_RE.sub(" ", text)
    text = MD_LINK_RE.sub(" ", text)
    text = MD_HEADING_RE.sub("", text)
    text = MD_BOLD_ITALIC_RE.sub("", text)
    return text


def split_sentences(text: str) -> list[str]:
    parts = SENT_BREAK_RE.split(text)
    return [p.strip() for p in parts if p and p.strip()]


# ---------------------------------------------------------------------------
# Lemmatization
# ---------------------------------------------------------------------------


class Lemmatizer:
    """Thin wrapper around pymorphy3 with an LRU cache.

    pymorphy3 is fast (~100k tokens/sec) but we still cache because the corpus
    is repetitive — many words appear thousands of times.
    """

    def __init__(self) -> None:
        self.morph = pymorphy3.MorphAnalyzer()
        self.cache: dict[str, tuple[str, str] | None] = {}

    def lemma_pos(self, word: str) -> tuple[str, str] | None:
        if word in self.cache:
            return self.cache[word]
        try:
            parses = self.morph.parse(word)
        except Exception:
            self.cache[word] = None
            return None
        if not parses:
            self.cache[word] = None
            return None
        best = parses[0]
        lemma = best.normal_form
        pos = best.tag.POS or ""
        result = (lemma, pos)
        self.cache[word] = result
        return result


def is_content_token(lemma: str, pos: str) -> bool:
    if not lemma or len(lemma) < 3:
        return False
    if lemma in RU_STOPWORDS_BASE or lemma in NOISE_LEMMAS:
        return False
    if pos in REJECT_POS:
        return False
    if lemma in KEEP_OVERRIDE:
        return True
    if pos in KEEP_POS:
        return True
    if pos in KEEP_POS_VERB:
        # Content verb? Already filtered through stopword override above; keep
        # remaining verbs but they tend to dominate frequency lists so we'll
        # let the prune handle weak ones.
        return True
    if pos in KEEP_POS_ADJ:
        # Adjectives like "святой", "божественный", "осознанный" carry weight.
        # We keep them all; degree-prune at the end drops the inert ones.
        return True
    # Foreign POS-less Latin tokens (ChatGPT, AI) — pymorphy3 won't tag them
    # but we still want them in. Must be > 2 chars (already filtered) and not
    # one of the HTML attribute fragments we explicitly blacklist.
    if not pos and re2.match(r"^[a-zA-Z]+$", lemma):
        return True
    return False


# ---------------------------------------------------------------------------
# Corpus walking
# ---------------------------------------------------------------------------


@dataclass
class Doc:
    slug: str
    title: str
    kind: str  # 'book' | 'poem' | 'project'
    number: int | None
    path: Path
    body: str
    tags: list[str]


def localized_text(value: object, lang: str = "ru") -> str:
    """Return a display string from scalar or localized frontmatter values."""
    if isinstance(value, dict):
        localized = cast("dict[str, Any]", value)
        picked = localized.get(lang) or localized.get("ru") or localized.get("en")
        if picked:
            return str(picked)
        for picked in localized.values():
            if picked:
                return str(picked)
        return ""
    return str(value or "")


def discover_docs() -> list[Doc]:
    docs: list[Doc] = []
    for sub in ("books", "poetry", "projects"):
        root = CONTENT / sub
        if not root.exists():
            continue
        for md in sorted(root.glob("*/ru.md")):
            text = md.read_text(encoding="utf-8")
            meta, body = strip_frontmatter(text)
            kind = meta.get("kind", sub[:-1])
            tags = meta.get("tags") or []
            if not isinstance(tags, list):
                tags = []
            docs.append(
                Doc(
                    slug=meta.get("slug", md.parent.name),
                    title=localized_text(meta.get("title"), "ru") or md.parent.name,
                    kind=kind,
                    number=meta.get("number"),
                    path=md,
                    body=clean_markdown(body),
                    tags=tags,
                )
            )
    return docs


# ---------------------------------------------------------------------------
# Co-occurrence
# ---------------------------------------------------------------------------


def slide_pairs(tokens: list[str], window: int) -> Iterable[tuple[str, str]]:
    """Yield unordered pairs within a sliding window (InfraNodus-style).

    Within each window every token co-occurs with every other token. We yield
    each adjacent pair distance-weighted by 1; this is what InfraNodus does
    and what the academic literature on text-network analysis converges on.
    """
    n = len(tokens)
    for i in range(n):
        a = tokens[i]
        # window forwards only (avoids double-counting)
        for j in range(i + 1, min(i + window, n)):
            b = tokens[j]
            if a == b:
                continue
            if a < b:
                yield (a, b)
            else:
                yield (b, a)


# ---------------------------------------------------------------------------
# Leiden community detection on a networkx graph
# ---------------------------------------------------------------------------


def leiden_communities(
    G: nx.Graph,
    weight_attr: str = "weight",
    seed: int = 42,
    partition_type: str = "modularity",
    resolution: float = 1.0,
) -> tuple[dict, float]:
    """Run Leiden on a networkx graph; return (node -> community_id, modularity).

    Why Leiden over Louvain:
      - Louvain can return *disconnected* communities (a known bug). Leiden's
        refinement phase guarantees connected, well-separated communities.
      - Same paradigm (modularity-driven greedy + local moves), strictly
        better local optimum on every iteration.
      - Same JSON shape downstream — `community` ints. Drop-in.
    """
    nodes = list(G.nodes())
    node_idx = {n: i for i, n in enumerate(nodes)}
    ig_edges: list[tuple[int, int]] = []
    ig_weights: list[float] = []
    for a, b, d in G.edges(data=True):
        ig_edges.append((node_idx[a], node_idx[b]))
        ig_weights.append(float(d.get(weight_attr, 1.0)))
    g = ig.Graph(n=len(nodes), edges=ig_edges, directed=False)
    g.es["weight"] = ig_weights

    if partition_type == "cpm":
        part = leidenalg.find_partition(
            g, leidenalg.CPMVertexPartition,
            weights="weight",
            resolution_parameter=resolution,
            seed=seed,
        )
    else:
        # ModularityVertexPartition is the direct comparison vs. Louvain.
        part = leidenalg.find_partition(
            g, leidenalg.ModularityVertexPartition,
            weights="weight",
            seed=seed,
        )

    membership = part.membership
    partition_dict = {nodes[i]: int(membership[i]) for i in range(len(nodes))}
    # igraph reports modularity of the partition directly (with weights).
    modularity = g.modularity(membership, weights="weight")
    return partition_dict, float(modularity)


# ---------------------------------------------------------------------------
# Corpus processing (used by both modes)
# ---------------------------------------------------------------------------


@dataclass
class CorpusBundle:
    docs: list[Doc]
    doc_streams: list[tuple[Doc, list[str]]]
    book_lemma_counts: dict[str, Counter]
    global_freq: Counter
    total_tokens_raw: int
    total_tokens_kept: int


def process_corpus(log: LogFn) -> CorpusBundle:
    docs = discover_docs()
    log(f"[corpus] {len(docs)} documents (books/poetry/projects)")

    lemmatizer = Lemmatizer()

    doc_streams: list[tuple[Doc, list[str]]] = []
    book_lemma_counts: dict[str, Counter] = defaultdict(Counter)
    global_freq: Counter = Counter()
    total_tokens_raw = 0
    total_tokens_kept = 0

    for d in docs:
        token_stream: list[str] = []
        for sentence in split_sentences(d.body):
            sent_tokens: list[str] = []
            for raw in TOKEN_RE.findall(sentence.lower()):
                total_tokens_raw += 1
                if len(raw) < 3:
                    continue
                lp = lemmatizer.lemma_pos(raw)
                if lp is None:
                    continue
                lemma, pos = lp
                if not is_content_token(lemma, pos):
                    continue
                sent_tokens.append(lemma)
                global_freq[lemma] += 1
                if d.kind == "book":
                    book_lemma_counts[d.slug][lemma] += 1
                total_tokens_kept += 1
            if sent_tokens:
                token_stream.extend(sent_tokens)
                token_stream.append("\x00")
        doc_streams.append((d, token_stream))
        log(f"[doc]    {d.slug[:50]:50s}  tokens={len(token_stream):>7d}")

    log(f"[tokens] raw={total_tokens_raw}  kept={total_tokens_kept}  "
        f"unique={len(global_freq)}")

    return CorpusBundle(
        docs=docs,
        doc_streams=doc_streams,
        book_lemma_counts=book_lemma_counts,
        global_freq=global_freq,
        total_tokens_raw=total_tokens_raw,
        total_tokens_kept=total_tokens_kept,
    )


# ---------------------------------------------------------------------------
# Mode: concepts (original — Leiden-swapped)
# ---------------------------------------------------------------------------


def run_concepts_mode(config: GraphConfig, out: Path, log: LogFn, bundle: CorpusBundle) -> int:
    docs = bundle.docs
    doc_streams = bundle.doc_streams
    book_lemma_counts = bundle.book_lemma_counts
    global_freq = bundle.global_freq
    total_tokens_raw = bundle.total_tokens_raw
    total_tokens_kept = bundle.total_tokens_kept

    # Trim by min frequency BEFORE building edges — saves a ton of memory.
    frequent = {lemma for lemma, c in global_freq.items() if c >= config.min_freq}
    log(f"[trim]   {len(frequent)} lemmas with freq >= {config.min_freq}")

    # Build co-occurrence edge weights.
    edge_w: Counter = Counter()
    for _d, stream in doc_streams:
        # Iterate stream, breaking on sentinel; only consider lemmas in frequent
        buf: list[str] = []
        for tok in stream:
            if tok == "\x00":
                if buf:
                    for a, b in slide_pairs(buf, config.window):
                        edge_w[(a, b)] += 1
                    buf = []
                continue
            if tok in frequent:
                buf.append(tok)
        if buf:
            for a, b in slide_pairs(buf, config.window):
                edge_w[(a, b)] += 1

    log(f"[edges]  raw_pairs={len(edge_w)}")

    # NPMI: normalized pointwise mutual information.
    #   pmi(a,b)  = log( p(a,b) / (p(a)*p(b)) )
    #   npmi(a,b) = pmi(a,b) / -log(p(a,b))
    # Range [-1, +1]; +1 = always together, 0 = independent, -1 = never.
    # We use it as an *edge filter* — chance co-occurrence (npmi ~0) gets cut,
    # surprising co-occurrence survives. This is the difference between
    # "every concept eventually co-occurs with every other" and a graph that
    # actually has shape.
    total_pair_obs = sum(edge_w.values()) or 1
    # marginals — how often each lemma participates in *any* pair window
    pair_marginal: Counter = Counter()
    for (a, b), w in edge_w.items():
        pair_marginal[a] += w
        pair_marginal[b] += w

    def npmi(a: str, b: str, w: int) -> float:
        p_ab = w / total_pair_obs
        p_a = pair_marginal[a] / (2 * total_pair_obs)
        p_b = pair_marginal[b] / (2 * total_pair_obs)
        if p_ab <= 0 or p_a <= 0 or p_b <= 0:
            return -1.0
        pmi = math.log(p_ab / (p_a * p_b))
        denom = -math.log(p_ab)
        if denom <= 0:
            return 0.0
        return pmi / denom

    # Build candidate edge list with NPMI scores.
    candidates: list[tuple[str, str, int, float]] = []
    for (a, b), w in edge_w.items():
        if w < config.min_weight:
            continue
        score = npmi(a, b, w)
        if score < config.min_npmi:
            continue
        candidates.append((a, b, w, score))
    log(f"[edges]  candidates after weight+npmi prune: {len(candidates)}")

    # Backbone filter — for each node keep top K neighbors by NPMI. This is
    # the move that turns a complete-ish graph into a readable shape: each
    # concept keeps its *most surprising* neighbors, not its most frequent
    # ones. Sum-of-best-from-both-sides survives.
    G = nx.Graph()
    if config.edges_per_node > 0:
        by_node: dict[str, list[tuple[str, int, float]]] = defaultdict(list)
        for a, b, w, s in candidates:
            by_node[a].append((b, w, s))
            by_node[b].append((a, w, s))
        keep_edges: set[tuple[str, str]] = set()
        for node, neighbors in by_node.items():
            neighbors.sort(key=lambda x: x[2], reverse=True)
            for other, _w, _s in neighbors[: config.edges_per_node]:
                pair = (node, other) if node < other else (other, node)
                keep_edges.add(pair)
        for a, b, w, s in candidates:
            pair = (a, b) if a < b else (b, a)
            if pair in keep_edges:
                G.add_edge(a, b, weight=w, npmi=round(s, 4))
    else:
        for a, b, w, s in candidates:
            G.add_edge(a, b, weight=w, npmi=round(s, 4))

    log(f"[graph]  nodes={G.number_of_nodes()} edges={G.number_of_edges()} (after backbone)")

    # Degree prune (iterative — removing a node may push neighbors below
    # threshold). Two passes is usually enough.
    for _ in range(3):
        weak = [n for n, deg in G.degree() if deg < config.min_degree]
        if not weak:
            break
        G.remove_nodes_from(weak)
    log(f"[graph]  nodes={G.number_of_nodes()} edges={G.number_of_edges()} (after degree prune)")

    # Cap to top N by weighted degree if still oversized.
    if G.number_of_nodes() > config.top:
        weighted_deg = {n: sum(d["weight"] for _, _, d in G.edges(n, data=True))
                        for n in G.nodes()}
        keep = set(sorted(weighted_deg, key=lambda n: weighted_deg[n], reverse=True)[: config.top])
        G = G.subgraph(keep).copy()
        log(f"[graph]  nodes={G.number_of_nodes()} (capped at top={config.top})")

    if G.number_of_nodes() == 0:
        print("ERROR: graph is empty — loosen thresholds.", file=sys.stderr)
        return 2

    # Take the giant component — fringe islands of size 1-2 just clutter.
    components = sorted(nx.connected_components(G), key=len, reverse=True)
    if len(components) > 1:
        G = G.subgraph(components[0]).copy()
        log(f"[graph]  largest component: nodes={G.number_of_nodes()} "
            f"edges={G.number_of_edges()}")

    # Centrality — PageRank on weighted graph. Reasons over betweenness:
    #   1. O(V*E) betweenness on 400 nodes / 8000 edges is fine, but PageRank
    #      surfaces "concept gravity wells" (Творец, Бог, Свет) which is what
    #      we actually want to size the nodes by.
    #   2. Betweenness over-rewards "bridge" concepts that are rare but
    #      connect two communities — useful for one-off analysis but the
    #      visualization needs the eye to land on the things that matter.
    pagerank = nx.pagerank(G, weight="weight", max_iter=200)

    # Communities — Leiden on the weighted graph (replacement for Louvain).
    # We also run Louvain on the same graph so we can print the modularity
    # delta — keeps the swap honest. The two are otherwise drop-in.
    louvain_part = community_louvain.best_partition(G, weight="weight", random_state=42)
    louvain_mod = community_louvain.modularity(louvain_part, G, weight="weight")
    partition, leiden_mod = leiden_communities(G, weight_attr="weight", seed=42)
    log(f"[comms]  Louvain modularity = {louvain_mod:.4f} "
        f"({len(set(louvain_part.values()))} communities)")
    log(f"[comms]  Leiden  modularity = {leiden_mod:.4f} "
        f"({len(set(partition.values()))} communities)")

    # Reassign community IDs in descending size order (so id 0 is the biggest)
    # and label each by its highest-pagerank member.
    comm_members: dict[int, list[str]] = defaultdict(list)
    for n, c in partition.items():
        comm_members[c].append(n)
    # `key=lambda` (not bare `key=len`): a lambda keeps the element type bound
    # to the input (`list[str]`); `key=len` would collapse it to `Sized`.
    sorted_comms = sorted(comm_members.values(), key=lambda c: len(c), reverse=True)
    comm_id_remap: dict[int, int] = {}
    for new_id, members in enumerate(sorted_comms):
        # find original id (any member works)
        orig = partition[members[0]]
        comm_id_remap[orig] = new_id
    for n in list(partition):
        partition[n] = comm_id_remap[partition[n]]

    # Community labels: the member with highest PageRank (proper-cased).
    comm_label: dict[int, str] = {}
    comm_size: dict[int, int] = {}
    for cid, members in enumerate(sorted_comms):
        best = max(members, key=lambda m: pagerank[m])
        comm_label[cid] = best.capitalize()
        comm_size[cid] = len(members)

    # Per-node top books (only books — poems are small and would dominate by
    # density). For each lemma we surface up to top-5 books by raw count.
    book_titles: dict[str, str] = {d.slug: d.title for d in docs if d.kind == "book"}

    def top_books_for(lemma: str, k: int = 5) -> list[dict]:
        scores: list[tuple[str, int]] = []
        for slug, counts in book_lemma_counts.items():
            c = counts.get(lemma, 0)
            if c:
                scores.append((slug, c))
        scores.sort(key=lambda x: x[1], reverse=True)
        out = []
        for slug, c in scores[:k]:
            out.append({
                "slug": slug,
                "kind": "book",
                "title": book_titles.get(slug, slug),
                "count": c,
            })
        return out

    # Build node list
    nodes_out = []
    for n in G.nodes():
        nodes_out.append({
            "id": n,
            "label": n.capitalize() if not n.isupper() else n,
            "lemma": n,
            "frequency": int(global_freq.get(n, 0)),
            "degree": int(G.degree(n)),
            "weighted_degree": int(sum(d["weight"] for _, _, d in G.edges(n, data=True))),
            "centrality": round(pagerank[n], 6),
            "community": int(partition[n]),
            "top_books": top_books_for(n),
        })
    # Sort nodes by centrality descending so consumers can take top-K easily
    nodes_out.sort(key=lambda x: x["centrality"], reverse=True)

    edges_out = []
    for a, b, d in G.edges(data=True):
        edges_out.append({
            "source": a,
            "target": b,
            "weight": int(d["weight"]),
            "npmi": float(d.get("npmi", 0.0)),
        })
    edges_out.sort(key=lambda e: e["weight"], reverse=True)

    communities_out = []
    for cid in sorted(set(partition.values())):
        communities_out.append({
            "id": cid,
            "label": comm_label[cid],
            "size": comm_size[cid],
            "color_index": cid % 12,
        })

    out_doc = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "params": {
            "window": config.window,
            "min_degree": config.min_degree,
            "min_weight": config.min_weight,
            "min_freq": config.min_freq,
            "min_npmi": config.min_npmi,
            "edges_per_node": config.edges_per_node,
            "top": config.top,
            "community_algorithm": "leiden-modularity",
        },
        "stats": {
            "books_processed": sum(1 for d in docs if d.kind == "book"),
            "poems_processed": sum(1 for d in docs if d.kind == "poem"),
            "projects_processed": sum(1 for d in docs if d.kind == "project"),
            "tokens_raw": total_tokens_raw,
            "tokens_kept": total_tokens_kept,
            "unique_lemmas": len(global_freq),
            "kept_nodes": G.number_of_nodes(),
            "edges": G.number_of_edges(),
            "communities": len(communities_out),
            "modularity": round(leiden_mod, 4),
            "modularity_louvain": round(louvain_mod, 4),
        },
        "communities": communities_out,
        "nodes": nodes_out,
        "edges": edges_out,
    }

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(out_doc, ensure_ascii=False, indent=2), encoding="utf-8")
    size_kb = out.stat().st_size / 1024
    log(f"\n[done]  wrote {out}  size={size_kb:.1f} KB")
    log(f"[done]  {len(nodes_out)} nodes, {len(edges_out)} edges, "
        f"{len(communities_out)} communities")
    return 0


# ---------------------------------------------------------------------------
# Mode: books (inverse projection — book × book overlap graph)
# ---------------------------------------------------------------------------


def run_books_mode(config: GraphConfig, out: Path, log: LogFn, bundle: CorpusBundle) -> int:
    """Build the inverse projection: book-book graph over shared concepts.

    Edge weight is **TF-IDF cosine similarity** on per-book concept-frequency
    vectors. Why this choice (after testing Jaccard and weighted-overlap on
    the same data):

      - Cosine handles the wide range of book lengths well — the longer
        books (#19, #40, #71 over 100k tokens) don't dominate, because
        cosine normalizes vector magnitude. Raw weighted-overlap with IDF
        is biased toward long books because they have more shared concept
        mass in absolute terms.
      - TF-IDF: rare concepts ("Светозар", "Самария", "Колобок") get weighted
        more than universal ones ("свет", "бог", "истина"). This is the
        difference between "every book shares 'свет' so everything is connected
        to everything" and visible thematic clusters. Without IDF damping the
        graph collapses to a single ball.
      - Jaccard on top-K concepts is simpler but throws away the magnitude
        signal: a book that mentions 'Светозар' 600 times shares the concept
        with one that mentions it 12 times under the same weight. Cosine
        keeps the magnitude dimension.

    After scoring, we prune to top-N neighbors per book so each book has 5–12
    connections — readable, clusterable.
    """
    docs = bundle.docs
    book_lemma_counts = bundle.book_lemma_counts
    global_freq = bundle.global_freq

    books = [d for d in docs if d.kind == "book" and d.slug in book_lemma_counts]
    books.sort(key=lambda d: (d.number or 999, d.slug))
    if len(books) < 4:
        print("ERROR: too few books for a book-book graph.", file=sys.stderr)
        return 2
    log(f"[books]  {len(books)} books with concept counts")

    # Build the vocabulary of *concepts that appear in at least 2 books*.
    # A concept that lives in only one book contributes nothing to any
    # overlap edge — drop it from the vectors to keep them sparse.
    concept_doc_freq: Counter = Counter()
    for b in books:
        for lemma in book_lemma_counts[b.slug]:
            if book_lemma_counts[b.slug][lemma] >= 2:
                concept_doc_freq[lemma] += 1
    # Keep concepts present in >= 2 books AND with raw corpus freq >= the
    # same min-freq as the concept mode (so we share the same vocabulary as
    # the concept graph; otherwise rare lemma-list noise creeps back in).
    # IMPORTANT: also drop "everywhere" concepts (df >= 90% of books). In this
    # corpus 'свет', 'бог', 'истина' appear in nearly every book and add the
    # same magnitude to every cosine score — they're a constant offset that
    # collapses thematic clusters. Dropping them sharpens contrast.
    max_df = int(0.85 * len(books))
    vocab = [
        lemma for lemma, df in concept_doc_freq.items()
        if df >= 2 and df <= max_df and global_freq.get(lemma, 0) >= config.min_freq
    ]
    vocab_idx = {lemma: i for i, lemma in enumerate(vocab)}
    log(f"[books]  vocab size = {len(vocab)} concepts "
        f"(≥2 books, ≤{max_df} books, ≥{config.min_freq} corpus freq)")

    # IDF for each vocab term — natural log smoothed.
    N = len(books)
    idf = {
        lemma: math.log((1 + N) / (1 + concept_doc_freq[lemma])) + 1.0
        for lemma in vocab
    }

    # Per-book TF-IDF sparse vector (dict lemma -> tf-idf) + norm.
    book_vec: dict[str, dict[str, float]] = {}
    book_norm: dict[str, float] = {}
    book_tokens: dict[str, int] = {}
    for b in books:
        counts = book_lemma_counts[b.slug]
        total = sum(counts.values()) or 1
        book_tokens[b.slug] = total
        vec: dict[str, float] = {}
        for lemma, c in counts.items():
            if lemma not in vocab_idx:
                continue
            # sublinear TF (log) softens the long-book advantage further
            tf = 1.0 + math.log(c)
            vec[lemma] = tf * idf[lemma]
        norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
        book_vec[b.slug] = vec
        book_norm[b.slug] = norm

    # Build an inverted index: concept -> list of (book, weight)
    # so cosine over book pairs is O(sum_lemma df^2) instead of O(B^2 * V).
    inv: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for slug, vec in book_vec.items():
        for lemma, w in vec.items():
            inv[lemma].append((slug, w))

    sim: dict[tuple[str, str], float] = defaultdict(float)
    for lemma, postings in inv.items():
        # contribution to dot product for every pair of books sharing this lemma
        for i in range(len(postings)):
            sa, wa = postings[i]
            for j in range(i + 1, len(postings)):
                sb, wb = postings[j]
                key = (sa, sb) if sa < sb else (sb, sa)
                sim[key] += wa * wb

    # Normalize to cosine.
    cosine: list[tuple[str, str, float]] = []
    for (a, b), dot in sim.items():
        c = dot / (book_norm[a] * book_norm[b])
        if c <= 0:
            continue
        cosine.append((a, b, c))
    log(f"[books]  raw cosine pairs = {len(cosine)} (before prune)")

    # Prune to top-K neighbors per book (K = config.books_edges_per_node).
    # We keep an edge if it survives in either endpoint's top-K list — this
    # is the "mutual k-NN union" trick that keeps hubs from monopolising
    # all the edges and isolated nodes from being orphaned.
    K = config.books_edges_per_node
    by_book: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for a, b, c in cosine:
        by_book[a].append((b, c))
        by_book[b].append((a, c))
    keep: set[tuple[str, str]] = set()
    for slug, neigh in by_book.items():
        neigh.sort(key=lambda x: x[1], reverse=True)
        for other, _c in neigh[:K]:
            pair = (slug, other) if slug < other else (other, slug)
            keep.add(pair)

    # Floor threshold so the very weakest links don't make it (e.g. two books
    # sharing one mid-IDF concept). Tune to roughly the 30th percentile of
    # surviving edges — books with no strong tie just sit isolated.
    sorted_keep_w = sorted(
        (cosine_score(book_vec, book_norm, a, b) for a, b in keep),
        reverse=True,
    )
    floor = config.books_min_cosine
    log(f"[books]  cosine floor = {floor:.3f}")

    G = nx.Graph()
    for b in books:
        G.add_node(b.slug)
    for a, b, c in cosine:
        pair = (a, b) if a < b else (b, a)
        if pair not in keep:
            continue
        if c < floor:
            continue
        G.add_edge(a, b, weight=round(c, 5))

    log(f"[books]  graph: nodes={G.number_of_nodes()} edges={G.number_of_edges()} "
        f"avg_deg={2*G.number_of_edges()/max(1,G.number_of_nodes()):.1f}")

    # Drop isolated books (no edges); they have nothing to say in this view.
    isolates = [n for n in G.nodes() if G.degree(n) == 0]
    if isolates:
        G.remove_nodes_from(isolates)
        log(f"[books]  removed {len(isolates)} isolated books: " +
            ", ".join(isolates))

    # Largest connected component — drop tiny fragments. Annotated because
    # `key=len` otherwise collapses the element type to `Sized` (see above).
    components: list[Any] = sorted(nx.connected_components(G), key=len, reverse=True)
    if components and len(components) > 1:
        kept = components[0]
        dropped = [n for c in components[1:] for n in c]
        G = G.subgraph(kept).copy()
        log(f"[books]  giant component: {G.number_of_nodes()} books; "
            f"dropped {len(dropped)} fragments")

    # Centrality on the pruned graph.
    pagerank = nx.pagerank(G, weight="weight", max_iter=200)

    # Communities — Leiden on the pruned book graph. We compare against
    # Louvain for the same modularity-delta print.
    louvain_part = community_louvain.best_partition(G, weight="weight", random_state=42)
    louvain_mod = community_louvain.modularity(louvain_part, G, weight="weight")
    partition, leiden_mod = leiden_communities(G, weight_attr="weight", seed=42)
    log(f"[books]  Louvain modularity = {louvain_mod:.4f} "
        f"({len(set(louvain_part.values()))} communities)")
    log(f"[books]  Leiden  modularity = {leiden_mod:.4f} "
        f"({len(set(partition.values()))} communities)")

    # Reassign community IDs by size desc.
    members: dict[int, list[str]] = defaultdict(list)
    for n, c in partition.items():
        members[c].append(n)
    # See note above: `key=lambda` keeps the `list[str]` element type.
    sorted_comms = sorted(members.values(), key=lambda c: len(c), reverse=True)
    remap: dict[int, int] = {}
    for new_id, group in enumerate(sorted_comms):
        remap[partition[group[0]]] = new_id
    for n in list(partition):
        partition[n] = remap[partition[n]]

    # Label each community by its most-central book's title (short form).
    # If a tag dominates within the community we use that as a subtitle.
    book_by_slug = {b.slug: b for b in books}

    def short_title(t: str) -> str:
        # First clause before colon/period/em-dash, then a generous length cap.
        # Truncating "Хрис...та" (saving 2 chars by spending 1 ellipsis) reads
        # broken — only truncate when there's a real reason, and at a word
        # boundary so the cut is legible.
        t = t.split(":")[0].split(" — ")[0].split(".")[0].strip()
        MAX_LEN = 48
        if len(t) <= MAX_LEN:
            return t
        # Find a word boundary before MAX_LEN; if none found in a reasonable
        # range, take the hard cut and accept it.
        cut = t.rfind(" ", 0, MAX_LEN)
        if cut < MAX_LEN - 16:  # boundary too far back → hard-cut
            cut = MAX_LEN
        return t[:cut].rstrip(" -—:,") + "…"

    comm_label: dict[int, str] = {}
    comm_size: dict[int, int] = {}
    for cid, group in enumerate(sorted_comms):
        best = max(group, key=lambda s: pagerank.get(s, 0))
        b = book_by_slug.get(best)
        comm_label[cid] = short_title(b.title) if b else best
        comm_size[cid] = len(group)

    # Top concepts per book — count * idf so universal lemmas don't dominate.
    # We use the *full* IDF here (including universal concepts dropped from
    # the cosine vocab) — readers want to see "Свет" in the top-concept list
    # even though we didn't use it for clustering.
    full_idf = {
        lemma: math.log((1 + N) / (1 + df)) + 1.0
        for lemma, df in concept_doc_freq.items()
        if df >= 2 and global_freq.get(lemma, 0) >= config.min_freq
    }

    def top_concepts_for(slug: str, k: int = 10) -> list[dict]:
        counts = book_lemma_counts[slug]
        scored: list[tuple[str, int, float]] = []
        for lemma, c in counts.items():
            if lemma not in full_idf:
                continue
            scored.append((lemma, c, c * full_idf[lemma]))
        scored.sort(key=lambda x: x[2], reverse=True)
        return [
            {
                "label": lemma.capitalize() if not lemma.isupper() else lemma,
                "lemma": lemma,
                "count": int(c),
            }
            for lemma, c, _ in scored[:k]
        ]

    # Top similar books for the side panel (top 5 neighbors by edge weight).
    # We attach these to each node so the panel can render them without a
    # second pass over edges.
    top_similar: dict[str, list[dict]] = {}
    doc_by_slug = {d.slug: d for d in docs}
    book_titles = {b.slug: b.title for b in books}
    for slug in G.nodes():
        neigh = []
        for other in G.neighbors(slug):
            w = G[slug][other]["weight"]
            neigh.append((other, w))
        neigh.sort(key=lambda x: x[1], reverse=True)
        top_similar[slug] = [
            {
                "slug": other,
                "kind": "book",
                "title": book_titles.get(other, other),
                "weight": round(float(w), 4),
            }
            for other, w in neigh[:5]
        ]

    # Top similar content by semantic embedding (Qwen3 + mean-centering). Loaded
    # from the sibling pipeline's output if available — embedded into each
    # node here so the panel doesn't need a separate 1 MB fetch just to render
    # 5 list rows. Unlike TF-IDF overlap, this can point at books, poems, or
    # projects; every row carries `kind` so the UI can link it correctly.
    top_similar_embed: dict[str, list[dict]] = {}
    embed_path = REPO / "data" / "conceptosphere-embed.json"
    if embed_path.exists():
        try:
            embed_data = json.loads(embed_path.read_text(encoding="utf-8"))
            for n in embed_data.get("nodes", []):
                sid = n.get("id") or n.get("slug")
                if not sid:
                    continue
                ms = n.get("most_similar") or []
                rows: list[dict] = []
                for m in ms[:5]:
                    target_slug = m.get("slug")
                    if not target_slug:
                        continue
                    target_doc = doc_by_slug.get(target_slug)
                    rows.append({
                        "slug": target_slug,
                        "kind": m.get("kind") or (target_doc.kind if target_doc else "book"),
                        "title": (target_doc.title if target_doc else localized_text(m.get("title"), "ru"))
                                 or str(target_slug),
                        "weight": round(float(m.get("sim") or m.get("weight") or 0), 4),
                    })
                top_similar_embed[sid] = rows
            log(f"merged semantic-similar rankings for "
                f"{len(top_similar_embed)} content items from {embed_path.name}")
        except (OSError, json.JSONDecodeError) as e:
            log(f"warn: could not load {embed_path.name}: {e}")
    else:
        log(f"note: {embed_path.name} not present — semantic recs will be empty")

    # Build node list.
    nodes_out = []
    for slug in G.nodes():
        b = book_by_slug.get(slug)
        if b is None:
            continue
        nodes_out.append({
            "id": slug,
            "slug": slug,
            "number": b.number,
            "label": b.title,
            "title": b.title,
            "tags": b.tags,
            "frequency": int(book_tokens.get(slug, 0)),
            "degree": int(G.degree(slug)),
            "weighted_degree": round(sum(G[slug][n]["weight"] for n in G.neighbors(slug)), 4),
            "centrality": round(pagerank.get(slug, 0.0), 6),
            "community": int(partition[slug]),
            "top_concepts": top_concepts_for(slug),
            "top_similar": top_similar[slug],
            "top_similar_embed": top_similar_embed.get(slug, []),
        })
    nodes_out.sort(key=lambda x: x["centrality"], reverse=True)

    edges_out = []
    for a, b, d in G.edges(data=True):
        edges_out.append({
            "source": a,
            "target": b,
            "weight": round(float(d["weight"]), 5),
        })
    edges_out.sort(key=lambda e: e["weight"], reverse=True)

    communities_out = []
    for cid in sorted(set(partition.values())):
        communities_out.append({
            "id": cid,
            "label": comm_label[cid],
            "size": comm_size[cid],
            "color_index": cid % 12,
        })

    out_doc = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "mode": "books",
        "params": {
            "edge_weight": "tfidf-cosine",
            "edges_per_node": config.books_edges_per_node,
            "min_cosine": config.books_min_cosine,
            "min_freq": config.min_freq,
            "community_algorithm": "leiden-modularity",
        },
        "stats": {
            "books": G.number_of_nodes(),
            "edges": G.number_of_edges(),
            "communities": len(communities_out),
            "modularity": round(leiden_mod, 4),
            "modularity_louvain": round(louvain_mod, 4),
            "vocab": len(vocab),
            "avg_degree": round(2 * G.number_of_edges() / max(1, G.number_of_nodes()), 2),
        },
        "communities": communities_out,
        "nodes": nodes_out,
        "edges": edges_out,
    }

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(out_doc, ensure_ascii=False, indent=2), encoding="utf-8")
    size_kb = out.stat().st_size / 1024
    log(f"\n[done]  wrote {out}  size={size_kb:.1f} KB")
    log(f"[done]  {len(nodes_out)} books, {len(edges_out)} edges, "
        f"{len(communities_out)} communities")
    return 0


def cosine_score(
    book_vec: dict[str, dict[str, float]],
    book_norm: dict[str, float],
    a: str,
    b: str,
) -> float:
    va, vb = book_vec[a], book_vec[b]
    if len(va) > len(vb):
        va, vb = vb, va
    dot = sum(va[k] * vb.get(k, 0.0) for k in va)
    return dot / (book_norm[a] * book_norm[b]) if dot else 0.0


# ---------------------------------------------------------------------------
# Library door — one typed entry both the CLI and the console-script call
# ---------------------------------------------------------------------------


def generate_graph(
    *,
    only: str | None = None,
    config: GraphConfig = GraphConfig(),
    concepts_out: Path | None = None,
    books_out: Path | None = None,
    quiet: bool = False,
) -> int:
    """Regenerate BOTH graph projections off one corpus scan (or one, via `only`).

    Owns the run timing so EVERY caller (the `pancratius data graph generate` door
    and the standalone `main`) gets the `[time]` line — `main` no longer times."""
    log: LogFn = print if not quiet else (lambda *a, **k: None)
    t0 = time.time()
    bundle = process_corpus(log)
    rc = 0
    # Attempt BOTH projections off the one bundle (the run_* call is the left
    # operand, so it always executes — a failed concepts projection does not skip
    # books); return the first nonzero exit. On the standalone single-mode path
    # exactly one branch runs, so this is `run_X(...) or 0` == run_X(...).
    if only in (None, "concepts"):
        rc = run_concepts_mode(config, concepts_out or DATA_OUT, log, bundle) or rc
    if only in (None, "books"):
        rc = run_books_mode(config, books_out or DATA_OUT_BOOKS, log, bundle) or rc
    log(f"[time]   total elapsed {time.time() - t0:.1f}s")
    return rc


# ---------------------------------------------------------------------------
# Main — dispatch on --mode
# ---------------------------------------------------------------------------


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        description="Extract a co-occurrence concept graph (--mode concepts) "
        "or a book-book overlap graph (--mode books) from the Pancratius corpus.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    ap.add_argument("--mode", choices=("concepts", "books"), default="concepts",
                    help="Which projection to build.")
    # Shared corpus params (used in concepts mode; min-freq also used in books mode)
    ap.add_argument("--top", type=int, default=420,
                    help="[concepts] Cap number of nodes kept (after pruning).")
    ap.add_argument("--window", type=int, default=4,
                    help="[concepts] Co-occurrence window (in content tokens).")
    ap.add_argument("--min-degree", type=int, default=3,
                    help="[concepts] Drop nodes with degree below this.")
    ap.add_argument("--min-weight", type=int, default=6,
                    help="[concepts] Drop edges with raw co-occurrence weight below this.")
    ap.add_argument("--min-freq", type=int, default=14,
                    help="Drop lemmas appearing fewer than this many times in corpus.")
    ap.add_argument("--edges-per-node", type=int, default=10,
                    help="[concepts] Backbone filter: keep top-K strongest edges per node.")
    ap.add_argument("--min-npmi", type=float, default=0.18,
                    help="[concepts] Drop edges with NPMI below this.")
    # Books-mode params
    ap.add_argument("--books-edges-per-node", type=int, default=5,
                    help="[books] Keep top-K most-similar neighbors per book "
                    "(mutual k-NN union, so each book ends up with ~5–12).")
    ap.add_argument("--books-min-cosine", type=float, default=0.10,
                    help="[books] Drop edges with cosine similarity below this. "
                    "Books mode uses pure k-NN by default; this is just a floor "
                    "against pathologically-weak ties.")
    ap.add_argument("--out", type=Path, default=None,
                    help="Output JSON path. Default depends on --mode.")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    config = GraphConfig(top=args.top, window=args.window, min_degree=args.min_degree,
        min_weight=args.min_weight, min_freq=args.min_freq, edges_per_node=args.edges_per_node,
        min_npmi=args.min_npmi, books_edges_per_node=args.books_edges_per_node,
        books_min_cosine=args.books_min_cosine)
    # generate_graph owns the timing line, so the door and the standalone CLI agree.
    return generate_graph(only=args.mode, config=config,
        concepts_out=args.out if args.mode == "concepts" else None,
        books_out=args.out if args.mode == "books" else None, quiet=args.quiet)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
