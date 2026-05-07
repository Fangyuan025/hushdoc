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
    """
    global _model, _load_attempted
    if _model is not None or _load_attempted:
        return _model
    _load_attempted = True
    try:
        from sentence_transformers import CrossEncoder
        logger.info("Loading cross-encoder reranker: %s (CPU)", model_name)
        _model = CrossEncoder(model_name, device="cpu", max_length=512)
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
    top ``top_k``. Returns the bi-encoder order untouched if:
      * the candidate set is already at-or-below the budget,
      * the cross-encoder failed to load, or
      * the predict call raised.
    """
    if not docs:
        return []
    if len(docs) <= top_k:
        return list(docs)

    model = _load(model_name)
    if model is None:
        return list(docs)[:top_k]

    pairs = [(query, d.page_content) for d in docs]
    try:
        scores = model.predict(pairs, show_progress_bar=False)
    except Exception:
        logger.exception("Cross-encoder predict failed; returning bi-encoder order.")
        return list(docs)[:top_k]

    indexed = sorted(
        zip(scores, docs), key=lambda x: float(x[0]), reverse=True,
    )
    return [d for _, d in indexed[:top_k]]
