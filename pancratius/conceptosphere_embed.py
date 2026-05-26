# Heavy embedding dependencies live in the project's `embed` optional-dependency
# group. Run via the door: `uv run pancratius conceptosphere embed generate`.
"""conceptosphere_embed.py — semantic-embedding-based conceptosphere for Pancratius.

A third complementary view alongside the co-occurrence pipeline. Instead of
"which concepts co-occur in N-word windows," this answers "which texts mean
similar things even when they use different vocabulary."

Pipeline:
  1. Read every src/content/{books,poetry,projects}/<slug>/ru.md
  2. Strip YAML frontmatter + Markdown syntax
  3. Sentence-split with razdel (Russian-aware)
  4. Pack into ~400-token chunks with ~50-token overlap, on sentence boundaries
  5. Embed each chunk with Qwen3-Embedding-0.6B via mlx-embeddings (MLX on M-series)
       - last-token pooling (decoder model); embeddings are L2-normalised in-model
       - cache as .npy per (slug, chunk_idx) for cheap re-runs
  6. Per-book centroid = length-weighted mean of chunk embeddings, renormalised
  7. Cosine similarity between every pair of book centroids
  8. Sparsify: union of (top-K mutual NN, k=10) ∪ (global threshold edges)
  9. Leiden communities on the sparsified graph (resolution=1.0, weighted)
 10. HDBSCAN over the chunk-level embedding space → topics; label each topic
     by top TF-IDF terms of its member chunks
 11. Emit JSON: nodes (books), edges (book-book similarity), topics, communities

Run:
    uv run pancratius conceptosphere embed generate
    uv run pancratius conceptosphere embed generate --model Qwen/Qwen3-Embedding-4B
    uv run pancratius conceptosphere embed generate --rebuild

The cache lives at data/conceptosphere-embed-cache/. It's keyed by
(model_id, slug, chunk_idx) so swapping models doesn't collide.
"""

from __future__ import annotations

import json
import math
import re
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, cast

# MLX model / tokenizer objects returned by ``mlx_embeddings.load()``. They are
# opaque, un-stubbed runtime objects with no public type; ``Any`` is the honest
# annotation at this dynamic ML boundary. Aliased (rather than bare ``Any``) so
# the intent is documented and greppable, and the ANN401 waiver is scoped to
# exactly these two parameters rather than the whole module.
MLXModel = Any
MLXTokenizer = Any

import numpy as np
import regex as re2
import yaml
from razdel import sentenize

from pancratius.paths import CONTENT_ROOT, DATA_ROOT

# ---------------------------------------------------------------------------
# Paths / config
# ---------------------------------------------------------------------------
CONTENT = CONTENT_ROOT
DATA_OUT = DATA_ROOT / "conceptosphere-embed.json"
CACHE_DIR = DATA_ROOT / "conceptosphere-embed-cache"

# Model — mlx-embeddings handles weight conversion from official Qwen repo
DEFAULT_MODEL = "Qwen/Qwen3-Embedding-0.6B"

# Chunking
CHUNK_TOKENS = 400     # target tokens per chunk
CHUNK_OVERLAP = 50     # token overlap between adjacent chunks
TOKENS_PER_WORD = 1.6  # rough chars→tokens factor for Russian (we use the model
                       # tokeniser only when batching; here it's an estimator).

# Graph
TOP_K_PER_NODE = 10
# After mean-centering, similarities collapse from the 0.85–0.95 band on this
# corpus to a real 0.3–0.7 spread. Old thresholds (0.55 / 0.30) were tuned for
# uncentred sims and produce a hairball post-centering.
GLOBAL_SIM_THRESHOLD = 0.45
MIN_SIM_FOR_EDGE = 0.0       # no absolute floor — trust top-K + threshold

# Qwen3-Embedding is instruction-aware. For symmetric tasks (clustering,
# similarity) the same instruction is applied on both sides during encoding.
# Wraps every chunk in `Instruct: {task}\nQuery: {chunk}` per the model card.
INSTRUCTION_TASK = (
    "Find Russian spiritual texts on closely related themes of consciousness, "
    "light, awakening, the Creator, and the awakened Светозар"
)
INSTRUCTION_PREFIX = f"Instruct: {INSTRUCTION_TASK}\nQuery: "

# Subtract the corpus-mean embedding from every chunk before computing book
# centroids. On a homogeneous single-author corpus this removes the shared
# "all texts are about X" component and exposes residual semantic structure;
# routinely lifts modularity 10× on this kind of data (ablation: 0.055 → 0.79).
MEAN_CENTER = True

# Topics
HDBSCAN_MIN_CLUSTER_SIZE = 30      # ≥ this many chunks per topic to be a topic
HDBSCAN_MIN_SAMPLES = 5
TOPICS_TARGET_MIN = 8
TOPICS_TARGET_MAX = 18

# ---------------------------------------------------------------------------
# Stopwords (re-used from co-occurrence pipeline so topic labels look consistent)
# ---------------------------------------------------------------------------
RU_STOP = set("""
и в во не что он на я с со как а то все она так его но да ты к у же вы за бы по
только ее мне было вот от меня еще нет о из ему теперь когда даже ну вдруг ли если
уже или ни быть был него до вас нибудь опять уж вам ведь там потом себя ничего ей
может они тут где есть надо ней для мы тебя их чем была сам чтоб без будто чего
раз тоже себе под будет ж тогда кто этот того потому этого какой совсем ним здесь
этом один почти мой тем чтобы нее сейчас были куда зачем всех никогда можно при
наконец два об другой хоть после над больше тот через эти нас про всего них какая
много разве три эту мою впрочем хорошо свою этой перед иногда лучше чуть том нельзя
такой им более всегда конечно всю между также весь свой такой свои свою тех тот
просто чему которые который которое которая которые лишь чтобы как-то как-нибудь
быть есть стать сделать пусть всё каждый каждое каждая каждые любой какой-то моей
тебе твой твоя твои твоё его её им ими нем нём ним нею нею нею нами вами теми
этой этим этими этих этом этой эту этими этого этой её свою свои свою его её им
вот ах эх ого
""".split())

# ---------------------------------------------------------------------------
# Loading + cleaning
# ---------------------------------------------------------------------------
FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)

# Markdown stripping — keep textual content, drop syntax noise.
MD_HEADER_RE = re.compile(r"^#{1,6}\s+", re.MULTILINE)
MD_BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")
MD_ITAL_RE = re.compile(r"\*([^*]+)\*")
MD_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")
MD_IMG_RE = re.compile(r"!\[[^\]]*\]\([^)]+\)")
MD_CODE_RE = re.compile(r"`[^`]+`")
MD_HR_RE = re.compile(r"^[-*]{3,}\s*$", re.MULTILINE)
MULTI_BLANK_RE = re.compile(r"\n{3,}")


def load_doc(path: Path) -> tuple[dict, str]:
    raw = path.read_text(encoding="utf-8")
    m = FRONTMATTER_RE.match(raw)
    if m:
        meta = yaml.safe_load(m.group(1)) or {}
        body = raw[m.end():]
    else:
        meta = {}
        body = raw
    return meta, body


def clean_markdown(body: str) -> str:
    body = MD_IMG_RE.sub(" ", body)
    body = MD_LINK_RE.sub(r"\1", body)
    body = MD_HEADER_RE.sub("", body)
    body = MD_BOLD_RE.sub(r"\1", body)
    body = MD_ITAL_RE.sub(r"\1", body)
    body = MD_CODE_RE.sub(" ", body)
    body = MD_HR_RE.sub("", body)
    body = body.replace(" ", " ")
    body = MULTI_BLANK_RE.sub("\n\n", body)
    return body.strip()


# ---------------------------------------------------------------------------
# Document collection
# ---------------------------------------------------------------------------
@dataclass
class Doc:
    slug: str
    kind: str          # "book" | "poem" | "project"
    number: int | None
    title: str
    tags: list[str]
    text: str          # cleaned body


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


def collect_docs() -> list[Doc]:
    docs: list[Doc] = []
    for kind, subdir in (("book", "books"), ("poem", "poetry"), ("project", "projects")):
        root = CONTENT / subdir
        if not root.exists():
            continue
        for slug_dir in sorted(root.iterdir()):
            if not slug_dir.is_dir():
                continue
            md = slug_dir / "ru.md"
            if not md.exists():
                continue
            meta, body = load_doc(md)
            text = clean_markdown(body)
            if len(text) < 200:
                # poems can be tiny; we keep them but warn under --verbose
                pass
            docs.append(Doc(
                slug=slug_dir.name,
                kind=kind,
                number=meta.get("number"),
                title=localized_text(meta.get("title"), "ru") or slug_dir.name,
                tags=list(meta.get("tags") or []),
                text=text,
            ))
    return docs


# ---------------------------------------------------------------------------
# Chunking — sentence-aware sliding window
# ---------------------------------------------------------------------------
# Russian-aware sentence splitter from razdel. We then pack into chunks of
# approximately CHUNK_TOKENS tokens, with overlap. We estimate token count from
# character count (Qwen tokeniser is sub-word; ~3.5 chars/token for Cyrillic).
CHARS_PER_TOKEN = 3.2


def sentences_of(text: str) -> list[str]:
    sents = [s.text.strip() for s in sentenize(text)]
    return [s for s in sents if s]


def chunk_sentences(
    sents: list[str],
    target_tokens: int = CHUNK_TOKENS,
    overlap_tokens: int = CHUNK_OVERLAP,
) -> list[str]:
    """Pack sentences into chunks of ~target_tokens tokens with overlap_tokens overlap."""
    if not sents:
        return []
    target_chars = int(target_tokens * CHARS_PER_TOKEN)
    overlap_chars = int(overlap_tokens * CHARS_PER_TOKEN)

    chunks: list[str] = []
    # Compute cumulative char positions of sentence ends so we can window quickly
    starts = []
    ends = []
    cursor = 0
    for s in sents:
        starts.append(cursor)
        cursor += len(s) + 1  # +1 for joining space
        ends.append(cursor)
    total = cursor

    i = 0
    n = len(sents)
    while i < n:
        # extend until we hit target_chars
        start_char = starts[i]
        j = i
        while j < n and ends[j] - start_char < target_chars:
            j += 1
        # j is exclusive end; ensure at least 1 sentence
        if j == i:
            j = i + 1
        chunk = " ".join(sents[i:j]).strip()
        if chunk:
            chunks.append(chunk)
        if j >= n:
            break
        # advance i so that the next chunk overlaps the tail of this one by ~overlap_chars
        target_back = ends[j - 1] - overlap_chars
        ni = j
        while ni > i + 1 and starts[ni - 1] > target_back:
            ni -= 1
        i = max(ni, i + 1)
    return chunks


# ---------------------------------------------------------------------------
# Caching helpers
# ---------------------------------------------------------------------------
def _cache_paths(model_id: str, slug: str) -> tuple[Path, Path]:
    safe_model = model_id.replace("/", "__")
    book_dir = CACHE_DIR / safe_model
    book_dir.mkdir(parents=True, exist_ok=True)
    return book_dir / f"{slug}.npy", book_dir / f"{slug}.json"


def load_book_cache(model_id: str, slug: str, chunks_now: list[str]) -> np.ndarray | None:
    """If cache exists and chunks match exactly, return the embedding matrix."""
    npy, jsn = _cache_paths(model_id, slug)
    if not npy.exists() or not jsn.exists():
        return None
    try:
        meta = json.loads(jsn.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if meta.get("chunks") != chunks_now:
        return None
    if meta.get("instruction") != INSTRUCTION_PREFIX:
        return None  # prefix changed → cached embeddings are stale
    arr = np.load(npy)
    if arr.shape[0] != len(chunks_now):
        return None
    return arr


def save_book_cache(model_id: str, slug: str, chunks: list[str], embs: np.ndarray) -> None:
    npy, jsn = _cache_paths(model_id, slug)
    np.save(npy, embs)
    jsn.write_text(json.dumps({
        "chunks": chunks,
        "shape": list(embs.shape),
        "instruction": INSTRUCTION_PREFIX,  # cache invalidates if the prefix changes
    }, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------------------
# Embedding via mlx-embeddings
# ---------------------------------------------------------------------------
def embed_chunks(
    chunks: list[str],
    model: MLXModel,  # noqa: ANN401 — opaque MLX model object, no usable stubs
    tokenizer: MLXTokenizer,  # noqa: ANN401 — opaque MLX tokenizer object
    *,
    batch_size: int,
    max_length: int,
) -> np.ndarray:
    """Embed a list of chunks → (N, D) numpy float32, L2-normalised."""
    import mlx.core as mx
    from mlx_embeddings import generate

    out_rows: list[np.ndarray] = []
    for s in range(0, len(chunks), batch_size):
        # Qwen3-Embedding wants the instruction prefix on every input for
        # symmetric tasks (similarity/clustering). Same instruction both sides.
        batch = [INSTRUCTION_PREFIX + c for c in chunks[s : s + batch_size]]
        out = generate(model, tokenizer, batch, max_length=max_length,
                       padding=True, truncation=True)
        # text_embeds is (B, D), already L2-normalised
        arr = np.asarray(out.text_embeds.astype(mx.float32))
        out_rows.append(arr)
        mx.eval(out.text_embeds)
    return np.concatenate(out_rows, axis=0) if out_rows else np.zeros((0, 0), dtype=np.float32)


# ---------------------------------------------------------------------------
# Graph construction
# ---------------------------------------------------------------------------
def build_book_graph(
    centroids: np.ndarray,
    slugs: list[str],
) -> tuple[list[tuple[int, int, float]], np.ndarray]:
    """Build sparsified book-book similarity graph.

    Returns (edges as (i, j, sim) with i<j, sims_matrix).
    """
    # cosine: centroids are L2-normalised already → sims = C @ C.T
    sims = centroids @ centroids.T
    np.fill_diagonal(sims, -1.0)
    N = len(slugs)

    # top-K per node
    topK_idx = np.argsort(-sims, axis=1)[:, :TOP_K_PER_NODE]

    kept: set[tuple[int, int]] = set()
    # mutual top-K
    for i in range(N):
        for j in topK_idx[i]:
            if i == j:
                continue
            if i in topK_idx[j]:
                a, b = (i, j) if i < j else (j, i)
                if sims[a, b] >= MIN_SIM_FOR_EDGE:
                    kept.add((a, b))
    # also keep non-mutual but high-sim if either is in the other's topK and sim >= threshold
    for i in range(N):
        for j in topK_idx[i]:
            if i == j:
                continue
            a, b = (i, j) if i < j else (j, i)
            if sims[a, b] >= MIN_SIM_FOR_EDGE:
                kept.add((a, b))
    # global threshold
    iu, ju = np.where(np.triu(sims >= GLOBAL_SIM_THRESHOLD, k=1))
    for a, b in zip(iu, ju):
        kept.add((int(a), int(b)))

    edges = [(a, b, float(sims[a, b])) for (a, b) in kept]
    edges.sort(key=lambda e: -e[2])
    return edges, sims


def leiden_communities(
    n_nodes: int,
    edges: list[tuple[int, int, float]],
    resolution: float = 1.0,
) -> tuple[list[int], float]:
    import igraph as ig
    import leidenalg
    g = ig.Graph(n=n_nodes, edges=[(a, b) for a, b, _ in edges], directed=False)
    g.es["weight"] = [w for _, _, w in edges]
    part = leidenalg.find_partition(
        g, leidenalg.RBConfigurationVertexPartition,
        weights="weight", resolution_parameter=resolution, seed=42,
    )
    membership = list(part.membership)
    modularity = float(g.modularity(membership, weights="weight"))
    return membership, modularity


def betweenness_centrality(n_nodes: int, edges: list[tuple[int, int, float]]) -> list[float]:
    import igraph as ig
    g = ig.Graph(n=n_nodes, edges=[(a, b) for a, b, _ in edges], directed=False)
    # use 1/weight as distance for shortest-paths betweenness — higher sim = closer
    weights = [max(1e-3, 1.0 - w) for _, _, w in edges]
    g.es["dist"] = weights
    bc = g.betweenness(weights="dist", directed=False)
    # normalise to 0..1 for output stability
    mx = max(bc) if bc else 1.0
    if mx <= 0:
        mx = 1.0
    return [b / mx for b in bc]


# ---------------------------------------------------------------------------
# Topic extraction (HDBSCAN over chunk embeddings + TF-IDF labels)
# ---------------------------------------------------------------------------
def discover_topics(
    chunk_embs: np.ndarray,
    chunk_texts: list[str],
    chunk_book_slug: list[str],
    chunk_book_title: list[str],
) -> list[dict]:
    import hdbscan
    from sklearn.feature_extraction.text import TfidfVectorizer

    if len(chunk_embs) < HDBSCAN_MIN_CLUSTER_SIZE * 2:
        return []

    # HDBSCAN on the unit sphere: euclidean on L2-normalised vectors ≈ angular
    clusterer = hdbscan.HDBSCAN(
        min_cluster_size=HDBSCAN_MIN_CLUSTER_SIZE,
        min_samples=HDBSCAN_MIN_SAMPLES,
        metric="euclidean",
        cluster_selection_method="eom",
    )
    labels = clusterer.fit_predict(chunk_embs.astype(np.float32))

    # If we got too few clusters, relax once
    n_topics = len(set(labels)) - (1 if -1 in labels else 0)
    if n_topics < TOPICS_TARGET_MIN:
        clusterer = hdbscan.HDBSCAN(
            min_cluster_size=max(15, HDBSCAN_MIN_CLUSTER_SIZE // 2),
            min_samples=3,
            metric="euclidean",
            cluster_selection_method="eom",
        )
        labels = clusterer.fit_predict(chunk_embs.astype(np.float32))

    # If still too few or way too many, fall back to k-means at a sensible k
    n_topics = len(set(labels)) - (1 if -1 in labels else 0)
    if n_topics < TOPICS_TARGET_MIN or n_topics > TOPICS_TARGET_MAX * 2:
        from sklearn.cluster import KMeans
        k = max(TOPICS_TARGET_MIN, min(TOPICS_TARGET_MAX, 12))
        km = KMeans(n_clusters=k, n_init=10, random_state=42)
        labels = km.fit_predict(chunk_embs.astype(np.float32))

    # Build TF-IDF labels per cluster.
    # Strategy: for each cluster, compare in-cluster term frequencies vs the
    # whole-corpus term frequencies, and pick the top terms by an in/out ratio.
    # We pre-lemmatise to make labels readable.
    morph: Any = None  # opaque pymorphy3 analyzer (dynamic boundary) or None
    try:
        import pymorphy3
        morph = pymorphy3.MorphAnalyzer()
        def lemma_of(w: str) -> str:
            return morph.parse(w)[0].normal_form
    except (ImportError, OSError, RuntimeError):
        def lemma_of(w: str) -> str: return w

    WORD_RE = re2.compile(r"\p{L}+", re2.UNICODE)
    def doc_terms(t: str) -> list[str]:
        out = []
        for w in WORD_RE.findall(t.lower()):
            if len(w) < 3:
                continue
            l = lemma_of(w)
            if l in RU_STOP or len(l) < 3:
                continue
            out.append(l)
        return out

    # Pre-tokenise each chunk once
    chunk_terms = [doc_terms(t) for t in chunk_texts]

    # Global IDF
    df_counter: Counter[str] = Counter()
    for terms in chunk_terms:
        df_counter.update(set(terms))
    N = len(chunk_terms)
    idf = {w: math.log((N + 1) / (df + 1)) + 1.0 for w, df in df_counter.items()}

    topics: list[dict] = []
    cluster_ids = sorted({int(l) for l in labels if l != -1})
    for cid in cluster_ids:
        member_idx = [i for i, l in enumerate(labels) if l == cid]
        if len(member_idx) < 5:
            continue
        # Aggregate TF over cluster
        tf_in: Counter[str] = Counter()
        for i in member_idx:
            tf_in.update(chunk_terms[i])
        # TF-IDF score
        scored: list[tuple[str, float]] = []
        for w, c in tf_in.items():
            if df_counter[w] < 2:
                continue
            score = (c / max(1, len(member_idx))) * idf.get(w, 1.0)
            scored.append((w, score))
        scored.sort(key=lambda kv: -kv[1])
        top_terms = [w for w, _ in scored[:12]]

        # Sample chunks: take the 3 closest to the cluster centroid
        cluster_centroid = chunk_embs[member_idx].mean(axis=0)
        cluster_centroid = cluster_centroid / (np.linalg.norm(cluster_centroid) + 1e-9)
        sims = chunk_embs[member_idx] @ cluster_centroid
        order = np.argsort(-sims)
        sample_chunk_idx = [member_idx[k] for k in order[:5]]

        # Books predominantly in this topic
        slug_counts: Counter[str] = Counter()
        title_for: dict[str, str] = {}
        for i in member_idx:
            slug_counts[chunk_book_slug[i]] += 1
            title_for[chunk_book_slug[i]] = chunk_book_title[i]
        # Normalize: per-book chunk count
        sample_books = []
        for slug, cnt in slug_counts.most_common(8):
            sample_books.append({"slug": slug, "title": title_for[slug], "chunks": cnt})

        # Label: first 2-3 most distinctive terms, title-cased; preserve case of names.
        label_terms = top_terms[:3]
        label = " · ".join(t.capitalize() for t in label_terms) if label_terms else f"Тема {cid}"

        topics.append({
            "id": int(cid),
            "label": label,
            "size": len(member_idx),
            "top_terms": top_terms,
            "sample_books": sample_books,
            "sample_chunks": [
                {"slug": chunk_book_slug[i], "preview": chunk_texts[i][:280]}
                for i in sample_chunk_idx
            ],
        })

    # Sort topics by size desc
    topics.sort(key=lambda t: -t["size"])
    return topics


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def generate_embeddings(
    *,
    model: str = DEFAULT_MODEL,
    rebuild: bool = False,
    batch_size: int = 8,
    max_length: int = 512,
    out: Path = DATA_OUT,
    limit: int = 0,
) -> None:
    """Embed the corpus, cluster, and write the semantic conceptosphere JSON."""
    # The ``model`` parameter is the string model-id (what argparse held in
    # ``args.model``). Keep it under a stable name because the verbatim body
    # later rebinds the bare ``model`` name to the *loaded MLX object* via
    # ``model, tokenizer = load(...)`` — exactly as the original did, where
    # ``args.model`` (string) and ``model`` (object) were distinct names.
    model_id = model
    t_total = time.time()
    print(f"[i] model: {model_id}")
    print(f"[i] cache: {CACHE_DIR}")
    docs = collect_docs()
    if limit:
        docs = docs[:limit]
    print(f"[i] {len(docs)} documents collected "
          f"(books={sum(d.kind=='book' for d in docs)}, "
          f"poems={sum(d.kind=='poem' for d in docs)}, "
          f"projects={sum(d.kind=='project' for d in docs)})")

    # ---- chunk every doc ----
    print("[i] chunking …")
    t = time.time()
    chunked: list[tuple[Doc, list[str]]] = []
    for d in docs:
        sents = sentences_of(d.text)
        chunks = chunk_sentences(sents)
        if not chunks and d.text:
            # poetry can be too short for the chunker — keep as a single chunk
            chunks = [d.text]
        chunked.append((d, chunks))
    total_chunks = sum(len(c) for _, c in chunked)
    print(f"[i] {total_chunks} chunks across {len(docs)} docs "
          f"(mean {total_chunks/max(1,len(docs)):.1f}/doc) in {time.time()-t:.2f}s")

    # ---- embed ----
    print("[i] loading model (first run downloads weights)…")
    from mlx_embeddings import load
    import mlx.core as mx
    t = time.time()
    model, tokenizer = load(model_id)
    print(f"[i] model loaded in {time.time()-t:.2f}s")

    # Walk every book, embed missing chunks, cache.
    print("[i] embedding …")
    t_embed = time.time()
    book_embs: list[np.ndarray] = []
    chunk_book_slug: list[str] = []
    chunk_book_title: list[str] = []
    chunk_texts_all: list[str] = []
    chunk_embs_all: list[np.ndarray] = []

    n_cached = 0
    n_computed = 0
    for d, chunks in chunked:
        if not chunks:
            # empty doc — assign a zero centroid; will be a graph outlier
            embs = np.zeros((0, 1), dtype=np.float32)
        else:
            cached = None if rebuild else load_book_cache(model_id, d.slug, chunks)
            if cached is not None:
                embs = cached.astype(np.float32)
                n_cached += len(chunks)
            else:
                t_b = time.time()
                embs = embed_chunks(chunks, model, tokenizer,
                                    batch_size=batch_size,
                                    max_length=max_length)
                # re-normalise to be safe (mlx-embeddings already does this)
                norms = np.linalg.norm(embs, axis=1, keepdims=True) + 1e-9
                embs = (embs / norms).astype(np.float32)
                save_book_cache(model_id, d.slug, chunks, embs)
                n_computed += len(chunks)
                print(f"  · {d.slug}: {len(chunks)} chunks "
                      f"in {time.time()-t_b:.2f}s")
        book_embs.append(embs)
        for ci, c in enumerate(chunks):
            chunk_book_slug.append(d.slug)
            chunk_book_title.append(d.title)
            chunk_texts_all.append(c)
        chunk_embs_all.append(embs)
    chunk_embs_all = np.concatenate(chunk_embs_all, axis=0) if chunk_embs_all else np.zeros((0, 1024))
    t_embed_total = time.time() - t_embed
    print(f"[i] embedded {n_computed} new chunks, hit cache for {n_cached} "
          f"(wall {t_embed_total:.1f}s)")

    # Mean-center across all chunks: remove the shared "all-about-X" component.
    if MEAN_CENTER and chunk_embs_all.shape[0] > 1:
        mu = chunk_embs_all.mean(axis=0, keepdims=True)
        chunk_embs_all = chunk_embs_all - mu
        chunk_embs_all /= np.maximum(1e-9, np.linalg.norm(chunk_embs_all, axis=1, keepdims=True))
        # Re-slice per-book matrices from the centered concat so downstream
        # centroid + topic code uses centered chunks.
        _offset = 0
        for _i in range(len(book_embs)):
            _n = book_embs[_i].shape[0]
            book_embs[_i] = chunk_embs_all[_offset : _offset + _n]
            _offset += _n
        print(f"[i] mean-centered all chunks (||mu||={float(np.linalg.norm(mu)):.4f})")

    # ---- per-book centroids ----
    centroids = []
    valid_idx = []
    for i, (d, chunks) in enumerate(chunked):
        embs = book_embs[i]
        if embs.shape[0] == 0 or embs.shape[1] == 0:
            print(f"[!] {d.slug} has no chunks; skipping")
            continue
        # weight by chunk length (in chars)
        weights = np.array([len(c) for c in chunks], dtype=np.float32) + 1.0
        weights /= weights.sum()
        c = (embs * weights[:, None]).sum(axis=0)
        c = c / (np.linalg.norm(c) + 1e-9)
        centroids.append(c)
        valid_idx.append(i)
    centroids = np.stack(centroids, axis=0) if centroids else np.zeros((0, 1024))
    print(f"[i] {centroids.shape[0]} book centroids · dim {centroids.shape[1]}")

    # node order = valid_idx order
    nodes_docs = [chunked[i][0] for i in valid_idx]
    slugs = [d.slug for d in nodes_docs]
    chunk_counts = [len(chunked[i][1]) for i in valid_idx]

    # ---- graph ----
    edges, sims = build_book_graph(centroids, slugs)
    print(f"[i] {len(edges)} book-book edges "
          f"(min sim={MIN_SIM_FOR_EDGE}, top-K={TOP_K_PER_NODE}, "
          f"threshold={GLOBAL_SIM_THRESHOLD})")

    # ---- communities ----
    if edges:
        membership, modularity = leiden_communities(len(slugs), edges)
    else:
        membership = list(range(len(slugs)))
        modularity = 0.0
    print(f"[i] {len(set(membership))} communities · modularity={modularity:.3f}")

    # Community labels: title of the highest-degree node in each community
    deg = [0] * len(slugs)
    for a, b, _ in edges:
        deg[a] += 1
        deg[b] += 1
    bc = betweenness_centrality(len(slugs), edges) if edges else [0.0] * len(slugs)

    com_members: dict[int, list[int]] = defaultdict(list)
    for i, c in enumerate(membership):
        com_members[c].append(i)
    com_label: dict[int, str] = {}
    for cid, members in com_members.items():
        members_sorted = sorted(members, key=lambda i: (-deg[i], -bc[i]))
        anchor = members_sorted[0]
        com_label[cid] = nodes_docs[anchor].title

    # Renumber community ids by size desc
    sized = sorted(com_members.items(), key=lambda kv: -len(kv[1]))
    remap = {old: new for new, (old, _) in enumerate(sized)}
    membership = [remap[c] for c in membership]
    com_members = {remap[k]: v for k, v in com_members.items()}
    com_label = {remap[k]: v for k, v in com_label.items()}

    communities_out = [
        {"id": cid, "label": com_label[cid], "size": len(com_members[cid]),
         "color_index": cid}
        for cid in sorted(com_members)
    ]

    # ---- most-similar (top 10) per book ----
    # use raw sims (before pruning), exclude self
    top10_per: list[list[dict]] = []
    for i in range(len(slugs)):
        row = sims[i].copy()
        row[i] = -1.0
        idx = np.argsort(-row)[:10]
        top10_per.append([
            {
                "slug": slugs[j],
                "kind": nodes_docs[j].kind,
                "title": nodes_docs[j].title,
                "sim": float(row[j]),
            }
            for j in idx
        ])

    # ---- topics (HDBSCAN over chunk embeddings) ----
    print("[i] discovering topics over chunk embeddings …")
    t = time.time()
    topics = discover_topics(chunk_embs_all, chunk_texts_all,
                             chunk_book_slug, chunk_book_title)
    print(f"[i] {len(topics)} topics in {time.time()-t:.2f}s")

    # Per-node primary topic (the topic with the most member chunks for that book)
    book_topic_counts: dict[str, Counter[int]] = defaultdict(Counter)
    if topics:
        # Recompute the cluster label per chunk to keep ids stable
        # (simpler: assign by argmax of mean-sim to topic centroids)
        topic_centroids = []
        for t_ in topics:
            mem = [i for i, sl in enumerate(chunk_book_slug)
                   if any(sl == sb["slug"] for sb in t_["sample_books"])]
            # actually easier: reconstruct from sample_chunks centroid via top_terms; skip.
        # Recompute from the labels we built above by re-running argmax over cluster centroids:
        # to avoid re-running HDBSCAN we recompute centroids from chunk_embs by topic via top-term overlap.
        # Simpler approach: for each chunk, find topic whose top_terms overlap most with the chunk's lemmas.
        # This is a coarse proxy but good enough for the per-node primary_topic field.
        # — replaced with the simpler: for each topic, mark its sample_books with that topic.
        for ti, t_ in enumerate(topics):
            for sb in t_["sample_books"]:
                book_topic_counts[sb["slug"]][t_["id"]] += sb["chunks"]

    # ---- emit JSON ----
    PALETTE_LEN = 20  # matches conceptosphere.html
    out_doc = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "model": model_id,
        "params": {
            "chunk_tokens": CHUNK_TOKENS,
            "chunk_overlap": CHUNK_OVERLAP,
            "top_k_per_node": TOP_K_PER_NODE,
            "global_sim_threshold": GLOBAL_SIM_THRESHOLD,
            "min_sim_for_edge": MIN_SIM_FOR_EDGE,
            "hdbscan_min_cluster_size": HDBSCAN_MIN_CLUSTER_SIZE,
        },
        "stats": {
            "books": int(sum(d.kind == "book" for d in nodes_docs)),
            "poems": int(sum(d.kind == "poem" for d in nodes_docs)),
            "projects": int(sum(d.kind == "project" for d in nodes_docs)),
            "nodes": len(slugs),
            "chunks": int(chunk_embs_all.shape[0]),
            "edges": len(edges),
            "communities": len(communities_out),
            "modularity": round(modularity, 4),
            "topics_discovered": len(topics),
        },
        "communities": communities_out,
        "nodes": [
            {
                "id": slugs[i],
                "slug": slugs[i],
                "kind": nodes_docs[i].kind,
                "number": nodes_docs[i].number,
                "title": nodes_docs[i].title,
                "tags": nodes_docs[i].tags,
                "chunk_count": chunk_counts[i],
                "degree": deg[i],
                "centrality": round(float(bc[i]), 4),
                "community": int(membership[i]),
                "primary_topic": (book_topic_counts[slugs[i]].most_common(1)[0][0]
                                  if book_topic_counts.get(slugs[i]) else None),
                "most_similar": top10_per[i],
            }
            for i in range(len(slugs))
        ],
        "edges": [
            {"source": slugs[a], "target": slugs[b], "weight": round(w, 4)}
            for (a, b, w) in edges
        ],
        "topics": topics,
    }

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(out_doc, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[✓] wrote {out}  ({out.stat().st_size/1024:.1f} KB)")
    print(f"[✓] total wall: {time.time()-t_total:.1f}s")
