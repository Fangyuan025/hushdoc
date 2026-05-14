"""
v0.5.0: in-process BM25 index that sits alongside the Chroma vector store.

We keep the SAME chunk text and SAME metadata in both indexes so a candidate
list from BM25 can be merged with a candidate list from dense retrieval (via
Reciprocal Rank Fusion) without any cross-referencing. The BM25 corpus
lives in memory — on cold start we pull every chunk's text + metadata out
of Chroma in one call and tokenize once. ``rank-bm25`` is pure-Python so
the rebuild is fast (a few hundred ms for ~10k chunks) and there is no
on-disk format to keep in sync.

Tokenization is intentionally simple: lower-case + ``\\w+``. We don't
stem, don't strip stop-words (BM25 already down-weights them via IDF), and
don't try to be clever about CJK — for queries that contain Chinese the
caller can drop back to ``HUSHDOC_RETRIEVAL_MODE=dense``, which is the
right tool for that job anyway. For English / European-language queries
this gives a useful keyword channel that catches exact-name / exact-number
matches the bi-encoder regularly misses (filenames, model versions, error
codes, etc.).
"""
from __future__ import annotations

import logging
import re
import threading
from typing import Dict, List, Optional, Sequence, Tuple

from langchain_core.documents import Document

logger = logging.getLogger("bm25_index")


# Lower-case, then keep alphanumeric runs of length >= 1. Underscores
# stay so identifiers like ``my_var`` count as one token. Pure regex —
# no language-specific behaviour, no external resources to load.
_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+", re.UNICODE)


def _tokenize(text: str) -> List[str]:
    if not text:
        return []
    return _TOKEN_RE.findall(text.lower())


class BM25Index:
    """In-memory BM25 over the same chunk corpus as the Chroma collection.

    The index is rebuilt lazily — adds / removes / resets mark the index
    dirty, and the next search rebuilds before querying. This batches the
    common case where ingest loops call ``add_documents`` many times in
    a row: one rebuild instead of N."""

    def __init__(self) -> None:
        # Each row: (filename, chunk_index, tokens, page_content, metadata).
        # We key by (filename, chunk_index) when present, falling back to
        # the row index when chunk_index is missing. Two rows with the
        # same key overwrite — matches how Chroma re-ingest behaves.
        self._rows: List[Tuple[Optional[str], Optional[int], List[str], str, Dict]] = []
        self._key_to_row: Dict[Tuple[Optional[str], Optional[int]], int] = {}
        self._bm25 = None  # rank_bm25.BM25Okapi, built on demand
        self._dirty: bool = True
        self._lock = threading.Lock()

    # --------------------------------------------------------- bookkeeping
    @staticmethod
    def _row_key(meta: Dict) -> Tuple[Optional[str], Optional[int]]:
        fn = meta.get("filename")
        ci = meta.get("chunk_index")
        try:
            ci_int = int(ci) if ci is not None else None
        except (TypeError, ValueError):
            ci_int = None
        return (fn, ci_int)

    def _mark_dirty(self) -> None:
        self._dirty = True
        self._bm25 = None

    # ------------------------------------------------------------- writes
    def add_documents(self, docs: Sequence[Document]) -> int:
        """Upsert chunks into the corpus. Returns the number of new rows
        (existing keys overwrite in place)."""
        if not docs:
            return 0
        new = 0
        with self._lock:
            for d in docs:
                meta = dict(d.metadata or {})
                key = self._row_key(meta)
                tokens = _tokenize(d.page_content)
                row = (key[0], key[1], tokens, d.page_content, meta)
                if key in self._key_to_row and key != (None, None):
                    self._rows[self._key_to_row[key]] = row
                else:
                    self._key_to_row[key] = len(self._rows)
                    self._rows.append(row)
                    new += 1
            self._mark_dirty()
        return new

    def delete_by_filename(self, filename: str) -> int:
        """Drop every row whose filename matches. Returns the count
        removed. O(N) — we rebuild the row list."""
        if not filename:
            return 0
        with self._lock:
            kept: List = []
            new_key_map: Dict = {}
            removed = 0
            for row in self._rows:
                if row[0] == filename:
                    removed += 1
                    continue
                key = (row[0], row[1])
                new_key_map[key] = len(kept)
                kept.append(row)
            if removed:
                self._rows = kept
                self._key_to_row = new_key_map
                self._mark_dirty()
        return removed

    def reset(self) -> None:
        """Drop everything. Matches the semantics of LocalVectorStore.reset()."""
        with self._lock:
            self._rows = []
            self._key_to_row = {}
            self._mark_dirty()

    def rebuild_from(self, docs: Sequence[Document]) -> int:
        """Wipe + bulk-load. Used on cold start to materialise the index
        from whatever's already in Chroma. Returns the row count."""
        with self._lock:
            self._rows = []
            self._key_to_row = {}
            for d in docs:
                meta = dict(d.metadata or {})
                key = self._row_key(meta)
                tokens = _tokenize(d.page_content)
                self._key_to_row[key] = len(self._rows)
                self._rows.append((key[0], key[1], tokens, d.page_content, meta))
            self._mark_dirty()
        logger.info("BM25 index rebuilt: %d chunks.", len(self._rows))
        return len(self._rows)

    # -------------------------------------------------------------- reads
    def __len__(self) -> int:
        return len(self._rows)

    def _ensure_built(self) -> None:
        if not self._dirty and self._bm25 is not None:
            return
        # Imported lazily so machines without rank-bm25 installed can
        # still run dense-only. The retrieval-mode chooser surfaces a
        # log line rather than crashing if the import fails.
        try:
            from rank_bm25 import BM25Okapi
        except ImportError:
            logger.warning(
                "rank-bm25 not installed; BM25 search will return nothing."
            )
            self._bm25 = None
            self._dirty = False
            return
        corpus = [row[2] for row in self._rows]
        if not corpus:
            self._bm25 = None
        else:
            # BM25Okapi requires non-empty token lists for every doc;
            # substitute a sentinel for the rare empty chunk so we don't
            # get a divide-by-zero from the IDF computation.
            corpus = [c if c else ["__empty__"] for c in corpus]
            self._bm25 = BM25Okapi(corpus)
        self._dirty = False

    def search(
        self,
        query: str,
        k: int = 10,
        filenames: Optional[Sequence[str]] = None,
    ) -> List[Tuple[Document, float]]:
        """Top-k BM25 hits. Returns (Document, score) pairs sorted by
        score descending. ``filenames`` restricts the result set to that
        scope (we run BM25 over the full corpus then filter — k is the
        post-filter cap so callers get the budget they asked for)."""
        if k <= 0:
            return []
        with self._lock:
            self._ensure_built()
            if self._bm25 is None or not self._rows:
                return []
            qtoks = _tokenize(query)
            if not qtoks:
                return []
            scores = self._bm25.get_scores(qtoks)
            # Score 0 means no token overlap at all -- those are noise,
            # drop them rather than pad the result with zero-scored rows.
            scope = set(filenames) if filenames else None
            ranked = sorted(
                (
                    (i, float(scores[i]))
                    for i in range(len(self._rows))
                    if scores[i] > 0
                    and (scope is None or self._rows[i][0] in scope)
                ),
                key=lambda t: -t[1],
            )[:k]
            out: List[Tuple[Document, float]] = []
            for idx, score in ranked:
                _, _, _, content, meta = self._rows[idx]
                out.append((Document(page_content=content, metadata=dict(meta)), score))
            return out


# ---------------------------------------------------------------------------
# Reciprocal Rank Fusion
# ---------------------------------------------------------------------------
def reciprocal_rank_fusion(
    runs: Sequence[Sequence[Document]],
    k: int = 6,
    rrf_k: int = 60,
) -> List[Tuple[Document, float, List[int]]]:
    """Fuse multiple ranked lists via RRF.

    Each ``runs[i]`` is one ranked list (best first). The score of a
    document in the fused output is ``sum_i 1 / (rrf_k + rank_i)`` over
    every list it appears in (``rank_i`` is 1-indexed). ``rrf_k=60`` is
    the value from the original Cormack-Clarke-Buettcher paper; it
    smooths out the difference between rank 1 and rank 2 so a single
    list can't dominate the fusion.

    Returns ``[(Document, fused_score, [rank_in_each_run]), ...]`` where
    ``rank_in_each_run[i]`` is the 1-indexed rank in ``runs[i]`` or 0 if
    the document didn't appear there. The caller uses that to tag each
    fused candidate with which retrieval channel(s) surfaced it (the
    ``source`` field in the retrieval trace).
    """
    # Dedupe key: (filename, chunk_index) when present, else the raw
    # text. Two retrievers returning the same chunk should fuse to one
    # entry, not duplicate.
    def _key(d: Document) -> Tuple:
        meta = d.metadata or {}
        fn = meta.get("filename")
        ci = meta.get("chunk_index")
        if fn is not None and ci is not None:
            return (fn, ci)
        return ("text", d.page_content[:200])

    aggregate: Dict[Tuple, Dict] = {}
    n_runs = len(runs)
    for run_idx, run in enumerate(runs):
        for rank, doc in enumerate(run, start=1):
            key = _key(doc)
            entry = aggregate.setdefault(
                key,
                {
                    "doc": doc,
                    "score": 0.0,
                    "ranks": [0] * n_runs,
                },
            )
            entry["score"] += 1.0 / (rrf_k + rank)
            entry["ranks"][run_idx] = rank
    fused = sorted(aggregate.values(), key=lambda e: -e["score"])[:k]
    return [(e["doc"], e["score"], e["ranks"]) for e in fused]
