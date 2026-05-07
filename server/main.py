"""
FastAPI app for Hushdoc.

P1 + P2:
  - GET    /api/health             — cheap liveness + lazy-load status
  - GET    /api/documents          — list indexed filenames + summaries
  - POST   /api/documents/upload   — multipart upload + ingest, SSE
  - DELETE /api/documents          — wipe the vector store + summaries
  - POST   /api/chat               — streaming chat, SSE
  - POST   /api/chat/clear         — reset a chat session's memory
  - POST   /api/voice/transcribe   — Whisper-base.en, multipart audio
  - POST   /api/voice/synthesize   — Kokoro-82M, returns audio/wav
  - GET    /api/voice/health       — pipelines lazy-load status
"""
from __future__ import annotations

import asyncio
import io
import logging
import os
import shutil
import tempfile
import threading
import time
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, Response
from sse_starlette.sse import EventSourceResponse

import doc_summaries
from server import deps
from server.conversations import default_store as conv_store
from server.schemas import (
    ChatClearRequest,
    ChatClearResponse,
    ChatRequest,
    ConversationDetail,
    ConversationMessage,
    ConversationMeta,
    ConversationsListResponse,
    CreateConversationRequest,
    DeleteDocumentsResponse,
    DocumentsResponse,
    HealthResponse,
    RenameConversationRequest,
    VoiceHealthResponse,
    VoiceSynthesizeRequest,
    VoiceTranscribeResponse,
)
from server.streaming import chain_stream_to_sse, events_to_sse

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("server.main")


app = FastAPI(
    title="Hushdoc API",
    description=(
        "Local-only HTTP API for the Hushdoc PDF assistant. Wraps the "
        "ingest / vector-store / RAG-chain modules behind a small set of "
        "JSON + SSE endpoints. Consumed by the React frontend in ``web/``."
    ),
    version="1.0.0",
)

# CORS — Vite dev server on :5173 needs to call us on :8000. Production
# build will be served from the same origin so this becomes a no-op.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------
@app.get("/api/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Liveness + lazy-load status. Never triggers heavy initialization."""
    store = deps.get_store_if_loaded()
    chain = deps.get_chain_if_loaded()
    if store is not None:
        try:
            count = store.count()
            files = store.list_filenames()
        except Exception:
            count, files = 0, []
    else:
        count, files = 0, []
    return HealthResponse(
        ok=True,
        chain_loaded=chain is not None,
        store_loaded=store is not None,
        vector_count=count,
        indexed_files=files,
    )


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------
@app.get("/api/documents", response_model=DocumentsResponse)
def list_documents() -> DocumentsResponse:
    store = deps.get_store()
    return DocumentsResponse(
        filenames=store.list_filenames(),
        chunk_count=store.count(),
        summaries=doc_summaries.all_summaries(),
    )


@app.delete("/api/documents", response_model=DeleteDocumentsResponse)
def delete_documents() -> DeleteDocumentsResponse:
    """Wipe every chunk + every cached summary. Idempotent."""
    store = deps.get_store()
    was = store.count()
    store.reset()
    doc_summaries.clear_all()
    logger.info("Vector store wiped (was %d chunks).", was)
    return DeleteDocumentsResponse(ok=True, was_count=was)


# ---------------------------------------------------------------------------
# Document upload (multipart + per-file SSE progress)
# ---------------------------------------------------------------------------
UPLOAD_DIR = Path("./data/uploads")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


async def _ingest_files_streaming(
    paths: list[Path],
) -> AsyncIterator[tuple[str, dict]]:
    """Per-file ingest, yielding ('file_done', {...}) after each, then
    a final ('all_done', {...}). Heavy work runs in a thread so the
    event loop stays responsive."""
    chain = deps.get_chain()
    ingestor = deps.get_ingestor()
    store = deps.get_store()

    total_chunks = 0
    succeeded = 0

    def _ingest_one(p: Path) -> dict:
        result = ingestor.ingest(p)
        store.add_documents(result.documents)
        full_text = result.markdown or "\n\n".join(
            d.page_content for d in result.documents
        )
        summary = chain.summarize_document(p.name, full_text) or ""
        return {
            "filename": p.name,
            "chunks": result.chunk_count,
            "summary": summary,
        }

    for p in paths:
        try:
            payload = await asyncio.to_thread(_ingest_one, p)
            total_chunks += payload["chunks"]
            succeeded += 1
            yield ("file_done", payload)
        except Exception as exc:
            logger.exception("Failed to ingest %s", p)
            yield ("file_error", {"filename": p.name, "error": str(exc)})

    yield ("all_done", {
        "succeeded": succeeded,
        "total": len(paths),
        "total_chunks": total_chunks,
    })


@app.post("/api/documents/upload")
async def upload_documents(
    files: list[UploadFile] = File(...),
    replace: bool = Form(False),
):
    """Save each uploaded file under ./data/uploads/ then ingest it.
    Returns an SSE stream so the frontend can render per-file progress.
    """
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded.")

    # Optional wipe FIRST so the new uploads don't get mixed in.
    if replace:
        store = deps.get_store()
        store.reset()
        doc_summaries.clear_all()
        logger.info("Replace=true: wiped existing index before ingest.")

    paths: list[Path] = []
    for f in files:
        if not f.filename:
            continue
        dest = UPLOAD_DIR / f.filename
        with open(dest, "wb") as out:
            shutil.copyfileobj(f.file, out)
        paths.append(dest)

    return EventSourceResponse(
        events_to_sse(_ingest_files_streaming(paths)),
        media_type="text/event-stream",
    )


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------
@app.post("/api/chat")
async def chat(req: ChatRequest):
    """Streaming RAG turn. Emits SSE events:
        standalone, sources, token, done, error, [title]
    The React client uses ``EventSource`` to consume these by event name.

    When ``conversation_id`` is provided, the user/assistant messages are
    persisted to ``./chat_history/<id>.json`` and (on the first turn) an
    auto-title is generated and emitted as a ``title`` event so the
    sidebar can update without a separate roundtrip.
    """
    if not req.question or not req.question.strip():
        raise HTTPException(status_code=400, detail="question is empty")

    chain = deps.get_chain()
    # The chain memory key: prefer a real conversation id when present so
    # different convs keep separate rewriter / chat-history state.
    memory_key = req.conversation_id or req.session_id

    conv_before = None
    if req.conversation_id:
        conv_before = conv_store.get(req.conversation_id)
        if conv_before is None:
            raise HTTPException(
                status_code=404, detail=f"conversation {req.conversation_id} not found",
            )
        # Hydrate the chain's in-memory chat history from disk so the
        # rewriter and follow-up boost see the right context even after
        # a server restart.
        chain.hydrate_session(memory_key, conv_before.get("messages", []))

    events = chain.stream(
        req.question,
        session_id=memory_key,
        filenames=req.filenames or None,
    )

    async def _augment_with_persistence():
        """Wrap the chain SSE stream with side-effects:
        1. Forward every event to the client unchanged.
        2. When the 'done' event fires, persist user+assistant messages.
        3. If this was the conversation's first turn, generate a title
           and emit a 'title' event before closing the stream.
        """
        final_answer = ""
        async for ev in chain_stream_to_sse(events):
            yield ev
            if ev.get("event") == "done" and req.conversation_id:
                try:
                    import json as _json
                    payload = _json.loads(ev["data"])
                    final_answer = payload.get("answer", "") or ""
                except Exception:
                    final_answer = ""
        if req.conversation_id and final_answer:
            try:
                conv_store.append_messages(
                    req.conversation_id,
                    [
                        {"role": "user", "content": req.question},
                        {"role": "assistant", "content": final_answer},
                    ],
                )
                # Auto-title on the very first turn (now exactly 2 msgs).
                refreshed = conv_store.get(req.conversation_id) or {}
                msg_count = len(refreshed.get("messages", []))
                current_title = refreshed.get("title", "")
                if msg_count <= 2 and (not current_title or current_title == "New chat"):
                    title = await asyncio.to_thread(
                        chain.generate_title, req.question, final_answer,
                    )
                    conv_store.set_title(req.conversation_id, title)
                    yield {
                        "event": "title",
                        "data": __import__("json").dumps(
                            {"conversation_id": req.conversation_id, "title": title},
                            ensure_ascii=False,
                        ),
                    }
            except Exception:
                logger.exception("Conversation persistence/title failed.")

    return EventSourceResponse(
        _augment_with_persistence(),
        media_type="text/event-stream",
    )


# ---------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------
@app.get("/api/conversations", response_model=ConversationsListResponse)
def list_conversations() -> ConversationsListResponse:
    metas = conv_store.list_metas()
    return ConversationsListResponse(
        conversations=[ConversationMeta(**m) for m in metas],
    )


@app.post("/api/conversations", response_model=ConversationDetail)
def create_conversation(req: CreateConversationRequest) -> ConversationDetail:
    conv = conv_store.create(title=req.title or "New chat")
    return ConversationDetail(
        id=conv["id"],
        title=conv["title"],
        created_at=conv["created_at"],
        updated_at=conv["updated_at"],
        messages=[],
    )


@app.get("/api/conversations/{conv_id}", response_model=ConversationDetail)
def get_conversation(conv_id: str) -> ConversationDetail:
    conv = conv_store.get(conv_id)
    if not conv:
        raise HTTPException(status_code=404, detail="conversation not found")
    return ConversationDetail(
        id=conv["id"],
        title=conv["title"],
        created_at=conv["created_at"],
        updated_at=conv["updated_at"],
        messages=[ConversationMessage(**m) for m in conv.get("messages", [])],
    )


@app.delete("/api/conversations/{conv_id}")
def delete_conversation(conv_id: str):
    ok = conv_store.delete(conv_id)
    # Also drop the in-memory chat history for this conv so the next
    # creation with the same key starts clean.
    chain = deps.get_chain_if_loaded()
    if chain is not None:
        chain.reset_session(conv_id)
    return {"ok": ok}


@app.patch("/api/conversations/{conv_id}", response_model=ConversationMeta)
def rename_conversation(conv_id: str, req: RenameConversationRequest) -> ConversationMeta:
    conv = conv_store.set_title(conv_id, req.title)
    if not conv:
        raise HTTPException(status_code=404, detail="conversation not found")
    return ConversationMeta(
        id=conv["id"],
        title=conv["title"],
        created_at=conv["created_at"],
        updated_at=conv["updated_at"],
        message_count=len(conv.get("messages", [])),
    )


@app.post("/api/chat/clear", response_model=ChatClearResponse)
def clear_chat(req: ChatClearRequest) -> ChatClearResponse:
    chain = deps.get_chain()
    chain.reset_session(req.session_id)
    logger.info("Reset chat session %r.", req.session_id)
    return ChatClearResponse(ok=True)


# ---------------------------------------------------------------------------
# Voice
# ---------------------------------------------------------------------------
@app.get("/api/voice/health", response_model=VoiceHealthResponse)
def voice_health() -> VoiceHealthResponse:
    """Reports whether the ASR / TTS singletons have been loaded yet.
    Doesn't trigger loading — just peeks at the module globals."""
    import voice as voice_mod  # local import to avoid cold-start at app boot
    return VoiceHealthResponse(
        whisper_ready=voice_mod._asr is not None,    # noqa: SLF001
        kokoro_ready=voice_mod._tts is not None,     # noqa: SLF001
    )


@app.post("/api/voice/transcribe", response_model=VoiceTranscribeResponse)
async def voice_transcribe(audio: UploadFile = File(...)) -> VoiceTranscribeResponse:
    """Whisper-base.en transcription, English only. Returns ``{text}``."""
    import voice as voice_mod
    data = await audio.read()
    if not data:
        raise HTTPException(status_code=400, detail="empty audio payload")
    try:
        text = await asyncio.to_thread(voice_mod.transcribe, data)
    except Exception as exc:
        logger.exception("Voice transcription failed")
        raise HTTPException(status_code=500, detail=str(exc))
    return VoiceTranscribeResponse(text=text)


@app.post("/api/voice/synthesize")
async def voice_synthesize(req: VoiceSynthesizeRequest):
    """Kokoro-82M synthesis, English only. Returns audio/wav bytes
    directly so the frontend can pipe them into an HTMLAudioElement
    without going through JSON+base64."""
    import voice as voice_mod
    if not req.text or not req.text.strip():
        raise HTTPException(status_code=400, detail="text is empty")
    try:
        wav_bytes = await asyncio.to_thread(voice_mod.synthesize, req.text)
    except Exception as exc:
        logger.exception("Voice synthesis failed")
        raise HTTPException(status_code=500, detail=str(exc))
    if not wav_bytes:
        raise HTTPException(status_code=500, detail="synthesis returned empty audio")
    return Response(content=wav_bytes, media_type="audio/wav")


# ---------------------------------------------------------------------------
# Heartbeat watchdog
#
# The frontend POSTs /api/heartbeat every few seconds while the tab is open.
# If the server stops hearing pings for HEARTBEAT_TIMEOUT_S seconds AFTER it
# has received at least one (i.e. the user actually opened the app at least
# once this run), the process self-exits. The launcher (hushdoc.bat) sees
# the backend exit and falls into its cleanup-prompt flow.
#
# Disabled by default if HUSHDOC_AUTO_SHUTDOWN=0, so plain `dev.sh` /
# `uvicorn` workflows aren't ambushed by it.
# ---------------------------------------------------------------------------
HEARTBEAT_TIMEOUT_S = float(os.environ.get("HUSHDOC_HEARTBEAT_TIMEOUT", "15"))
_AUTO_SHUTDOWN_ENABLED = os.environ.get("HUSHDOC_AUTO_SHUTDOWN", "1") != "0"
_heartbeat_state = {"last_ts": 0.0, "ever_received": False}


@app.post("/api/heartbeat")
def heartbeat() -> dict:
    """Frontend ping. Resets the auto-shutdown timer."""
    _heartbeat_state["last_ts"] = time.time()
    _heartbeat_state["ever_received"] = True
    return {"ok": True}


async def _heartbeat_watchdog() -> None:
    """Background loop: once the first heartbeat has arrived, exit if the
    stream goes silent for too long. Sleeps 3s between checks so detection
    latency is bounded by HEARTBEAT_TIMEOUT_S + 3s."""
    while True:
        await asyncio.sleep(3)
        if not _heartbeat_state["ever_received"]:
            continue
        elapsed = time.time() - _heartbeat_state["last_ts"]
        if elapsed > HEARTBEAT_TIMEOUT_S:
            logger.info(
                "No heartbeat for %.1fs (timeout=%.0fs) — auto-shutdown.",
                elapsed, HEARTBEAT_TIMEOUT_S,
            )
            # Defer the actual exit a tick so the current tick of the event
            # loop can return cleanly. os._exit avoids hanging on lingering
            # SSE connections / atexit handlers that block on I/O.
            threading.Timer(0.3, lambda: os._exit(0)).start()
            return


@app.on_event("startup")
async def _start_watchdog() -> None:
    if _AUTO_SHUTDOWN_ENABLED:
        asyncio.create_task(_heartbeat_watchdog())
