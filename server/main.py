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
from fastapi.responses import FileResponse, StreamingResponse, Response
from sse_starlette.sse import EventSourceResponse

import doc_summaries
from server import config as app_config
from server import deps
from server.conversations import (
    default_store as conv_store,
    active_content as conv_active_content,
)
from server.schemas import (
    AppConfigResponse,
    AppConfigUpdateRequest,
    ChatClearRequest,
    ChatClearResponse,
    ChatRequest,
    ConversationDetail,
    ConversationMessage,
    ConversationMeta,
    ConversationsListResponse,
    CreateConversationRequest,
    DeleteDocumentsResponse,
    DeleteOneFileResponse,
    DocumentsResponse,
    FileMeta,
    MessageVariant,
    PasteTextRequest,
    HealthResponse,
    RenameConversationRequest,
    SetActiveVariantRequest,
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


# Repo-root VERSION file is the single source of truth for the build's
# user-visible version string. Read once at import; missing-or-broken file
# falls back to "dev" so a git-clone running from source isn't blocked.
def _read_version() -> str:
    try:
        # main.py lives at server/main.py; VERSION is one level up at the repo root.
        path = Path(__file__).resolve().parent.parent / "VERSION"
        return path.read_text(encoding="utf-8").strip() or "dev"
    except Exception:
        return "dev"


APP_VERSION = _read_version()


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
        version=APP_VERSION,
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
    rows = store.list_files_with_meta()
    # v0.5.0: stamp has_raw on every row. Cheap stat per file; we run
    # it here rather than baking it into the chunk metadata so removing
    # a raw file (or re-uploading one for a previously typed item)
    # picks up the right answer on the next list call without a reindex.
    files: list[FileMeta] = []
    for row in rows:
        has_raw = False
        try:
            candidate = UPLOAD_DIR / Path(row["filename"]).name
            if candidate.is_file() and candidate.suffix.lower() in RAW_VIEWER_SUFFIXES:
                has_raw = True
        except Exception:
            has_raw = False
        files.append(FileMeta(**row, has_raw=has_raw))
    return DocumentsResponse(
        filenames=[f.filename for f in files] or store.list_filenames(),
        chunk_count=store.count(),
        summaries=doc_summaries.all_summaries(),
        files=files,
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


@app.delete("/api/documents/{filename}", response_model=DeleteOneFileResponse)
def delete_one_document(filename: str) -> DeleteOneFileResponse:
    """Remove a single file (every chunk whose metadata.filename matches)
    + its cached summary. v0.2.0 -- replaces the all-or-nothing wipe for
    the common case of 'I'm done with this one document'."""
    store = deps.get_store()
    n = store.delete_by_filename(filename)
    if n < 0:
        raise HTTPException(status_code=500, detail="Delete failed.")
    if n == 0:
        # Not really an error -- might be a typed/pasted item with no
        # disk copy to clean up, or an idempotent re-delete.
        logger.info("Delete for %r found 0 chunks (already gone?).", filename)
    try:
        doc_summaries.remove_summary(filename)
    except Exception:
        # Best-effort: the chain scopes summaries by current filenames at
        # query time, so a leftover summary won't break anything.
        logger.debug("Couldn't remove summary for %s.", filename, exc_info=True)

    # Also remove the saved upload file from disk if we put it there.
    try:
        candidate = UPLOAD_DIR / filename
        if candidate.is_file():
            candidate.unlink()
    except Exception:
        logger.debug("Couldn't remove upload file %s.", filename, exc_info=True)

    return DeleteOneFileResponse(ok=True, removed_chunks=n)


# ---------------------------------------------------------------------------
# Document upload (multipart + per-file SSE progress)
# ---------------------------------------------------------------------------
UPLOAD_DIR = Path("./data/uploads")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


def _stamp_file_metadata(
    documents,
    *,
    file_size: int,
    source_kind: str,
) -> None:
    """Decorate every chunk's metadata with the per-file fields the
    Library panel reads (added_at / file_size / source_kind). Mutates in
    place. ``added_at`` is recorded at ingest time, not file mtime, so
    re-uploading the same file moves it back to the top of the list.

    Kept as a free function so both file uploads and pasted-text ingest
    can share it."""
    now = time.time()
    for d in documents:
        # Don't overwrite existing values if the caller already set them.
        d.metadata.setdefault("added_at", now)
        d.metadata.setdefault("file_size", file_size)
        d.metadata.setdefault("source_kind", source_kind)


# v0.2.2: cooperative cancel for in-flight ingest. The streaming loop
# checks this between files so a runaway folder ingest can be stopped
# without killing the backend. Per-process flag is fine because only
# one ingest can run at a time today (FastAPI handles concurrent
# uploads but the chain's chroma + summary writes serialize anyway).
_ingest_cancel = asyncio.Event()


async def _ingest_files_streaming(
    paths: list[Path],
    source_kind: str = "uploaded",
) -> AsyncIterator[tuple[str, dict]]:
    """Per-file ingest, yielding ('file_done', {...}) after each, a final
    ('all_done', {...}), or ('cancelled', {...}) if the user hits cancel.

    Heavy work runs in a thread so the event loop stays responsive. The
    expensive per-file LLM summary is DEFERRED to a background task
    fired after all_done -- it's not needed until the user actually
    asks a question, and waiting on it serially used to add 3-10 s per
    file on the critical path.

    ``source_kind`` lets the upload endpoint mark whether the file came
    from a single drag-drop ('uploaded') or a bulk folder pick
    ('folder'). The frontend's Library row uses this to badge the row."""
    chain = deps.get_chain()
    ingestor = deps.get_ingestor()
    store = deps.get_store()

    # Fresh cancel epoch for THIS upload. Previous /cancel hits that
    # arrived after the last upload finished are discarded here.
    _ingest_cancel.clear()

    total_chunks = 0
    succeeded = 0
    # (filename, full_text) pairs queued for background summarization
    # after the SSE stream closes. Capturing the text up front because
    # the chunk text is already in chroma; reading it back from there
    # would just round-trip the same data.
    summary_queue: list[tuple[str, str]] = []

    def _ingest_one(p: Path) -> tuple[dict, str]:
        result = ingestor.ingest(p)
        try:
            file_size = p.stat().st_size
        except Exception:
            file_size = 0
        _stamp_file_metadata(
            result.documents, file_size=file_size, source_kind=source_kind,
        )
        store.add_documents(result.documents)
        full_text = result.markdown or "\n\n".join(
            d.page_content for d in result.documents
        )
        payload = {
            "filename": p.name,
            "chunks": result.chunk_count,
            # Summary still empty at this point; backfilled in the
            # background task below. The frontend doesn't surface it
            # anywhere user-visible during ingest, so the empty value
            # is harmless until the chain actually queries it.
            "summary": "",
        }
        return payload, full_text

    for p in paths:
        if _ingest_cancel.is_set():
            logger.info(
                "Ingest cancelled by user; %d/%d files completed before cancel.",
                succeeded, len(paths),
            )
            yield ("cancelled", {
                "completed": succeeded,
                "total": len(paths),
                "total_chunks": total_chunks,
            })
            break
        try:
            payload, full_text = await asyncio.to_thread(_ingest_one, p)
            total_chunks += payload["chunks"]
            succeeded += 1
            summary_queue.append((p.name, full_text))
            yield ("file_done", payload)
        except Exception as exc:
            logger.exception("Failed to ingest %s", p)
            yield ("file_error", {"filename": p.name, "error": str(exc)})
    else:
        # Loop completed without a `break` from the cancel branch.
        yield ("all_done", {
            "succeeded": succeeded,
            "total": len(paths),
            "total_chunks": total_chunks,
        })

    # Fire-and-forget summary backfill. Runs even after the user
    # cancels so the partial set of completed files still gets its
    # summaries. doc_summaries cache is idempotent so re-runs are safe.
    if summary_queue:
        asyncio.create_task(_backfill_summaries(summary_queue))


async def _backfill_summaries(items: list[tuple[str, str]]) -> None:
    """Generate per-document summaries serially in the background after
    the ingest SSE stream has already closed. We process serially so
    we don't compete with the user's first chat turn for the single
    llama-server slot."""
    chain = deps.get_chain()
    for filename, full_text in items:
        try:
            await asyncio.to_thread(chain.summarize_document, filename, full_text)
        except Exception:
            logger.exception("Background summary failed for %s", filename)


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

    # The folder vs single-file distinction comes from the multipart
    # request itself (multiple paths from one webkitdirectory pick vs
    # one-or-more from the drag-drop zone). The frontend signals which
    # via the `source_kind` form field; default to 'uploaded'.
    return EventSourceResponse(
        events_to_sse(_ingest_files_streaming(paths)),
        media_type="text/event-stream",
    )


@app.post("/api/documents/upload/cancel")
def cancel_upload() -> dict:
    """Stop the in-flight ingest at the NEXT file boundary. The current
    file's Docling parse (already in a worker thread) will run to
    completion -- we can't safely abort that mid-pass -- but every
    remaining file in the queue is dropped, and a 'cancelled' SSE
    event is emitted so the frontend can clear its progress UI.

    Idempotent: setting an already-set event is a no-op."""
    _ingest_cancel.set()
    logger.info("Ingest cancel flag set.")
    return {"ok": True}


# v0.5.0: extensions whose raw bytes the citation viewer can render.
# PDFs go through pdf.js on the frontend; everything else surfaces as a
# 404 from /raw, and the UI falls back to the snippet-only sources card.
RAW_VIEWER_SUFFIXES = {".pdf"}


def _resolve_raw_path(filename: str) -> Path:
    """Map a stored filename onto its on-disk copy under UPLOAD_DIR,
    rejecting any input that would escape the upload dir (path-traversal
    hardening). We use ``Path(...).name`` to peel off any directory
    components a caller might smuggle in via ``..\\``, then assert the
    resolved path stays inside ``UPLOAD_DIR``."""
    if not filename:
        raise HTTPException(status_code=400, detail="empty filename")
    base = Path(filename).name
    if not base or base != filename:
        # Reject ../ traversal, absolute paths, embedded slashes.
        raise HTTPException(status_code=400, detail="bad filename")
    candidate = (UPLOAD_DIR / base).resolve()
    upload_root = UPLOAD_DIR.resolve()
    try:
        candidate.relative_to(upload_root)
    except ValueError:
        raise HTTPException(status_code=400, detail="bad filename")
    return candidate


@app.get("/api/documents/{filename}/raw")
def get_document_raw(filename: str):
    """Return the original uploaded file's bytes so the citation viewer
    can render it.

    PDFs are what we actually wire into the v0.5.0 viewer (pdf.js
    renders them with a text layer we can highlight), but the endpoint
    serves any extension whose raw bytes we kept on disk. Typed / pasted
    items have no on-disk copy and return 404, as do legacy files that
    were ingested before this endpoint existed (the user can re-upload
    to get the viewer back).
    """
    path = _resolve_raw_path(filename)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="no raw copy on disk")
    suffix = path.suffix.lower()
    if suffix not in RAW_VIEWER_SUFFIXES:
        # We could happily serve anything here, but the citation viewer
        # only knows what to do with PDFs today. Returning 415 instead
        # of streaming bytes the UI can't use makes the failure visible
        # in logs / devtools rather than silently rendering nothing.
        raise HTTPException(
            status_code=415,
            detail=f"raw viewer not supported for {suffix or 'this file'}",
        )
    return FileResponse(
        path,
        media_type="application/pdf",
        # inline disposition so the browser hands it to our viewer
        # rather than offering a download.
        headers={"Content-Disposition": f'inline; filename="{path.name}"'},
    )


@app.post("/api/documents/paste")
async def paste_document(req: PasteTextRequest):
    """Ingest raw pasted text (no disk copy). The Library shows the
    item with source_kind='typed' so the user can tell it apart from
    uploaded files. Filename is either user-supplied or auto-derived
    from the first non-empty line."""
    text = (req.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="empty text")

    # Derive a friendly filename from the first non-empty / non-heading
    # line of the paste, capped at 60 chars. Falls back to a timestamp.
    def _derive_filename() -> str:
        for line in text.splitlines():
            cleaned = line.strip().lstrip("#").strip()
            if cleaned:
                # Strip characters that would be awkward in URLs / sidebars.
                safe = "".join(
                    c if (c.isalnum() or c in " -_.") else " "
                    for c in cleaned
                ).strip()
                safe = " ".join(safe.split())[:60]
                if safe:
                    return f"{safe}.md"
        from datetime import datetime
        return f"pasted-{datetime.now():%Y%m%d-%H%M%S}.md"

    filename = (req.filename or "").strip() or _derive_filename()

    ingestor = deps.get_ingestor()
    chain = deps.get_chain()
    store = deps.get_store()

    def _run() -> dict:
        result = ingestor.ingest_text(text, filename=filename)
        _stamp_file_metadata(
            result.documents,
            file_size=len(text.encode("utf-8")),
            source_kind="typed",
        )
        store.add_documents(result.documents)
        summary = chain.summarize_document(filename, text) or ""
        return {"filename": filename, "chunks": result.chunk_count, "summary": summary}

    try:
        payload = await asyncio.to_thread(_run)
    except Exception as exc:
        logger.exception("Paste-text ingest failed.")
        raise HTTPException(status_code=500, detail=str(exc))
    return payload


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

    v0.5.0 regenerate mode (``regenerate=True``): instead of appending a
    new ``(user, assistant)`` pair, re-runs the chain against the last
    user turn and appends the new answer as an additional **variant** on
    the tail assistant message. ``question`` is ignored in this mode --
    we always use the conversation's last user message so the regen is
    grounded in exactly the same prompt the user already asked.
    """
    chain = deps.get_chain()
    memory_key = req.conversation_id or req.session_id

    # ------------------------------------------------------------------
    # Regenerate path -- requires an existing conversation whose tail is
    # an assistant message. We splice the chain history so the rewriter
    # sees everything *up to and including* the last user message, but
    # NOT the prior assistant reply (otherwise the new variant would be
    # biased toward repeating it verbatim).
    # ------------------------------------------------------------------
    conv_before = None
    regen_target_index: int = -1
    regen_question: str = req.question
    if req.regenerate:
        if not req.conversation_id:
            raise HTTPException(
                status_code=400,
                detail="regenerate requires conversation_id",
            )
        conv_before = conv_store.get(req.conversation_id)
        if conv_before is None:
            raise HTTPException(
                status_code=404,
                detail=f"conversation {req.conversation_id} not found",
            )
        msgs = conv_before.get("messages", [])
        if not msgs or msgs[-1].get("role") != "assistant":
            raise HTTPException(
                status_code=400,
                detail="regenerate: last message is not an assistant turn",
            )
        # Find the user message immediately preceding the assistant tail.
        if len(msgs) < 2 or msgs[-2].get("role") != "user":
            raise HTTPException(
                status_code=400,
                detail="regenerate: cannot locate prior user message",
            )
        regen_target_index = len(msgs) - 1
        regen_question = msgs[-2].get("content", "") or ""
        if not regen_question.strip():
            raise HTTPException(
                status_code=400,
                detail="regenerate: prior user message is empty",
            )
        # Hydrate WITHOUT the tail assistant -- the rewriter should see
        # exactly what it saw the first time around.
        chain.hydrate_session(memory_key, msgs[:-1])
        chain.preload_session_memory(
            memory_key, conv_before.get("recent_chunks", []) or [],
        )
    else:
        if not req.question or not req.question.strip():
            raise HTTPException(status_code=400, detail="question is empty")
        if req.conversation_id:
            conv_before = conv_store.get(req.conversation_id)
            if conv_before is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"conversation {req.conversation_id} not found",
                )
            # Hydrate the chain's in-memory chat history from disk so the
            # rewriter and follow-up boost see the right context even
            # after a server restart.
            chain.hydrate_session(memory_key, conv_before.get("messages", []))
            chain.preload_session_memory(
                memory_key, conv_before.get("recent_chunks", []) or [],
            )

    events = chain.stream(
        regen_question,
        session_id=memory_key,
        filenames=req.filenames or None,
    )

    async def _augment_with_persistence():
        """Wrap the chain SSE stream with side-effects:
        1. Forward every event to the client unchanged.
        2. Capture standalone / sources / done payloads so we can persist
           the full variant payload, not just the answer text.
        3. On ``done``, persist either a new (user, assistant) pair (the
           normal path) or a new variant on the tail assistant message
           (the regenerate path).
        4. If this was the conversation's first turn, generate a title
           and emit a ``title`` event before closing the stream.
        """
        final_answer = ""
        # Sidecar payload captured from the stream for the variant write.
        captured_standalone: str = ""
        captured_sources: list = []
        captured_trace: list = []
        captured_mode: str = ""
        captured_chitchat: bool = False
        # v0.6.0: sentence -> chunk-paragraph bindings for the inline
        # citation popover. Captured from the done event and persisted
        # alongside the assistant message so a reloaded conversation
        # still renders the popovers without re-running retrieval.
        captured_bindings: list = []

        import json as _json
        async for ev in chain_stream_to_sse(events):
            # On the regenerate path we annotate the done frame with the
            # message_index the new variant attaches to so the frontend
            # knows which bubble to update without re-fetching the conv.
            if ev.get("event") == "standalone":
                try:
                    captured_standalone = _json.loads(ev["data"]).get("query", "") or ""
                except Exception:
                    pass
            elif ev.get("event") == "sources":
                try:
                    captured_sources = _json.loads(ev["data"]).get("docs", []) or []
                except Exception:
                    pass
            elif ev.get("event") == "done":
                try:
                    payload = _json.loads(ev["data"])
                    final_answer = payload.get("answer", "") or ""
                    captured_trace = payload.get("retrieval_trace", []) or []
                    captured_mode = payload.get("retrieval_mode", "") or ""
                    captured_chitchat = bool(payload.get("chitchat", False))
                    captured_bindings = payload.get("sentence_bindings", []) or []
                    if not captured_sources:
                        captured_sources = payload.get("source_documents", []) or []
                    if req.regenerate and req.conversation_id:
                        payload["regenerated_message_index"] = regen_target_index
                        ev = {
                            "event": "done",
                            "data": _json.dumps(payload, ensure_ascii=False),
                        }
                except Exception:
                    final_answer = ""
            yield ev

        if not (req.conversation_id and final_answer):
            return

        try:
            variant_payload = {
                "content": final_answer,
                "sources": captured_sources,
                "retrieval_trace": captured_trace,
                "retrieval_mode": captured_mode,
                "standalone_question": captured_standalone,
                "chitchat": captured_chitchat,
                "sentence_bindings": captured_bindings,
            }
            if req.regenerate:
                conv_after = conv_store.append_variant(
                    req.conversation_id,
                    regen_target_index,
                    variant_payload,
                )
                # Surface the new variant index so the frontend can flip
                # its pager to the freshly-generated answer.
                if conv_after is not None:
                    target = conv_after["messages"][regen_target_index]
                    new_variant_index = target.get("active_variant", 0)
                    yield {
                        "event": "variant_done",
                        "data": _json.dumps({
                            "conversation_id": req.conversation_id,
                            "message_index": regen_target_index,
                            "variant_index": new_variant_index,
                            "variant_count": len(target.get("variants", [])),
                        }, ensure_ascii=False),
                    }
            else:
                conv_store.append_messages(
                    req.conversation_id,
                    [
                        {"role": "user", "content": req.question},
                        {"role": "assistant", **variant_payload},
                    ],
                )
            # v0.5.0: persist the chain's rolling chunk window alongside
            # the messages so a backend restart preserves the +memory(N)
            # follow-up boost. No-op when the chain didn't select any
            # chunks (chitchat turns, empty index).
            try:
                mem = chain.export_session_memory(memory_key)
                if mem:
                    conv_store.set_recent_chunks(req.conversation_id, mem)
            except Exception:
                logger.exception("Failed to persist session memory.")

            # Auto-title only on the very first turn of a fresh
            # conversation. Regenerate is, by definition, not the first
            # turn, so we skip the title pass there.
            if req.regenerate:
                return
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
                    "data": _json.dumps(
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


def _message_to_schema(m: dict) -> ConversationMessage:
    """Project a stored message (possibly variants shape) to the wire
    schema. ``content`` always carries the active variant's text so old
    clients keep working; ``variants`` + ``active_variant`` are populated
    for assistant messages so v0.5.0 clients can render the pager."""
    role = m.get("role", "user")
    if role == "assistant" and isinstance(m.get("variants"), list):
        return ConversationMessage(
            role="assistant",
            content=conv_active_content(m),
            ts=m.get("ts"),
            variants=[
                MessageVariant(
                    content=v.get("content", ""),
                    ts=v.get("ts"),
                    sources=v.get("sources"),
                    retrieval_trace=v.get("retrieval_trace"),
                    retrieval_mode=v.get("retrieval_mode"),
                    standalone_question=v.get("standalone_question"),
                    chitchat=v.get("chitchat"),
                    error=v.get("error"),
                    sentence_bindings=v.get("sentence_bindings"),
                )
                for v in m["variants"]
            ],
            active_variant=m.get("active_variant", 0),
        )
    return ConversationMessage(
        role=role,
        content=m.get("content", ""),
        ts=m.get("ts"),
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
        messages=[_message_to_schema(m) for m in conv.get("messages", [])],
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


@app.patch(
    "/api/conversations/{conv_id}/messages/{message_index}/active_variant",
    response_model=ConversationDetail,
)
def set_active_variant(
    conv_id: str,
    message_index: int,
    req: SetActiveVariantRequest,
) -> ConversationDetail:
    """v0.5.0: switch which variant of an assistant message is the
    'live' one. The chain's in-memory history is also rehydrated so
    the next turn sees the chosen variant as the prior reply."""
    conv = conv_store.set_active_variant(
        conv_id, message_index, req.variant_index,
    )
    if not conv:
        raise HTTPException(
            status_code=404,
            detail="conversation, message, or variant index not found",
        )
    # Refresh chain history to mirror the new active variant. Without
    # this, the rewriter on the next turn would still see whatever
    # variant was active when the session was last hydrated.
    chain = deps.get_chain_if_loaded()
    if chain is not None:
        chain.hydrate_session(conv_id, conv.get("messages", []))
    return ConversationDetail(
        id=conv["id"],
        title=conv["title"],
        created_at=conv["created_at"],
        updated_at=conv["updated_at"],
        messages=[_message_to_schema(m) for m in conv.get("messages", [])],
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
# Heartbeat watchdog (two-mode)
#
# Mode A — IDLE (default): the frontend POSTs /api/heartbeat every ~10s
#     while the tab is alive. After HEARTBEAT_TIMEOUT_S of silence we
#     self-exit. The 60s default is sized to tolerate Chrome / Edge
#     throttling background-tab setInterval to once a minute, so a tab
#     the user has clicked away from won't fire a false shutdown.
#
# Mode B — GOODBYE: when the page is actually being unloaded (close,
#     navigate-away, real reload) the frontend hits the same endpoint
#     with ?closing=1 via navigator.sendBeacon -- this fires reliably
#     during pagehide, where regular fetch is killed. We then switch to
#     a much shorter GOODBYE_TIMEOUT_S window. If a normal ping arrives
#     before that expires (e.g. an F5 reload completes and pings resume),
#     we cancel the goodbye and stay in IDLE mode -- no false shutdown
#     on reload. Otherwise the launcher's cleanup prompt appears within
#     ~5s of the user closing the tab, instead of 60s.
#
# HUSHDOC_AUTO_SHUTDOWN=0 disables the watchdog so plain dev.sh /
# uvicorn workflows aren't ambushed by it.
# ---------------------------------------------------------------------------
HEARTBEAT_TIMEOUT_S = float(os.environ.get("HUSHDOC_HEARTBEAT_TIMEOUT", "60"))
GOODBYE_TIMEOUT_S = float(os.environ.get("HUSHDOC_GOODBYE_TIMEOUT", "5"))
_AUTO_SHUTDOWN_ENABLED = os.environ.get("HUSHDOC_AUTO_SHUTDOWN", "1") != "0"
_heartbeat_state = {
    "last_ts": 0.0,        # last regular ping
    "ever_received": False,
    "goodbye_ts": None,    # set by ?closing=1; cleared by next regular ping
}


@app.post("/api/heartbeat")
def heartbeat(closing: bool = False) -> dict:
    """Frontend ping. ``?closing=1`` switches to the short goodbye window;
    a regular ping (no query) cancels any pending goodbye (so a reload's
    pagehide -> page-mount -> ping sequence does not exit the server)."""
    _heartbeat_state["ever_received"] = True
    if closing:
        _heartbeat_state["goodbye_ts"] = time.time()
    else:
        _heartbeat_state["last_ts"] = time.time()
        _heartbeat_state["goodbye_ts"] = None
    return {"ok": True}


async def _heartbeat_watchdog() -> None:
    """Background loop: once the first heartbeat has arrived, exit either
    when the goodbye window expires or when the idle window expires.
    Sleeps 1.5s so goodbye detection latency stays sub-second past the
    GOODBYE_TIMEOUT_S threshold."""
    while True:
        await asyncio.sleep(1.5)
        if not _heartbeat_state["ever_received"]:
            continue

        goodbye_at = _heartbeat_state["goodbye_ts"]
        if goodbye_at is not None:
            elapsed = time.time() - goodbye_at
            if elapsed > GOODBYE_TIMEOUT_S:
                logger.info(
                    "Client said goodbye %.1fs ago and did not return — auto-shutdown.",
                    elapsed,
                )
                threading.Timer(0.3, lambda: os._exit(0)).start()
                return
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


@app.on_event("startup")
async def _apply_persisted_config() -> None:
    """Pull the user's saved model_path out of hushdoc_config.json (if
    any) and prime deps so the chain's first build uses it. Has to run
    BEFORE the first chat request, so an on_event handler is the right
    place. Auto-cleanup setting is read by the launcher, not by this
    process, so no startup work needed for that one."""
    deps._load_persisted_model_path()  # noqa: SLF001


# ---------------------------------------------------------------------------
# Configuration (Settings page)
# ---------------------------------------------------------------------------
def _build_config_response() -> AppConfigResponse:
    cfg = app_config.read_config()
    raw = cfg.get("model_path", "")
    valid = False
    try:
        p = Path(raw).expanduser().resolve()
        valid = p.is_file() and p.suffix.lower() == ".gguf"
    except Exception:
        valid = False
    return AppConfigResponse(
        model_path=raw,
        auto_cleanup_on_exit=bool(cfg.get("auto_cleanup_on_exit", False)),
        model_path_valid=valid,
    )


@app.get("/api/config", response_model=AppConfigResponse)
def get_config() -> AppConfigResponse:
    return _build_config_response()


@app.put("/api/config", response_model=AppConfigResponse)
async def update_config(req: AppConfigUpdateRequest) -> AppConfigResponse:
    """Persist the deltas in ``req`` and, if the model path changed,
    stop+restart llama-server against the new GGUF. Returns the full
    config snapshot afterwards so the frontend doesn't need a second
    GET to refresh the form."""
    updates: dict = {}

    if req.model_path is not None:
        # Trust-but-verify the path. Empty string -> reset to default.
        candidate = (req.model_path or "").strip()
        if not candidate:
            raise HTTPException(status_code=400, detail="model_path is empty.")
        p = Path(candidate).expanduser()
        # Allow relative paths (resolved against repo root via cwd).
        if not p.is_absolute():
            p = (Path.cwd() / p).resolve()
        else:
            p = p.resolve()
        if not p.is_file():
            raise HTTPException(
                status_code=400,
                detail=f"Model file not found: {p}",
            )
        if p.suffix.lower() != ".gguf":
            raise HTTPException(
                status_code=400,
                detail="Model file must have a .gguf extension.",
            )
        updates["model_path"] = str(p)

    if req.auto_cleanup_on_exit is not None:
        updates["auto_cleanup_on_exit"] = bool(req.auto_cleanup_on_exit)

    if not updates:
        # Nothing actually changed; just echo back the current config.
        return _build_config_response()

    app_config.write_config(updates)

    # Model swap is the only update with side effects beyond the file.
    if "model_path" in updates:
        logger.info(
            "Settings: model_path -> %s; reloading chain.",
            updates["model_path"],
        )
        try:
            await asyncio.to_thread(
                deps.reload_chain_with_model, updates["model_path"],
            )
        except Exception as exc:
            # The persisted path is already updated. Surface the load
            # error to the UI; user can fix the file and retry.
            logger.exception("Model reload failed for %s", updates["model_path"])
            raise HTTPException(
                status_code=500,
                detail=f"Saved, but failed to load new model: {exc}",
            )

    return _build_config_response()
