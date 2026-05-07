# 🤫 Hushdoc

> **A fully-local document assistant. Every word stays on your machine.**

PDF / DOCX / image RAG with GPU-accelerated streaming, multi-conversation
memory, and optional voice I/O — all running offline. After a one-time
HuggingFace download for the embedding / ASR / TTS models, Hushdoc can
operate completely air-gapped.

---

## Highlights

Most RAG demos stop at "embed → top-k → prompt." Hushdoc tackles the
failure modes that show up the moment real users start asking real
questions:

- **Per-document summary cache** — vanilla top-k can't answer
  *"which one is about ML?"* or *"summarize this paper."* Ingest emits
  a 2–3 sentence summary per file (cached in `summaries.json`) and
  prepends a *Documents in scope* overview to every prompt.
- **Balanced multi-document retrieval** — when 2+ files are in scope the
  retrieval budget is split *per-filename* so one semantically dominant
  doc cannot starve the others. *"What's common between these two?"*
  becomes answerable.
- **Citation-filtered Sources panel** — the model is told to cite as
  `[file p.<page>]`; a regex extracts the citations after streaming and
  the UI surfaces only the chunks the answer actually used.
- **Per-turn language directive** — small models drift toward the
  language of the source corpus. Each turn detects `zh`/`en` from the
  user message and splices the directive *immediately before* `Answer:`
  — the most-recent-token slot, which small models obey most reliably.
- **Defensive query rewrite + follow-up boost** — the standalone-query
  rewriter falls back to the raw question when its output looks corrupt
  (SQL leakage, mid-`<think>` truncation, repeated past turns); for very
  short follow-ups (`why?`, `为什么?`) it appends a snippet of the
  previous assistant message to the search query as a safety net.
- **Inline `<think>` stripping FSM** — handles reasoning-model output
  even when the open or close tag is split across two streamed token
  chunks; the user never sees scratch reasoning.
- **Sentence-buffered streaming TTS** — voice mode flushes each completed
  sentence to Kokoro-82M as it arrives, so audio starts playing while
  the answer is still being generated. No 5–15 s gap of dead air.
- **Heartbeat-driven auto-shutdown** — closing the browser stops the
  client heartbeat; the backend self-exits, the launcher detects it,
  and a cleanup prompt runs — one-click lifecycle from spawn to wipe.
- **Subprocess `llama-server` over `llama-cpp-python`** — Windows CUDA
  wheels for the Python binding lag upstream `llama.cpp` by months and
  break on newer GGUF architectures. Talking to the standalone binary
  via its OpenAI-compatible HTTP API gets the latest model support for
  free.
- **Multi-format ingestion** — PDF, DOCX, JPG / PNG / TIFF / BMP routed
  through [IBM Docling](https://github.com/DS4SD/docling): layout +
  TableFormer for PDFs, native XML for DOCX, RapidOCR for images. All
  three converge on the same `DoclingDocument` so the chain has zero
  format-special-casing.

---

## Architecture

```
┌──────────────────────────────────┐  HTTP / SSE   ┌────────────────────────────┐
│ web/  React + Vite + Tailwind    │ ─────────────▶│ server/  FastAPI           │
│  ChatPane (streaming markdown)   │               │  /api/chat        (SSE)    │
│  Sidebar (chats · docs · scope)  │◀───────────── │  /api/documents/* /upload  │
│  Browser VAD + streaming TTS     │  /api/* proxy │  /api/voice/{transcribe,   │
└────────────────┬─────────────────┘               │            synthesize}     │
                 │                                 │  /api/heartbeat (watchdog) │
                 ▼ Vite dev proxy                  └─────────────┬──────────────┘
        http://localhost:8000                                    │ imports
                                                                 ▼
                                            ingest · vector_store · llm_chain
                                            doc_summaries · voice · reranker
                                            llama_server (subprocess manager)
                                                                 │
                                                                 ▼
                                            llama-server.exe (GPU CUDA, GGUF)
                                            ChromaDB · summaries.json sidecar
```

### Per-turn flow inside `llm_chain.py`

```
question
  │
  ├─ language detect (zh / en)
  ├─ chitchat router ──► friendly prompt (skip retrieval)
  ▼
standalone-query rewrite  ── defensive fallback on corrupt output
  ▼
retrieval  (balanced per-filename when |scope| ≥ 2)
  ▼
cross-encoder rerank  (over-fetch ×3, rescore, take top-k)
  ▼
prompt = [ scope summary overview · retrieved chunks · history · directive ]
  ▼
stream tokens  ── inline <think> stripper FSM ──► SSE ──► browser
  ▼
done event (answer · sources · standalone_question)
  └─ on first turn: auto-title the conversation
```

---

## Quick start

```powershell
# 1. Python env (3.12 — 3.13/3.14 lack scikit-network wheels)
py -3.12 -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt

# 2. Frontend deps
cd web && npm install && cd ..

# 3. llama-server.exe + a GGUF model
#    Download a CUDA build from https://github.com/ggerganov/llama.cpp/releases
$env:LLAMA_SERVER_EXE = "C:\path\to\llama-server.exe"
#    Drop any GGUF at ./models/model.gguf  (or set LLAMA_MODEL_PATH)

# 4. Launch
.\hushdoc.bat                 # double-click works too — opens browser,
                              # auto-cleans on exit (per-category prompt)
```

Open <http://localhost:5173/>.

> **Other launchers:** `dev.sh` (bash / Git Bash / Linux / macOS),
> `dev.ps1` (PowerShell, no auto-cleanup) — both skip the heartbeat
> watchdog so manual shutdown is on you.

---

## Project layout

```
hushdoc/
├── server/                FastAPI backend
│   ├── main.py            routes + heartbeat watchdog
│   ├── deps.py            lazy singletons (chain · store · ingestor)
│   ├── conversations.py   per-conv JSON store with index
│   └── streaming.py       chain → SSE adapter
├── web/                   React + Vite frontend
│   └── src/
│       ├── components/    ChatPane · Sources · Sidebar · ConversationList
│       ├── hooks/         useChat · useVoice · useDocuments · useScope
│       └── lib/           api · audio · vad · utils
├── ingest.py              Docling parsing + HybridChunker
├── vector_store.py        ChromaDB + balanced retrieval
├── reranker.py            cross-encoder over-fetch + rescore
├── llm_chain.py           RAGChain (streaming · scope · follow-up · voice)
├── llama_server.py        llama-server.exe lifecycle (start · health · stop)
├── doc_summaries.py       per-PDF summary cache
├── voice.py               Whisper-base.en ASR · Kokoro-82M TTS
├── evaluate.py            offline Ragas scoring (local LLM as judge)
├── hushdoc.bat / .ps1     one-click launcher with cleanup prompt
└── dev.sh / dev.ps1       plain dev launchers (no auto-cleanup)
```

---

## Configuration

All knobs are env-var overridable so no code edits required for the
common paths.

| Env var | Default | Purpose |
|---|---|---|
| `LLAMA_SERVER_EXE` | `C:\Users\...\llama-server.exe` | Path to the binary |
| `LLAMA_MODEL_PATH` | `./models/model.gguf` | GGUF model file |
| `HUSHDOC_AUTO_SHUTDOWN` | `1` | Set `0` to disable heartbeat watchdog |
| `HUSHDOC_HEARTBEAT_TIMEOUT` | `15` | Seconds of silence before self-exit |

Programmatic knobs in `LLMConfig` (`llm_chain.py`) and `ServerConfig`
(`llama_server.py`) cover context window, GPU layers, sampling
temperature, and llama-server's `--parallel` slot count for Ragas
fan-out.

---

## CLI utilities

```powershell
# Index a file from the command line (no UI)
python vector_store.py path\to\doc.pdf
python vector_store.py path\to\doc.docx
python vector_store.py path\to\photo.jpg     # OCR via RapidOCR

# End-to-end smoke test (one chat turn over the current index)
python smoke_test.py

# Exercise every HTTP route (requires uvicorn already running)
python test_api.py

# Offline Ragas eval — local LLM judges itself
python evaluate.py --test-set eval_dataset.json
```

---

## Engineering notes

A few decisions that aren't obvious from the code:

**Two processes, one origin.** Vite proxies `/api/*` to FastAPI on
`:8000` so the browser only ever sees one origin and the backend never
needs CORS in production. A future container would `npm run build` to
`web/dist/` and have FastAPI serve it directly.

**SSE over WebSocket.** Chat is one-way (server → client) which is a
clean SSE fit. `EventSource` doesn't support POST bodies though, so the
React client uses `fetch` + `ReadableStream` and hand-parses SSE
frames — that lets the question, scope, and conversation id ride in the
request body instead of awkward URL params.

**Auto-create conversation on first send.** Clicking *+ New chat* does
not create a server-side record; only the first user message does. A
`skipHydrationFor` handshake between `App.tsx` and `useChat` prevents
the hydration GET from clobbering the optimistic stream that's already
in flight against the just-minted conversation id.

**Cross-encoder reranker as an opt-in stage.** `MiniLM-L6-v2` is fast
but coarse. After the bi-encoder retrieval over-fetches `k×3`, a
`ms-marco-MiniLM-L-6-v2` cross-encoder rescores and we keep the top
`k` — paid for in latency where it matters most (final ordering).

**Voice mode is feature-flagged off.** Loading Whisper + Kokoro adds
~1.5 GB of RAM and a long first-call latency; users who don't want
voice never pay for it. When on, browser-side VAD auto-stops on 1.5 s
of silence and re-encodes captured audio to PCM WAV in-process — no
ffmpeg dep.

---

## License

MIT.
