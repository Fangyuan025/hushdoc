"""Pydantic models for the Hushdoc HTTP API.

Kept deliberately small — the SSE chat stream uses dicts directly, but
everything else flows through these typed schemas so the OpenAPI spec
served at ``/docs`` is useful and the React client can codegen types
later if we want.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------
class HealthResponse(BaseModel):
    ok: bool
    version: str
    chain_loaded: bool
    store_loaded: bool
    vector_count: int
    indexed_files: List[str]


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------
class FileMeta(BaseModel):
    """Per-file metadata aggregated from the vector store. Added in v0.2.0
    to power the unified Library panel (size, added_at, source_kind shown
    in the row, hover-trash for per-file delete)."""
    filename: str
    chunk_count: int
    file_size: int = 0       # bytes; 0 if pre-v0.2.0 chunk or pasted text
    added_at: float = 0.0    # epoch seconds; 0 if pre-v0.2.0 chunk
    source_kind: str = "unknown"  # uploaded / folder / typed / unknown


class DocumentsResponse(BaseModel):
    filenames: List[str]
    chunk_count: int
    summaries: Dict[str, str]
    files: List[FileMeta] = []  # v0.2.0+ rich metadata; clients can ignore


class DeleteDocumentsResponse(BaseModel):
    ok: bool
    was_count: int


class DeleteOneFileResponse(BaseModel):
    """Response for DELETE /api/documents/{filename}."""
    ok: bool
    removed_chunks: int


class PasteTextRequest(BaseModel):
    """Payload for POST /api/documents/paste."""
    text: str
    # Optional display name. If omitted the server derives one from the
    # first non-empty line of the pasted content, falling back to a
    # timestamped 'pasted-YYYYmmdd-HHMMSS.md' name.
    filename: Optional[str] = None


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------
class ChatRequest(BaseModel):
    question: str
    # Either conversation_id (preferred, persists to disk) or session_id
    # (legacy, in-memory only). If conversation_id is set we use it as
    # the chain memory key AND append messages to disk.
    conversation_id: Optional[str] = None
    session_id: str = "default"
    filenames: Optional[List[str]] = Field(
        default=None,
        description="Restrict retrieval to these source files. None / empty "
                    "means search the whole vector store.",
    )


class ChatClearRequest(BaseModel):
    session_id: str = "default"


class ChatClearResponse(BaseModel):
    ok: bool


# ---------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------
class ConversationMeta(BaseModel):
    id: str
    title: str
    created_at: float
    updated_at: float
    message_count: int


class ConversationsListResponse(BaseModel):
    conversations: List[ConversationMeta]


class ConversationMessage(BaseModel):
    role: str
    content: str
    ts: Optional[float] = None


class ConversationDetail(BaseModel):
    id: str
    title: str
    created_at: float
    updated_at: float
    messages: List[ConversationMessage]


class CreateConversationRequest(BaseModel):
    title: Optional[str] = None


class RenameConversationRequest(BaseModel):
    title: str


# ---------------------------------------------------------------------------
# Voice
# ---------------------------------------------------------------------------
class VoiceTranscribeResponse(BaseModel):
    text: str


class VoiceSynthesizeRequest(BaseModel):
    text: str


class VoiceHealthResponse(BaseModel):
    whisper_ready: bool
    kokoro_ready: bool
