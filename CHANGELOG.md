# Changelog

All notable user-visible changes to Hushdoc. This project follows
[Semantic Versioning](https://semver.org). 0.x means breaking changes can
land between minor versions while we converge on 1.0.

## [0.1.0] — 2026-05-08

First public release. Local-only PDF / DOCX / image RAG assistant with
streaming chat, citation-anchored sources, multi-conversation history,
and one-click installer.

### Document chat
- Ingest **PDF, DOCX, JPG / PNG / TIFF / BMP** through IBM Docling
  with layout preservation, table structure, LaTeX math, and automatic
  RapidOCR for image inputs.
- **Streaming token-by-token answers** with markdown, code highlighting,
  GFM tables, and KaTeX-rendered LaTeX.
- **Inline citations** as `[file p.N]` chips that map back to the
  retrieved excerpt — only chunks the answer actually used appear in
  the Sources panel.
- **Bilingual** (中 / EN) — language detected per turn; replies always
  match the user's language even when the source documents are in the
  other one.
- **Multi-conversation history** with auto-generated titles after the
  first turn. Sidebar list, click-to-switch, click-to-confirm delete.

### Retrieval that actually works on real workloads
- **Per-document summary cache** prepended to the prompt as
  *Documents in scope* — answers high-level questions like "which one
  is about ML?" that pure top-k can't.
- **Balanced multi-doc retrieval** when 2+ files are in scope, so a
  wordy doc can't crowd the others out on cross-document questions.
- **Cross-encoder reranker** (ms-marco-MiniLM-L-6-v2) over an
  over-fetched bi-encoder candidate set.
- **Chitchat / RAG router** with strict regex patterns (CN + EN) and
  end-of-message anchors so doc queries don't get short-circuited.
- **Defensive standalone-query rewrite** with fallback to the raw
  question on suspicious rewrites; short follow-ups (`why?`, `为什么?`)
  get a snippet of the previous assistant message appended.

### Voice mode (English-only, opt-in)
- Browser-side VAD with auto-stop after ~1.5 s of silence.
- Whisper-base.en for speech-to-text, Kokoro-82M for text-to-speech,
  both running on CPU and lazy-loaded.
- **Sentence-buffered streaming TTS** — audio plays sentence-by-sentence
  while the answer is still generating; no 5–15 s gap of dead air.
- Per-message replay icon for prior answers.

### Lifecycle and polish
- **One-click `setup.bat` / `setup.sh`** auto-detects an NVIDIA GPU,
  downloads the matching `llama-server` build (CUDA 12.4 + cudart
  bundle, or CPU), and downloads the default Qwen3-1.7B Q4_K_M model.
  Visible progress bar via `curl --progress-bar`, hard 30-min timeout,
  60-second stall detection.
- **One-click `hushdoc.bat`** launcher spawns the stack, opens the
  browser, and on exit runs a per-category cleanup prompt
  (conversations / uploads / vector index).
- **Heartbeat-driven auto-shutdown.** Browser pings `/api/heartbeat`
  every 10 s; on `pagehide` the frontend fires a `closing=1` beacon
  via `navigator.sendBeacon` so the server self-exits within ~5 s of
  a real close. Backgrounded tabs survive the 60 s idle window.
- **ChatGPT-style empty state** with click-to-fill suggestion cards.
- **Dark mode** charcoal palette, not inky black.
- **Keyboard shortcuts:** `Ctrl/Cmd+K` focus input, `Ctrl/Cmd+L` new
  chat, `Esc` cancels.

### Robustness
- ChromaDB collection self-heals when replaced by a concurrent process
  (no more `NotFoundError` on stale UUIDs after a `replace existing` upload).
- Inline `<think>` reasoning-block stripper FSM survives split-across-
  chunk open / close tags.
- Streaming SSE parser handles fetch-based POST bodies (EventSource
  doesn't accept those).

### Stack
- **Backend:** FastAPI (Python 3.12), llama.cpp standalone `llama-server`
  via OpenAI-compatible HTTP, ChromaDB, IBM Docling.
- **Frontend:** React 19, Vite, Tailwind v4, shadcn/ui, TanStack Query.
- **Models:** Qwen3-1.7B Q4_K_M default (auto-downloaded);
  `sentence-transformers/all-MiniLM-L6-v2` embedding;
  `cross-encoder/ms-marco-MiniLM-L-6-v2` reranker.

[0.1.0]: https://github.com/Fangyuan025/hushdoc/releases/tag/v0.1.0
