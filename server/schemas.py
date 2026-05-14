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


class AppConfigResponse(BaseModel):
    """Surfaced to the UI's Settings page. The path validity flag is a
    derived hint so the user gets a red dot for 'file's gone' without
    the frontend having to do filesystem checks."""
    model_path: str
    auto_cleanup_on_exit: bool
    model_path_valid: bool = False


class AppConfigUpdateRequest(BaseModel):
    """PUT /api/config body. All fields optional -- the frontend sends
    only what changed so a partial save can't accidentally clobber an
    unrelated setting."""
    model_path: Optional[str] = None
    auto_cleanup_on_exit: Optional[bool] = None


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
    # v0.5.0 multi-variant regenerate. When true, the request re-generates
    # a fresh answer for the *previous* user turn (it doesn't append a new
    # user message). The new answer becomes a new variant attached to the
    # tail assistant message; the user can flip between variants in the
    # pager. ``question`` is ignored in this mode -- we use the last user
    # message stored in the conversation instead.
    regenerate: bool = False


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


class MessageVariant(BaseModel):
    """v0.5.0: one regenerated version of an assistant turn. The
    frontend renders these in the < N/M > pager and treats whichever
    matches ``active_variant`` as the live reply."""
    content: str
    ts: Optional[float] = None
    sources: Optional[List[Dict]] = None
    retrieval_trace: Optional[List[Dict]] = None
    retrieval_mode: Optional[str] = None
    standalone_question: Optional[str] = None
    chitchat: Optional[bool] = None
    error: Optional[str] = None


class ConversationMessage(BaseModel):
    role: str
    # ``content`` is the legacy flat field. For assistant messages in
    # v0.5.0 it mirrors the *active variant's* content so old clients
    # keep working; new clients should prefer ``variants`` +
    # ``active_variant`` and render the pager.
    content: str
    ts: Optional[float] = None
    variants: Optional[List[MessageVariant]] = None
    active_variant: Optional[int] = None


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


class SetActiveVariantRequest(BaseModel):
    """PATCH body for switching which variant of an assistant message
    is 'live'. The active variant is what later turns see as the prior
    assistant reply, so flipping this also flips chain history."""
    variant_index: int


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
