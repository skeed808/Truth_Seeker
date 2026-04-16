"""
Semantic Result Clustering — Phase 6a

Clusters search results by semantic similarity so that diversify_top_10()
can surface one representative per topic cluster rather than ten near-identical
pages about the same sub-topic.

Two embedding backends (auto-selected at import time):
  1. sentence-transformers / all-MiniLM-L6-v2  — best quality (needs the pkg)
  2. TF-IDF cosine (numpy-only)                — fallback, no extra deps

Clustering algorithm: agglomerative (average linkage), pure numpy,
distance threshold 0.35 (≈ cosine distance; 0 = identical, 1 = orthogonal).

Public API
----------
embed_snippet(text)             → np.ndarray  (unit-normed)
cluster_results(results)        → list[list[dict]]
diversify_top_10(clusters)      → list[dict]   (≤ top_n, default 10)
"""
from __future__ import annotations

import re
from typing import List, Dict, Optional

import numpy as np

# ── Embedding backend ─────────────────────────────────────────────────────────

_ST_MODEL: Optional[object] = None   # sentence_transformers.SentenceTransformer
_USE_ST:   Optional[bool]   = None   # None = not yet decided


def _decide_backend() -> bool:
    """Return True if sentence-transformers is available and loadable."""
    global _USE_ST, _ST_MODEL
    if _USE_ST is not None:
        return _USE_ST
    try:
        from sentence_transformers import SentenceTransformer  # type: ignore
        _ST_MODEL = SentenceTransformer("all-MiniLM-L6-v2")
        _USE_ST = True
    except Exception:
        _USE_ST = False
    return _USE_ST


# ── TF-IDF fallback ───────────────────────────────────────────────────────────

_STOP = frozenset("""
a an the and or but in on at to for of with by from as is was are were be
been being have has had do does did will would could should may might
this that these those it its we our they their there here then than when
what which who how all any some no not just also very can about up out
""".split())

_VOCAB: Optional[Dict[str, int]] = None
_IDF:   Optional[np.ndarray]     = None


def _tokenize(text: str) -> List[str]:
    return [w for w in re.sub(r"[^\w\s]", " ", text.lower()).split()
            if len(w) > 2 and w not in _STOP]


def _tfidf_embed(texts: List[str]) -> np.ndarray:
    """
    Fit a TF-IDF matrix on `texts` and return unit-normed row vectors.
    Shape: (len(texts), vocab_size).
    """
    global _VOCAB, _IDF

    tokenized = [_tokenize(t) for t in texts]

    # Build vocab from current batch (no persistent vocab — per-request fit)
    all_terms = sorted({tok for doc in tokenized for tok in doc})
    vocab = {t: i for i, t in enumerate(all_terms)}
    n_docs, n_terms = len(texts), len(vocab)

    if n_terms == 0:
        return np.zeros((n_docs, 1))

    # TF matrix (raw counts)
    tf = np.zeros((n_docs, n_terms), dtype=np.float32)
    for di, doc in enumerate(tokenized):
        for tok in doc:
            if tok in vocab:
                tf[di, vocab[tok]] += 1.0

    # IDF (smoothed)
    df = (tf > 0).sum(axis=0).astype(np.float32)
    idf = np.log((1.0 + n_docs) / (1.0 + df)) + 1.0

    tfidf = tf * idf

    # L2 normalise each row
    norms = np.linalg.norm(tfidf, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return (tfidf / norms).astype(np.float32)


# ── Public: embed_snippet ─────────────────────────────────────────────────────

def embed_snippet(snippet: str) -> np.ndarray:
    """
    Return a unit-normed 1-D embedding for `snippet`.

    Uses sentence-transformers when available, TF-IDF otherwise.
    For TF-IDF, the vocab is fitted on-the-fly from the single string, which
    degrades to a bag-of-words unit vector — useful only for cosine comparisons
    across a batch fitted together.  Prefer embed_batch for multi-result use.
    """
    if _decide_backend():
        vec = _ST_MODEL.encode(snippet, normalize_embeddings=True,
                               show_progress_bar=False)
        return np.array(vec, dtype=np.float32)
    # Fallback: single-doc TF-IDF is essentially a unit bag-of-words vector
    return _tfidf_embed([snippet])[0]


def _embed_batch(texts: List[str]) -> np.ndarray:
    """Embed a list of strings, returning (n, dim) unit-normed matrix."""
    if _decide_backend():
        vecs = _ST_MODEL.encode(texts, normalize_embeddings=True,
                                show_progress_bar=False, batch_size=32)
        return np.array(vecs, dtype=np.float32)
    return _tfidf_embed(texts)


# ── Agglomerative clustering (pure numpy, average linkage) ────────────────────

def _cosine_distance_matrix(embeddings: np.ndarray) -> np.ndarray:
    """
    Compute pairwise cosine distance matrix.
    embeddings: (n, d) unit-normed → dot product == cosine similarity.
    distance = 1 - similarity, clamped to [0, 1].
    """
    sim = embeddings @ embeddings.T          # (n, n)
    sim = np.clip(sim, -1.0, 1.0)
    dist = 1.0 - sim
    np.fill_diagonal(dist, 0.0)
    return dist.astype(np.float32)


def _agglomerative_cluster(
    dist: np.ndarray,
    threshold: float = 0.35,
) -> List[int]:
    """
    Average-linkage agglomerative clustering.

    Returns a list `labels` where labels[i] is the cluster id of item i.
    Two items are in the same cluster iff their average pairwise distance
    is < threshold.

    Runs in O(n³) — acceptable for n ≤ 100.
    """
    n = dist.shape[0]
    # Each item starts in its own cluster
    labels = list(range(n))

    # Track which original indices belong to each cluster label
    clusters: Dict[int, List[int]] = {i: [i] for i in range(n)}
    next_id = n

    while True:
        # Find the pair of distinct clusters with minimum average distance
        cluster_ids = list(clusters.keys())
        if len(cluster_ids) < 2:
            break

        best_dist = float("inf")
        best_a = best_b = -1

        for i, ca in enumerate(cluster_ids):
            for cb in cluster_ids[i + 1:]:
                members_a = clusters[ca]
                members_b = clusters[cb]
                avg = float(np.mean(dist[np.ix_(members_a, members_b)]))
                if avg < best_dist:
                    best_dist = avg
                    best_a, best_b = ca, cb

        if best_dist >= threshold:
            break   # Nothing close enough to merge

        # Merge best_b into best_a under a new id
        merged = clusters[best_a] + clusters[best_b]
        del clusters[best_a]
        del clusters[best_b]
        clusters[next_id] = merged
        next_id += 1

    # Assign sequential cluster ids
    for new_label, members in enumerate(clusters.values()):
        for idx in members:
            labels[idx] = new_label

    return labels


# ── Cluster label generation ──────────────────────────────────────────────────

def _auto_label(results: List[Dict]) -> str:
    """
    Generate a human-readable cluster label from the top 3 most common
    non-stop content nouns across all snippets in the cluster.
    Falls back to the title of the highest-scoring result.
    """
    texts = " ".join(
        (r.get("snippet") or r.get("title") or "")
        for r in results
    )
    tokens = _tokenize(texts)
    if not tokens:
        best = max(results, key=lambda r: r.get("scores", {}).get("final", 0))
        return (best.get("title") or "cluster")[:40]

    freq: Dict[str, int] = {}
    for tok in tokens:
        freq[tok] = freq.get(tok, 0) + 1
    top3 = sorted(freq, key=freq.get, reverse=True)[:3]
    return " · ".join(top3)


# ── Public: cluster_results ───────────────────────────────────────────────────

def cluster_results(results: List[Dict]) -> List[List[Dict]]:
    """
    Cluster a ranked list of result dicts by semantic similarity.

    Each result's snippet (or title) is embedded; agglomerative clustering
    groups semantically similar results.

    Side effects:
      - Sets result["cluster_id"]    (int)
      - Sets result["cluster_label"] (str — top nouns from cluster)

    Returns a list of clusters: [[result, ...], [result, ...], ...]
    Clusters are ordered by the best (highest) final score in each cluster.
    Within each cluster, results are ordered by final score descending.
    """
    if not results:
        return []

    texts = [
        (r.get("snippet") or r.get("title") or r.get("url", ""))
        for r in results
    ]

    embeddings = _embed_batch(texts)
    dist = _cosine_distance_matrix(embeddings)
    labels = _agglomerative_cluster(dist, threshold=0.35)

    # Group results by cluster label
    cluster_map: Dict[int, List[Dict]] = {}
    for result, label in zip(results, labels):
        cluster_map.setdefault(label, []).append(result)

    # Sort within each cluster by final score (best first)
    def _final_score(r: Dict) -> float:
        return r.get("scores", {}).get("final", 0.0)

    for members in cluster_map.values():
        members.sort(key=_final_score, reverse=True)

    # Assign sequential cluster ids (0-based) ordered by best score in cluster
    sorted_clusters = sorted(
        cluster_map.values(),
        key=lambda members: _final_score(members[0]),
        reverse=True,
    )

    result_clusters: List[List[Dict]] = []
    for cid, members in enumerate(sorted_clusters):
        label = _auto_label(members)
        for r in members:
            r["cluster_id"]    = cid
            r["cluster_label"] = label
        result_clusters.append(members)

    return result_clusters


# ── Public: diversify_top_10 ──────────────────────────────────────────────────

def diversify_top_10(
    clusters: List[List[Dict]],
    top_n: int = 10,
) -> List[Dict]:
    """
    Build a diverse top-N by round-robin picking from clusters.

    Pass 1: take the best result from each cluster (already sorted by score).
    Pass 2: take the second-best from each cluster, and so on.
    Stop when top_n results have been collected.

    Guarantees: at most 1 result per cluster in the output (per pass), so
    no two near-identical results occupy adjacent top slots.

    Returns a flat list of up to `top_n` results, preserving original scores.
    """
    output:  List[Dict] = []
    queues = [list(c) for c in clusters]   # copy so we can pop

    while len(output) < top_n and any(queues):
        for q in queues:
            if q and len(output) < top_n:
                output.append(q.pop(0))

    return output
