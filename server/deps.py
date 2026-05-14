"""
Lazy singletons for the heavy backend objects.

The vector store, ingestor, and RAG chain are each created on first
access. ``/api/health`` peeks via the ``*_if_loaded`` accessors so it
stays cheap and never triggers a 30-second cold-start of llama-server.
Endpoints that actually need the chain (chat, summarize) call the
plain ``get_*`` accessors and pay the load cost on first hit.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

import llama_server
from ingest import PDFIngestor
from llm_chain import LLMConfig, RAGChain
from vector_store import LocalVectorStore, build_default_store

logger = logging.getLogger("server.deps")

# Module-level singletons. None until first access.
_store: Optional[LocalVectorStore] = None
_ingestor: Optional[PDFIngestor] = None
_chain: Optional[RAGChain] = None


# ---------------------------------------------------------------------------
# Eager accessors (load on first call)
# ---------------------------------------------------------------------------
def get_store() -> LocalVectorStore:
    global _store
    if _store is None:
        logger.info("Loading vector store + embedding model...")
        _store = build_default_store()
    return _store


def get_ingestor() -> PDFIngestor:
    global _ingestor
    if _ingestor is None:
        logger.info("Initialising Docling ingestor...")
        _ingestor = PDFIngestor()
    return _ingestor


# Holds the effective model path the chain should use the next time
# it's (re)built. None = fall back to LLMConfig's frozen module-import
# default. Updated by reload_chain_with_model(); also read on first
# boot from the persisted hushdoc_config.json if present.
_pending_model_path: Optional[str] = None


def _load_persisted_model_path() -> None:
    """Apply hushdoc_config.json's model_path (if set + valid) to the
    pending slot so the very first chain build picks it up. Called
    once from main.py at startup."""
    global _pending_model_path
    try:
        from server import config as _cfg
        cfg = _cfg.read_config()
        p = cfg.get("model_path")
        if p:
            resolved = Path(p).expanduser().resolve()
            if resolved.is_file():
                _pending_model_path = str(resolved)
                logger.info(
                    "Persisted model path applied: %s", _pending_model_path,
                )
    except Exception:
        logger.exception("Failed to apply persisted model_path; using defaults.")


def get_chain() -> RAGChain:
    global _chain
    if _chain is None:
        logger.info("Spinning up RAGChain (will start llama-server on first ask)...")
        if _pending_model_path:
            # Explicit construction beats the frozen module-default,
            # which was evaluated at import time and can't be retroactively
            # changed by setting LLAMA_MODEL_PATH later in the process.
            llm_cfg = LLMConfig(model_path=Path(_pending_model_path))
            _chain = RAGChain(vector_store=get_store(), k=6, llm_config=llm_cfg)
        else:
            _chain = RAGChain(vector_store=get_store(), k=6)
    return _chain


# ---------------------------------------------------------------------------
# Peek accessors (return current singleton without triggering load)
# ---------------------------------------------------------------------------
def get_store_if_loaded() -> Optional[LocalVectorStore]:
    return _store


def get_ingestor_if_loaded() -> Optional[PDFIngestor]:
    return _ingestor


def get_chain_if_loaded() -> Optional[RAGChain]:
    return _chain


# ---------------------------------------------------------------------------
# Hot reload — used by the Settings page when the user picks a new model.
# ---------------------------------------------------------------------------
def reload_chain_with_model(model_path: str) -> None:
    """Stop the currently-running ``llama-server.exe`` (if any) and
    rebuild the RAG chain against a new GGUF.

    The vector store and ingestor singletons are untouched -- they
    don't depend on the LLM, just on the embedding model -- so swapping
    only the LLM is cheap. The chain itself is reset to None so the
    next ``get_chain()`` call rebuilds a ChatOpenAI client + spawns a
    fresh ``llama-server.exe`` against the new model. Any in-flight
    chat stream hitting the OLD subprocess will get a connection
    reset; the UI is expected to warn before triggering this path.

    Also sets ``LLAMA_MODEL_PATH`` for the benefit of any code path
    that still consults the env (e.g. CLI tools / smoke scripts that
    don't go through ``get_chain``)."""
    global _chain, _pending_model_path
    logger.info("Reloading chain with model: %s", model_path)

    # Stop the existing llama-server subprocess (terminates the .exe).
    shared = getattr(llama_server, "_SHARED", None)
    if shared is not None:
        try:
            shared.stop()
        except Exception:
            logger.exception("Failed to stop the existing llama-server.")
        llama_server._SHARED = None

    # Record the new path. get_chain() consults this slot to build
    # LLMConfig EXPLICITLY -- can't just rely on env mutation because
    # LLMConfig.model_path's default was frozen at module import.
    _pending_model_path = str(Path(model_path).expanduser().resolve())
    os.environ["LLAMA_MODEL_PATH"] = _pending_model_path
    _chain = None

    # Pre-warm: build the chain right now so the user's next chat turn
    # doesn't pay the 10-15 s cold-start latency on top of the reload.
    try:
        get_chain()
        logger.info("Chain rebuilt with new model.")
    except Exception:
        logger.exception("Chain rebuild failed; next /api/chat will retry.")
