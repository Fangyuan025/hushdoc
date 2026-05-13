"""
Step 2: Local Vector Database Module.

Wraps a local HuggingFace embedding model (all-MiniLM-L6-v2) and a persistent
ChromaDB collection. Provides upsert + retrieval helpers for the RAG chain.

100% offline: the embedding model is cached locally on first download and the
vector store lives on disk under ./chroma_db.
"""
from __future__ import annotations

import logging
import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever
from langchain_chroma import Chroma
from langchain_huggingface import HuggingFaceEmbeddings

logger = logging.getLogger("vector_store")
if not logger.handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    )


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DEFAULT_PERSIST_DIR = Path("./chroma_db")
DEFAULT_COLLECTION = "pdf_rag"
DEFAULT_EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"


@dataclass
class VectorStoreConfig:
    persist_directory: Path = DEFAULT_PERSIST_DIR
    collection_name: str = DEFAULT_COLLECTION
    embedding_model_name: str = DEFAULT_EMBED_MODEL
    # Run on CPU by default to stay portable; switch to "cuda" if available.
    device: str = "cpu"
    normalize_embeddings: bool = True
    model_kwargs: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Vector store wrapper
# ---------------------------------------------------------------------------
class LocalVectorStore:
    """Thin wrapper over LangChain's Chroma + HuggingFace embeddings."""

    def __init__(self, config: Optional[VectorStoreConfig] = None) -> None:
        self.config = config or VectorStoreConfig()
        self.config.persist_directory = Path(self.config.persist_directory)
        self.config.persist_directory.mkdir(parents=True, exist_ok=True)

        self._embeddings = self._init_embeddings()
        self._store = self._init_store()

    # --------------------------------------------------------------- builders
    @staticmethod
    def _warmup_torch_for_hf() -> None:
        """
        Workaround for a Windows-only segfault in transformers 5.6.x +
        huggingface_hub 1.12.x where cold-loading a sentence-transformers
        model crashes during the first network/cache resolution.
        Instantiating Docling's DocumentConverter first runs the same
        torch/transformers init path safely and "warms" the process so
        the subsequent embedding-model load succeeds. Effect persists for
        the lifetime of the process. Cheap (a few hundred ms) and harmless
        if Docling is not installed.
        """
        try:
            from docling.document_converter import DocumentConverter
            DocumentConverter()
        except Exception as exc:  # pragma: no cover
            logger.debug("Docling warm-up skipped: %s", exc)

    def _init_embeddings(self) -> HuggingFaceEmbeddings:
        try:
            self._warmup_torch_for_hf()
            logger.info(
                "Loading local embedding model: %s (device=%s)",
                self.config.embedding_model_name,
                self.config.device,
            )
            return HuggingFaceEmbeddings(
                model_name=self.config.embedding_model_name,
                model_kwargs={"device": self.config.device, **self.config.model_kwargs},
                encode_kwargs={"normalize_embeddings": self.config.normalize_embeddings},
            )
        except Exception as exc:
            logger.exception("Failed to load embedding model.")
            raise RuntimeError(
                f"Could not initialize embeddings '{self.config.embedding_model_name}'."
            ) from exc

    def _init_store(self) -> Chroma:
        try:
            logger.info(
                "Opening Chroma collection '%s' at %s",
                self.config.collection_name,
                self.config.persist_directory,
            )
            return Chroma(
                collection_name=self.config.collection_name,
                embedding_function=self._embeddings,
                persist_directory=str(self.config.persist_directory),
            )
        except Exception as exc:
            logger.exception("Failed to open ChromaDB.")
            raise RuntimeError(
                f"Could not open Chroma at {self.config.persist_directory}."
            ) from exc

    # ----------------------------------------------------------------- upsert
    @staticmethod
    def _doc_id(doc: Document) -> str:
        """
        Deterministic ID = hash(source + chunk_index + content). Keeps re-ingest
        idempotent: re-uploading the same PDF overwrites instead of duplicating.
        """
        source = str(doc.metadata.get("source", ""))
        idx = str(doc.metadata.get("chunk_index", ""))
        h = hashlib.sha256()
        h.update(source.encode("utf-8"))
        h.update(idx.encode("utf-8"))
        h.update(doc.page_content.encode("utf-8"))
        return h.hexdigest()

    @staticmethod
    def _sanitize_metadata(doc: Document) -> Document:
        """
        Chroma only accepts str/int/float/bool/None metadata values. Lists
        (like our `pages` field) get joined into comma-separated strings.
        """
        clean = {}
        for k, v in (doc.metadata or {}).items():
            if isinstance(v, (str, int, float, bool)) or v is None:
                clean[k] = v
            elif isinstance(v, (list, tuple)):
                clean[k] = ",".join(str(x) for x in v)
            else:
                clean[k] = str(v)
        return Document(page_content=doc.page_content, metadata=clean)

    def add_documents(self, documents: Sequence[Document]) -> List[str]:
        if not documents:
            logger.warning("add_documents called with empty input.")
            return []

        clean_docs = [self._sanitize_metadata(d) for d in documents]
        ids = [self._doc_id(d) for d in clean_docs]

        try:
            self._store.add_documents(documents=clean_docs, ids=ids)
        except Exception as exc:
            logger.exception("Chroma upsert failed.")
            raise RuntimeError("Failed to upsert documents into Chroma.") from exc

        logger.info("Upserted %d chunks into Chroma.", len(clean_docs))
        return ids

    # ---------------------------------------------------------- introspection
    def count(self) -> int:
        try:
            return self._store._collection.count()  # noqa: SLF001
        except Exception as exc:
            # Stale collection reference (another process called reset()).
            # Refresh once and retry; if that still fails, surface -1 so
            # /api/health stays cheap and never throws.
            if any(h in str(exc).lower() for h in self._TRANSIENT_HINTS):
                self._refresh_store()
                try:
                    return self._store._collection.count()  # noqa: SLF001
                except Exception:
                    pass
            return -1

    def reset(self) -> None:
        """Delete the entire collection. Useful for tests / re-ingest flows."""
        try:
            self._store.delete_collection()
            logger.warning("Chroma collection '%s' deleted.", self.config.collection_name)
        finally:
            self._store = self._init_store()

    # -------------------------------------------------------------- retrieval
    @staticmethod
    def _build_filter(
        filter: Optional[dict],
        filenames: Optional[Sequence[str]],
    ) -> Optional[dict]:
        """Compose an optional caller-supplied Chroma `where` filter with a
        scope restriction to specific filenames. Empty / None filenames lists
        mean 'no scope restriction' (search everything)."""
        scope = None
        if filenames:
            names = [n for n in filenames if n]
            if names:
                scope = {"filename": {"$in": list(names)}}
        if filter and scope:
            return {"$and": [filter, scope]}
        return filter or scope

    # Substrings of exception messages we know are recoverable. SQLite
    # file-locks ('lock', 'busy', 'i/o disk') clear themselves on retry;
    # 'collection ... does not exist' / NotFoundError happens when ANOTHER
    # process (or another LocalVectorStore in the same process) deleted
    # the collection out from under us via reset() -- re-opening the
    # Chroma wrapper picks up the freshly-created replacement.
    _TRANSIENT_HINTS = (
        "lock", "busy", "i/o disk", "temporarily unavailable",
        "does not exist", "not found", "notfounderror", "no such table",
    )

    def _refresh_store(self) -> None:
        """Drop the cached Chroma wrapper and reopen against the on-disk
        chroma_db. Cheap, and lets the next call pick up a collection
        UUID that may have changed underneath us."""
        try:
            self._store = self._init_store()
        except Exception:
            logger.exception("Failed to refresh Chroma wrapper.")

    def _search_with_retry(self, op_name: str, fn_factory):
        """Run a chroma similarity call, retry once on transient errors.

        ``fn_factory`` must be a zero-arg callable that READS ``self._store``
        each time it's invoked -- the retry path replaces ``self._store``,
        so capturing it in a closure beforehand would defeat the recovery."""
        try:
            return fn_factory()
        except Exception as exc:
            msg = str(exc).lower()
            transient = any(h in msg for h in self._TRANSIENT_HINTS)
            if transient:
                logger.warning(
                    "%s hit a transient error (%s); refreshing Chroma and retrying...",
                    op_name, exc.__class__.__name__,
                )
                self._refresh_store()
                try:
                    return fn_factory()
                except Exception as exc2:
                    logger.exception("%s failed on retry too.", op_name)
                    raise RuntimeError(
                        f"Vector search failed: {exc2.__class__.__name__}: {exc2}. "
                        "If this keeps happening, restart the app."
                    ) from exc2
            logger.exception("%s failed.", op_name)
            raise RuntimeError(
                f"Vector search failed: {exc.__class__.__name__}: {exc}"
            ) from exc

    def similarity_search(
        self,
        query: str,
        k: int = 4,
        filter: Optional[dict] = None,
        filenames: Optional[Sequence[str]] = None,
    ) -> List[Document]:
        """Top-k similarity search, optionally restricted to a subset of
        indexed filenames (multi-document cross-talk control)."""
        where = self._build_filter(filter, filenames)
        return self._search_with_retry(
            "Similarity search",
            # NOTE: read self._store inside the lambda each call -- the
            # retry path may have replaced it after a NotFoundError.
            lambda: self._store.similarity_search(query, k=k, filter=where),
        )

    def similarity_search_with_scores(
        self,
        query: str,
        k: int = 4,
        filter: Optional[dict] = None,
        filenames: Optional[Sequence[str]] = None,
    ) -> List[tuple[Document, float]]:
        where = self._build_filter(filter, filenames)
        return self._search_with_retry(
            "Scored similarity search",
            lambda: self._store.similarity_search_with_score(query, k=k, filter=where),
        )

    def as_retriever(self, k: int = 4, **kwargs) -> BaseRetriever:
        """Return a LangChain BaseRetriever for use in chains."""
        search_kwargs = {"k": k}
        search_kwargs.update(kwargs.pop("search_kwargs", {}))
        return self._store.as_retriever(search_kwargs=search_kwargs, **kwargs)

    def _all_metadatas(self) -> List[dict]:
        """Internal: pull every chunk's metadata in one call. Used by both
        list_filenames (legacy) and list_files_with_meta (v0.2.0 UI)."""
        def _get():
            return self._store._collection.get(include=["metadatas"])  # noqa: SLF001
        try:
            data = _get()
        except Exception as exc:
            if any(h in str(exc).lower() for h in self._TRANSIENT_HINTS):
                self._refresh_store()
                try:
                    data = _get()
                except Exception:
                    logger.exception("Failed to enumerate metadatas after refresh.")
                    return []
            else:
                logger.exception("Failed to enumerate metadatas.")
                return []
        return [m or {} for m in data.get("metadatas", [])]

    def list_filenames(self) -> List[str]:
        """All distinct `filename` values currently in the collection.
        Useful for the UI's 'search scope' selector."""
        names = {m.get("filename") for m in self._all_metadatas()}
        return sorted(n for n in names if n)

    def list_files_with_meta(self) -> List[dict]:
        """Aggregate per-file metadata from individual chunk metadatas:
        chunk_count, file_size (bytes), added_at (epoch seconds, MIN
        across chunks -- the moment the file first appeared in the
        index), source_kind (uploaded / typed / folder / unknown).

        Returns a list sorted by added_at descending (newest first) so
        the Library panel shows recent uploads at the top. Files that
        existed before the v0.2.0 metadata fields rolled out fall
        through with sensible defaults (added_at=0, file_size=0,
        source_kind='unknown')."""
        agg: dict[str, dict] = {}
        for m in self._all_metadatas():
            fn = m.get("filename")
            if not fn:
                continue
            row = agg.setdefault(fn, {
                "filename": fn,
                "chunk_count": 0,
                "file_size": 0,
                "added_at": 0.0,
                "source_kind": "unknown",
            })
            row["chunk_count"] += 1
            # added_at / file_size / source_kind should be identical across
            # every chunk of one file. Use first non-default we see; pick
            # MIN added_at to be defensive against re-ingest bumping it.
            added = m.get("added_at")
            if isinstance(added, (int, float)) and added > 0:
                row["added_at"] = (
                    min(row["added_at"], float(added))
                    if row["added_at"] > 0 else float(added)
                )
            size = m.get("file_size")
            if isinstance(size, (int, float)) and size > 0:
                row["file_size"] = int(size)
            kind = m.get("source_kind")
            if isinstance(kind, str) and kind:
                row["source_kind"] = kind
        return sorted(agg.values(), key=lambda r: -r["added_at"])

    def delete_by_filename(self, filename: str) -> int:
        """Drop every chunk whose metadata.filename == filename. Returns
        the number of chunks removed (best-effort; -1 on failure)."""
        if not filename:
            return 0
        try:
            # Chroma's delete-by-where: passes metadata filter directly.
            # We have to count first because delete() doesn't report n.
            before = self._all_metadatas()
            target_ids = []
            data = self._store._collection.get(  # noqa: SLF001
                where={"filename": filename},
                include=[],
            )
            target_ids = data.get("ids", []) or []
            if not target_ids:
                return 0
            self._store._collection.delete(ids=target_ids)  # noqa: SLF001
            logger.info("Deleted %d chunks for %s.", len(target_ids), filename)
            return len(target_ids)
        except Exception as exc:
            if any(h in str(exc).lower() for h in self._TRANSIENT_HINTS):
                self._refresh_store()
                try:
                    data = self._store._collection.get(  # noqa: SLF001
                        where={"filename": filename}, include=[],
                    )
                    ids = data.get("ids", []) or []
                    if ids:
                        self._store._collection.delete(ids=ids)  # noqa: SLF001
                    return len(ids)
                except Exception:
                    logger.exception("delete_by_filename retry failed.")
                    return -1
            logger.exception("delete_by_filename failed for %s.", filename)
            return -1

    def similarity_search_balanced(
        self,
        query: str,
        k: int = 6,
        filenames: Optional[Sequence[str]] = None,
    ) -> List[Document]:
        """
        Cross-document balanced retrieval. Allocates the budget evenly across
        the in-scope filenames so a single semantically-dominant document can
        not crowd out the others. Falls back to plain similarity_search when
        only one (or zero) filenames are in scope.

        Used for queries like 'what's common between the two essays?' where
        top-k by raw similarity often returns chunks from just one of them
        and the LLM ends up answering as if the other doesn't exist.
        """
        scope = list(filenames) if filenames else self.list_filenames()
        if len(scope) <= 1:
            return self.similarity_search(query, k=k, filenames=scope or None)

        per_doc = max(1, k // len(scope))
        bonus = k - per_doc * len(scope)  # distribute leftover
        results: List[Document] = []
        for i, fn in enumerate(scope):
            take = per_doc + (1 if i < bonus else 0)
            results.extend(
                self.similarity_search(query, k=take, filenames=[fn])
            )
        logger.info(
            "Balanced retrieval: %d chunks across %d docs (~%d/doc).",
            len(results), len(scope), per_doc,
        )
        return results


# ---------------------------------------------------------------------------
# Convenience top-level helpers
# ---------------------------------------------------------------------------
def build_default_store() -> LocalVectorStore:
    return LocalVectorStore(VectorStoreConfig())


def index_documents(
    documents: Iterable[Document],
    store: Optional[LocalVectorStore] = None,
) -> LocalVectorStore:
    """Embed a stream of LangChain Documents into the local vector store."""
    store = store or build_default_store()
    docs = list(documents)
    store.add_documents(docs)
    logger.info("Vector store now holds approx %d chunks.", store.count())
    return store


# ---------------------------------------------------------------------------
# CLI: ingest a directory and index it in one shot.
# ---------------------------------------------------------------------------
def main(argv: Optional[List[str]] = None) -> None:
    import argparse

    from ingest import PDFIngestor, DEFAULT_DATA_DIR

    parser = argparse.ArgumentParser(
        description="Index PDFs into the local Chroma vector store."
    )
    parser.add_argument(
        "paths",
        nargs="*",
        help="PDF files to index. Defaults to ./data/*.pdf.",
    )
    parser.add_argument("--reset", action="store_true", help="Wipe collection first.")
    args = parser.parse_args(argv)

    ingestor = PDFIngestor()
    if args.paths:
        results = ingestor.ingest_many(args.paths)
    else:
        results = ingestor.ingest_directory(DEFAULT_DATA_DIR)

    store = build_default_store()
    if args.reset:
        store.reset()

    all_docs: List[Document] = []
    for r in results:
        all_docs.extend(r.documents)
    index_documents(all_docs, store=store)

    print(f"Indexed {len(all_docs)} chunks. Collection size: {store.count()}.")


if __name__ == "__main__":
    main()
