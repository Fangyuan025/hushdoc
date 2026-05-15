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


class AssistantVariant(TypedDict, total=False):
    """v0.5.0: one regenerated version of an assistant turn.

    Multi-variant model: a user msg can be followed by an assistant msg
    that holds N variants -- the user navigates between them with the
    < N/M > pager. The currently-displayed variant is the one whose
    content / sources / trace the model sees as 'prior assistant turn'
    when generating the next reply."""
    content: str
    # Optional metadata that goes into the Sources drawer + retrieval-
    # trace tab. Kept as raw dicts so the schema mirrors the SSE
    # payload shape; the chain doesn't need to deserialise this.
    sources: List[Dict]
    retrieval_trace: List[Dict]
    retrieval_mode: str
    standalone_question: str
    chitchat: bool
    ts: float
    error: str  # set when the variant generation failed mid-stream


class ConvMessage(TypedDict, total=False):
    role: str           # "user" | "assistant"
    # User messages: 'content' is the typed text. Assistant messages
    # may use 'content' (pre-v0.5.0 shape) OR 'variants' + 'active_variant'
    # (v0.5.0 multi-variant shape). Reads always go through
    # normalize_message() which converts the legacy shape to variants.
    content: str
    variants: List[AssistantVariant]
    active_variant: int  # index into variants
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


def normalize_message(msg: Dict) -> Dict:
    """Back-compat: legacy assistant messages ({role, content}) get
    wrapped into the variants shape so the rest of the codebase only
    ever has to deal with one format. Idempotent -- a message already
    in variant shape passes through unchanged. User messages always
    pass through unchanged."""
    if not isinstance(msg, dict):
        return msg
    if msg.get("role") != "assistant":
        return msg
    if "variants" in msg and isinstance(msg["variants"], list):
        # Already in v0.5.0 shape. Make sure active_variant is in range.
        n = len(msg["variants"])
        if n == 0:
            # Shouldn't happen, but recover by treating as legacy.
            return msg
        active = msg.get("active_variant", 0)
        if not isinstance(active, int) or active < 0 or active >= n:
            msg["active_variant"] = 0
        return msg
    # Legacy: lift content into a single-variant array
    content = msg.get("content", "")
    return {
        "role": "assistant",
        "ts": msg.get("ts"),
        "variants": [
            {"content": content, "ts": msg.get("ts")},
        ],
        "active_variant": 0,
    }


def active_content(msg: Dict) -> str:
    """For assistant messages, return the content of the active
    variant (the one the user is currently viewing in the pager and
    therefore the one subsequent turns build on)."""
    if msg.get("role") == "assistant" and "variants" in msg:
        variants = msg["variants"] or []
        if not variants:
            return ""
        idx = msg.get("active_variant", 0)
        idx = max(0, min(idx, len(variants) - 1))
        return variants[idx].get("content", "")
    return msg.get("content", "")


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
            conv = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            logger.exception("Conv %s unreadable.", conv_id)
            return None
        # Normalise messages on read so callers only see the v0.5.0
        # variant shape. Legacy assistant messages ({role, content}) are
        # lifted into a single-variant array; user messages and already-
        # normalised assistant messages pass through unchanged.
        if isinstance(conv, dict) and isinstance(conv.get("messages"), list):
            conv["messages"] = [normalize_message(m) for m in conv["messages"]]
        return conv

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
        full conversation after the write, or None if the id is unknown.

        v0.5.0: assistant messages are always written in variants shape.
        If a caller hands us a legacy ``{role: assistant, content: ...}``
        we wrap it into a single-variant array before persisting so the
        on-disk format stays consistent."""
        conv = self._read_conv(conv_id)
        if not conv:
            return None
        conv.setdefault("messages", [])
        now = time.time()
        for m in messages:
            m.setdefault("ts", now)
            if m.get("role") == "assistant":
                # Force variants shape on write.
                if "variants" not in m or not isinstance(m.get("variants"), list):
                    content = m.get("content", "")
                    variant: Dict = {"content": content, "ts": m["ts"]}
                    # Carry through any optional sidecar fields the caller
                    # supplied alongside content (sources / trace / etc.)
                    # so the active variant holds the full payload.
                    for key in (
                        "sources",
                        "retrieval_trace",
                        "retrieval_mode",
                        "standalone_question",
                        "chitchat",
                        "error",
                        # v0.6.0: persisted per-sentence chunk-paragraph
                        # bindings for the inline citation popover.
                        "sentence_bindings",
                    ):
                        if key in m:
                            variant[key] = m[key]
                    m = {
                        "role": "assistant",
                        "ts": m["ts"],
                        "variants": [variant],
                        "active_variant": 0,
                    }
                else:
                    # Caller passed an already-shaped variants message --
                    # make sure active_variant is sane.
                    n = len(m["variants"])
                    if n > 0:
                        a = m.get("active_variant", 0)
                        if not isinstance(a, int) or a < 0 or a >= n:
                            m["active_variant"] = 0
            conv["messages"].append(m)
        conv["updated_at"] = now
        self._write_conv(conv)
        return conv

    def append_variant(
        self,
        conv_id: str,
        message_index: int,
        variant: Dict,
    ) -> Optional[Conversation]:
        """v0.5.0: append a new regenerated variant to an assistant
        message and make it the active one. ``message_index`` is the
        index into ``conv["messages"]`` of the assistant turn being
        regenerated. Returns the full conversation post-write, or None
        on bad id / bad index / non-assistant target."""
        conv = self._read_conv(conv_id)
        if not conv:
            return None
        msgs = conv.get("messages", [])
        if message_index < 0 or message_index >= len(msgs):
            logger.warning(
                "append_variant: bad message_index %d (have %d msgs)",
                message_index, len(msgs),
            )
            return None
        target = msgs[message_index]
        if target.get("role") != "assistant":
            logger.warning(
                "append_variant: msg %d is not an assistant turn", message_index
            )
            return None
        # _read_conv already normalised this, but defend in depth.
        target = normalize_message(target)
        variant.setdefault("ts", time.time())
        target.setdefault("variants", []).append(variant)
        target["active_variant"] = len(target["variants"]) - 1
        msgs[message_index] = target
        conv["messages"] = msgs
        conv["updated_at"] = time.time()
        self._write_conv(conv)
        return conv

    def set_active_variant(
        self,
        conv_id: str,
        message_index: int,
        variant_index: int,
    ) -> Optional[Conversation]:
        """Switch which variant of an assistant turn is 'live'. The
        active variant is what later turns see as the prior assistant
        reply, so flipping this also flips the chain's view of history."""
        conv = self._read_conv(conv_id)
        if not conv:
            return None
        msgs = conv.get("messages", [])
        if message_index < 0 or message_index >= len(msgs):
            return None
        target = normalize_message(msgs[message_index])
        if target.get("role") != "assistant":
            return None
        n = len(target.get("variants", []))
        if n == 0 or variant_index < 0 or variant_index >= n:
            return None
        target["active_variant"] = variant_index
        msgs[message_index] = target
        conv["messages"] = msgs
        conv["updated_at"] = time.time()
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
