# Changelog

All notable user-visible changes to Hushdoc. This project follows
[Semantic Versioning](https://semver.org). 0.x means breaking changes can
land between minor versions while we converge on 1.0.

## [0.2.0] — 2026-05-13

Theme: **"Bring it in (offline), prove it back, breathe."** Wider local
content coverage + a unified, decluttered sidebar + a right-side drawer
that shows the full retrieval trace, not just the chips.

### Local content coverage
- **Folder ingest** — pick or drop a whole directory; every `.pdf` /
  `.docx` / `.md` / `.txt` / image inside it gets queued and indexed
  with per-file SSE progress. Everything stays on disk.
- **TXT / Markdown** — skips the Docling layout pipeline entirely.
  A 5 KB note no longer waits for the 770 MB layout model to load.
  Headings ( `# / ## / ###` ) are tracked so chunk metadata still
  carries useful structure.
- **Paste text** — new modal under *Add to library → Paste text*.
  Pasted content is split client-side, ingested in memory, never
  written to `data/uploads/`. Filename is auto-derived from the first
  heading or non-empty line, capped at 60 chars.

### Library — Documents + Search-scope merged
- Single **Library** panel replaces the old paired Documents +
  Search-scope sections. Each row:
  - `[☑]` scope checkbox (was the separate scope panel before)
  - filename + per-file metadata line (`chunks · size · source · age`)
  - hover-trash → click-to-confirm single-file delete (was wipe-all
    only before)
- **Source-kind badges** (`uploaded` / `folder` / `typed` /
  `unknown`) so the user can tell apart drag-drops, folder picks,
  and pasted notes at a glance.
- **`+ Add to library` popover** consolidates Add files / Add folder
  / Paste text into one entry point so the panel stays compact when
  the user is just reading.

### Verifiable retrieval — Sources drawer
- Citation chips on the assistant bubble are now click targets.
  Clicking opens a **right-side drawer** with two tabs:
  - **Sources** — cited chunks with snippets; the chip you clicked
    auto-scrolls into view and highlights.
  - **Retrieval** — the full bi-encoder candidate set with
    `rank_before → rank_after`, cross-encoder score, kept / dropped /
    cited badges. Hover the row, see what the reranker did.
- Inline accordion is gone; replaced by the drawer so the message
  flow doesn't grow vertically as v0.2.0 piles features into it.
- Citation regex broadened beyond `.pdf` to also match `.docx` /
  `.md` / `.txt` / image extensions — previously, citations for
  pasted text never matched and Sources silently fell through to
  *all chunks*.

### Sidebar layout + density
- **Collapsible sections** with `▾`/`▸` toggles; state persisted to
  `localStorage` so the user's layout sticks across reloads. Voice
  defaults to collapsed since most users don't enable it.
- **Header slimmed** — `local-only document assistant` subtitle
  dropped, padding cut from `py-2.5` to `py-1.5`, HealthPill
  compacted from `px-3 py-1 text-xs` to `px-2 py-0.5 text-[11px]`.
  Net ~14 px of fixed chrome reclaimed.

### Answer-level productivity (the C theme)
- **Regenerate answer** — `↻` button on completed assistant messages
  re-runs the same user question so a different reranker order or
  decode pass can produce a better answer.
- **Copy answer** — `📋` button (markdown preserved, the same string
  the model produced). 1.2 s success state via the icon swap.

### Robustness
- `vector_store.delete_by_filename()` — backs the new per-file delete
  endpoint, shares the same transient-error retry + collection
  refresh as the rest of the read path.

### Stack additions
- `langchain_text_splitters.RecursiveCharacterTextSplitter` (already
  transitive via `langchain-chroma`) for the TXT / MD chunking path —
  no new top-level requirement.

[0.2.0]: https://github.com/Fangyuan025/hushdoc/releases/tag/v0.2.0

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
