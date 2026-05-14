"""
Per-conversation persistence for Hushdoc.

Each conversation is one JSON file under ``./chat_history/<id>.json`` so
deleting one is just a file unlink and concurrent writes from different
conversations don't fight each other. A small ``index.json`` keeps the
sidebar list cheap (no need to open every conv file just to enumerate).

Schema is intentionally simple — no migrations, no schema version, just
a dict per conversation. If we ever need to evolve it, we can add a
``version`` field and write a one-shot upgrade pass.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Dict, List, Optional, TypedDict

logger = logging.getLogger("conversations")

# All persistence lives under ./chat_history/. Co-locating it next to the
# Chroma store makes "wipe everything" a single rmtree operation later.
DEFAULT_DIR = Path("./chat_history")
INDEX_FILENAME = "index.json"


class ConvMessage(TypedDict, total=False):
    role: str           # "user" | "assistant"
    content: str
    ts: float           # unix timestamp


class ConvMeta(TypedDict, total=False):
    id: str
    title: str
    created_at: float
    updated_at: float
    message_count: int


class Conversation(ConvMeta, total=False):
    messages: List[ConvMessage]
    # v0.5.0: rolling window of last-N chunks the chain selected on
    # prior turns of this conversation. Mixed into the candidate pool
    # by the chain's _retrieve on follow-ups; bounded to ~12 entries
    # so a long convo doesn't fill the file. Items are flattened
    # Document dicts (filename / chunk_index / page_content / metadata).
    recent_chunks: List[Dict]


@dataclass
class ConversationStore:
    """Thread-safe-enough store for the local single-user app. A single
    Lock guards index updates; per-conversation files are written
    independently."""

    root: Path = DEFAULT_DIR
    _lock: Lock = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        if not self._index_path().exists():
            self._write_index({"order": []})

    # --------------------------------------------------------------- paths
    def _index_path(self) -> Path:
        return self.root / INDEX_FILENAME

    def _conv_path(self, conv_id: str) -> Path:
        return self.root / f"{conv_id}.json"

    # --------------------------------------------------------------- index
    def _read_index(self) -> Dict:
        try:
            return json.loads(self._index_path().read_text(encoding="utf-8"))
        except Exception:
            logger.exception("Index unreadable, falling back to empty.")
            return {"order": []}

    def _write_index(self, idx: Dict) -> None:
        tmp = self._index_path().with_suffix(".tmp")
        tmp.write_text(json.dumps(idx, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        tmp.replace(self._index_path())

    # ---------------------------------------------------------- conv files
    def _read_conv(self, conv_id: str) -> Optional[Conversation]:
        p = self._conv_path(conv_id)
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            logger.exception("Conv %s unreadable.", conv_id)
            return None

    def _write_conv(self, conv: Conversation) -> None:
        p = self._conv_path(conv["id"])
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(conv, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        tmp.replace(p)

    # -------------------------------------------------------------- public
    def list_metas(self) -> List[ConvMeta]:
        """Return conversations sorted newest-first by updated_at."""
        with self._lock:
            idx = self._read_index()
        out: List[ConvMeta] = []
        for cid in idx.get("order", []):
            conv = self._read_conv(cid)
            if not conv:
                continue
            out.append({
                "id": conv["id"],
                "title": conv.get("title", "New chat"),
                "created_at": conv.get("created_at", 0.0),
                "updated_at": conv.get("updated_at", 0.0),
                "message_count": len(conv.get("messages", [])),
            })
        out.sort(key=lambda m: m.get("updated_at", 0.0), reverse=True)
        return out

    def get(self, conv_id: str) -> Optional[Conversation]:
        return self._read_conv(conv_id)

    def create(self, title: str = "New chat") -> Conversation:
        cid = uuid.uuid4().hex[:12]
        now = time.time()
        conv: Conversation = {
            "id": cid,
            "title": title,
            "created_at": now,
            "updated_at": now,
            "messages": [],
        }
        self._write_conv(conv)
        with self._lock:
            idx = self._read_index()
            idx.setdefault("order", []).insert(0, cid)
            self._write_index(idx)
        logger.info("Created conversation %s", cid)
        return conv

    def append_messages(
        self,
        conv_id: str,
        messages: List[ConvMessage],
    ) -> Optional[Conversation]:
        """Append one or more messages and bump updated_at. Returns the
        full conversation after the write, or None if the id is unknown."""
        conv = self._read_conv(conv_id)
        if not conv:
            return None
        conv.setdefault("messages", [])
        now = time.time()
        for m in messages:
            m.setdefault("ts", now)
            conv["messages"].append(m)
        conv["updated_at"] = now
        self._write_conv(conv)
        return conv

    def set_recent_chunks(
        self,
        conv_id: str,
        chunks: List[Dict],
    ) -> Optional[Conversation]:
        """v0.5.0: persist the chain's rolling chunk window for this
        session so a backend restart doesn't break the follow-up
        retrieval boost. Writes only the chunks field; doesn't bump
        updated_at because this is purely an internal cache."""
        conv = self._read_conv(conv_id)
        if not conv:
            return None
        conv["recent_chunks"] = chunks
        self._write_conv(conv)
        return conv

    def set_title(self, conv_id: str, title: str) -> Optional[Conversation]:
        conv = self._read_conv(conv_id)
        if not conv:
            return None
        conv["title"] = title.strip()[:80] or "New chat"
        conv["updated_at"] = time.time()
        self._write_conv(conv)
        logger.info("Renamed conv %s -> %r", conv_id, conv["title"])
        return conv

    def delete(self, conv_id: str) -> bool:
        with self._lock:
            idx = self._read_index()
            order = idx.get("order", [])
            if conv_id in order:
                order.remove(conv_id)
                idx["order"] = order
                self._write_index(idx)
        p = self._conv_path(conv_id)
        if p.exists():
            try:
                p.unlink()
            except OSError:
                logger.exception("Failed to unlink %s", p)
                return False
        return True

    def clear_all(self) -> int:
        """Delete every conversation. Returns the count removed."""
        with self._lock:
            idx = self._read_index()
            order = list(idx.get("order", []))
        n = 0
        for cid in order:
            if self.delete(cid):
                n += 1
        return n


# Module-level default singleton.
default_store = ConversationStore()
