"""
Cross-encoder reranker for RAG retrieval.

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
from typing import List, Sequence

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
