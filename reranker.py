"""
Cross-encoder reranker for RAG retrieval, plus v0.6.0 post-rerank
helpers: adaptive top-k truncation and MMR diversification.

Bi-encoder similarity (the SentenceTransformer-based vector search the
ChromaDB store uses) compresses each chunk to a 384-d vector once and
retrieves by cosine similarity. That's fast but imprecise: relevance
gets blurred through the bottleneck of two independent encodings.

A cross-encoder reads ``(query, candidate)`` pairs together and outputs
a single relevance score per pair. Much higher precision but O(N) per
query — too slow as a primary index.

Standard recipe (and what we do here): fetch a wider candidate set by
bi-encoder similarity (e.g. k * 3), then re-rank with the cross-encoder
and keep the top-k.

Default model is ``cross-encoder/ms-marco-MiniLM-L-6-v2`` — only ~80 MB,
runs comfortably on CPU, gives a measurable precision bump over plain
similarity for both English and (with caveats) Chinese queries.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional, Sequence

from langchain_core.documents import Document

logger = logging.getLogger("reranker")

DEFAULT_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"

_model = None
_load_attempted = False


def _load(model_name: str = DEFAULT_MODEL):
    """Lazy-load the cross-encoder. Caches a single instance.
    Falls through to None on failure so callers can degrade gracefully.

    v0.5.0: device matches the embedding model's choice (CUDA when
    available, CPU otherwise, HUSHDOC_EMBED_DEVICE override). Pre-v0.5.0
    was hardcoded ``cpu`` which made the cross-encoder a noticeable
    fraction of per-query latency even with a GPU sitting idle.
    """
    global _model, _load_attempted
    if _model is not None or _load_attempted:
        return _model
    _load_attempted = True
    try:
        from sentence_transformers import CrossEncoder
        # Lazy import vector_store so reranker stays importable in
        # contexts where chroma isn't available (smoke tests etc.).
        from vector_store import pick_embed_device
        device = pick_embed_device()
        logger.info(
            "Loading cross-encoder reranker: %s (device=%s)", model_name, device,
        )
        _model = CrossEncoder(model_name, device=device, max_length=512)
        logger.info("Cross-encoder ready.")
    except Exception:
        logger.exception("Cross-encoder load failed; reranker disabled.")
        _model = None
    return _model


def rerank(
    query: str,
    docs: Sequence[Document],
    top_k: int,
    model_name: str = DEFAULT_MODEL,
) -> List[Document]:
    """Sort ``docs`` by cross-encoder relevance to ``query`` and keep the
    top ``top_k``. See ``rerank_with_trace`` for the version that also
    returns per-candidate scores -- this thin wrapper exists for the
    callers (non-streaming chain path) that don't care about the trace.
    """
    docs_out, _trace = rerank_with_trace(query, docs, top_k, model_name)
    return docs_out


def rerank_with_trace(
    query: str,
    docs: Sequence[Document],
    top_k: int,
    model_name: str = DEFAULT_MODEL,
) -> tuple[List[Document], List[dict]]:
    """Like ``rerank`` but also returns a per-candidate trace usable by
    the UI's Retrieval-trace panel. Each trace entry:

        {
          "filename":     str,    # from chunk metadata
          "page":         int|None,
          "chunk_index":  int|None,
          "snippet":      str,    # first 200 chars of page_content
          "rank_before":  int,    # 0-indexed bi-encoder rank
          "rank_after":   int|None,  # 0-indexed final rank, or None if
                                     # this candidate was dropped from
                                     # the top-k
          "score_after":  float|None,# cross-encoder score; None when no
                                     # rerank ran (passthrough mode)
        }

    The trace covers EVERY bi-encoder candidate (not just the kept ones)
    so the UI can show 'why was this candidate filtered out'."""
    if not docs:
        return [], []

    def _entry(idx: int, d: Document) -> dict:
        m = d.metadata or {}
        # v0.5.0: ``source`` carries which retrieval channel surfaced
        # this candidate -- 'dense' / 'bm25' / 'both' for hybrid runs,
        # or the static fallback set by the chain for dense-only modes.
        # The trace panel renders it as a small chip next to filename.
        source = m.get("_rrf_source", "")
        return {
            "filename": m.get("filename", ""),
            "page": m.get("page"),
            "chunk_index": m.get("chunk_index"),
            "snippet": (d.page_content or "")[:200],
            "rank_before": idx,
            "rank_after": None,
            "score_after": None,
            "source": source,
        }

    trace = [_entry(i, d) for i, d in enumerate(docs)]

    # Trivial passthrough: every candidate fits within the budget.
    if len(docs) <= top_k:
        for i, entry in enumerate(trace):
            entry["rank_after"] = i
        return list(docs), trace

    model = _load(model_name)
    if model is None:
        # Reranker unavailable -- keep the bi-encoder top_k unchanged.
        for i in range(top_k):
            trace[i]["rank_after"] = i
        return list(docs)[:top_k], trace

    pairs = [(query, d.page_content) for d in docs]
    try:
        scores = model.predict(pairs, show_progress_bar=False)
    except Exception:
        logger.exception("Cross-encoder predict failed; returning bi-encoder order.")
        for i in range(top_k):
            trace[i]["rank_after"] = i
        return list(docs)[:top_k], trace

    # Annotate every candidate with its cross-encoder score, then sort
    # descending. The kept docs get rank_after set; the rest stay None.
    for i, s in enumerate(scores):
        trace[i]["score_after"] = float(s)

    order = sorted(range(len(docs)), key=lambda i: -float(scores[i]))
    new_docs: List[Document] = []
    for new_rank, orig_idx in enumerate(order):
        if new_rank < top_k:
            trace[orig_idx]["rank_after"] = new_rank
            new_docs.append(docs[orig_idx])
    return new_docs, trace


# ---------------------------------------------------------------------------
# v0.6.0 post-rerank shaping: adaptive truncation + MMR diversification
# ---------------------------------------------------------------------------
import re
from langchain_core.documents import Document  # type: ignore  # already imported

_MMR_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+|[一-鿿]", re.UNICODE)


def _tokset(text: str) -> set:
    if not text:
        return set()
    return {t.lower() for t in _MMR_TOKEN_RE.findall(text)}


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def adaptive_keep(
    docs: List[Document],
    trace: List[dict],
    *,
    min_keep: int = 3,
    keep_ratio: float = 0.3,
) -> tuple[List[Document], List[dict]]:
    """v0.6.0: drop the rerank tail when cross-encoder scores fall off
    a cliff. Concretely: keep doc i iff its score is within
    ``keep_ratio`` of the top doc's range (shifted to non-negative
    space first because cross-encoder scores can be negative on
    irrelevant pairs). Always keep at least ``min_keep`` so the
    chain never starves on a single-chunk prompt.

    Defaults are deliberately conservative -- the early v0.6.0
    iteration shipped with min_keep=2 / ratio=0.5 and routinely cut
    a 6-doc pool down to 2, after which the answer model had too
    little to cite and the strict-citation filter left zero sources
    on screen. 3 / 0.3 keeps enough breathing room while still
    dropping the clearly-noise tail.

    Operates only on the docs whose rerank_after is not None (i.e. the
    rerank-kept set). Trace rows whose rank_after gets dropped go from
    ``rank_after=<n>`` back to ``rank_after=None`` so the UI's
    Retrieval-trace tab shows them as "dropped by adaptive truncation"
    rather than mysteriously vanishing.

    A 'no-op' case is when no rerank ran (scores all None / equal):
    return docs unchanged."""
    if len(docs) <= min_keep:
        return docs, trace
    # Trace scores are aligned 1:1 with the original candidate list (the
    # pre-rerank order). We need the scores ordered by current rerank
    # rank — i.e. matching ``docs``. Re-derive from the trace.
    # Build mapping from doc-content prefix -> trace entry so we can
    # look scores up without relying on identity.
    score_for_doc: List[Optional[float]] = []
    for d in docs:
        s: Optional[float] = None
        for e in trace:
            if e.get("rank_after") is None:
                continue
            # Match on the chunk_index + filename since that's the
            # stable key the rerank trace already uses.
            if (
                e.get("filename") == (d.metadata or {}).get("filename")
                and str(e.get("chunk_index")) == str(
                    (d.metadata or {}).get("chunk_index")
                )
            ):
                s = e.get("score_after")
                break
        score_for_doc.append(s)
    if not any(isinstance(s, (int, float)) for s in score_for_doc):
        return docs, trace
    top_score = max(s for s in score_for_doc if isinstance(s, (int, float)))
    # Cross-encoder scores can be negative on irrelevant pairs. To make
    # the ratio behave naturally, shift everything to >= 0 before
    # comparing.
    floor = min(s for s in score_for_doc if isinstance(s, (int, float)))
    shifted_top = top_score - floor
    if shifted_top <= 0:
        return docs, trace
    threshold = floor + keep_ratio * shifted_top
    kept_docs: List[Document] = []
    kept_indices_in_orig: List[int] = []
    for i, (d, s) in enumerate(zip(docs, score_for_doc)):
        if isinstance(s, (int, float)) and s < threshold and len(kept_docs) >= min_keep:
            continue
        kept_docs.append(d)
        kept_indices_in_orig.append(i)
    # Update trace: any rank_after that points past the kept count
    # becomes None.
    n_kept = len(kept_docs)
    for e in trace:
        ra = e.get("rank_after")
        if ra is None:
            continue
        if ra >= n_kept:
            e["rank_after"] = None
    return kept_docs, trace


def mmr_reorder(
    docs: List[Document],
    trace: List[dict],
    *,
    lambda_: float = 0.7,
) -> tuple[List[Document], List[dict]]:
    """v0.6.0: re-order ``docs`` using Maximal Marginal Relevance to
    reduce near-duplicate top-k entries.

    Score(d) for the next pick = ``lambda_ * relevance(d) -
    (1 - lambda_) * max_sim_to_already_picked(d)``. ``relevance(d)`` is
    the cross-encoder score from the rerank trace (falls back to a
    rank-based proxy when scores are missing). Similarity is token
    Jaccard over the chunk's page_content -- pure-Python, no embedding
    calls needed. ``lambda_=0.7`` (default) favours relevance over
    diversity but still rotates duplicates out of the head.

    Trace ``rank_after`` is rewritten to the new MMR order so the UI
    shows the same ordering the chain handed to the model."""
    if len(docs) <= 1:
        return docs, trace
    # Build relevance scores for each kept doc.
    rel: List[float] = []
    for d in docs:
        s: Optional[float] = None
        for e in trace:
            if (
                e.get("filename") == (d.metadata or {}).get("filename")
                and str(e.get("chunk_index")) == str(
                    (d.metadata or {}).get("chunk_index")
                )
            ):
                s = e.get("score_after")
                break
        if isinstance(s, (int, float)):
            rel.append(float(s))
        else:
            # No rerank score -- approximate from rerank rank.
            rank_after = None
            for e in trace:
                if (
                    e.get("filename") == (d.metadata or {}).get("filename")
                    and str(e.get("chunk_index")) == str(
                        (d.metadata or {}).get("chunk_index")
                    )
                ):
                    rank_after = e.get("rank_after")
                    break
            if isinstance(rank_after, int):
                rel.append(1.0 - rank_after / max(1, len(docs)))
            else:
                rel.append(0.0)
    tokens = [_tokset(d.page_content) for d in docs]

    # Greedy MMR: pick one at a time.
    n = len(docs)
    remaining = list(range(n))
    picked: List[int] = []
    while remaining:
        best_i = remaining[0]
        best_score = float("-inf")
        for i in remaining:
            if not picked:
                score = rel[i]
            else:
                max_sim = max(_jaccard(tokens[i], tokens[j]) for j in picked)
                score = lambda_ * rel[i] - (1.0 - lambda_) * max_sim
            if score > best_score:
                best_score = score
                best_i = i
        picked.append(best_i)
        remaining.remove(best_i)
    new_docs = [docs[i] for i in picked]
    # Rewrite trace rank_after to MMR order.
    new_rank_by_key: Dict[tuple, int] = {}
    for new_rank, d in enumerate(new_docs):
        key = (
            (d.metadata or {}).get("filename"),
            str((d.metadata or {}).get("chunk_index")),
        )
        new_rank_by_key[key] = new_rank
    for e in trace:
        if e.get("rank_after") is None:
            continue
        key = (e.get("filename"), str(e.get("chunk_index")))
        if key in new_rank_by_key:
            e["rank_after"] = new_rank_by_key[key]
    return new_docs, trace
