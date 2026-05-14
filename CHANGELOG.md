# Changelog

All notable user-visible changes to Hushdoc. This project follows
[Semantic Versioning](https://semver.org). 0.x means breaking changes can
land between minor versions while we converge on 1.0.

## [0.4.0] — 2026-05-13

Multi-turn follow-ups now work. Reported pain point: in a single
conversation, follow-up questions ("why did they choose X?", "and
the participants?", bare "why?") frequently hit "I don't know based
on the provided documents" or lost continuity with earlier turns,
even when the answer was sitting in chunks the chain had just
retrieved one turn ago. Root-causing surfaced four architectural
gaps; all four are fixed in this release.

### Fixed — follow-up detection
`_retrieve` previously triggered the "append last assistant turn to
the search query" boost ONLY when `len(question) < 15`. That picked
up bare "why?" but missed "Why did they choose mixed-methods?" (34
chars) -- which is just as anchored to prior turn via the unbound
"they".

Replaced with `is_likely_followup(text, has_history)` covering three
signals, any of which fires the boost:
  1. < 15 chars (the old behavior)
  2. < 120 chars AND contains a pronoun (en: they/them/it/this/those
     /he/she/why/how + "the {authors,paper,study,document,finding,
     result}", zh: 他们/她们/它们/他/她/它/这/那/为什么/怎么/为何
     /这篇/那篇/这项/作者/论文/研究)
  3. starts with a discourse marker (and/so/but/also/then/继续/然后
     /那么/另外)

### Fixed — retrieval memory across turns
The bi-encoder + reranker stack was stateless: each turn rebuilt its
candidate pool from scratch via similarity_search. If turn 1's
methodology section was relevant to turn 2's follow-up but the new
query's vector similarity scored those chunks too low to make the
top-k, the answer model never saw them again -- so it had to fall
back to chitchat or IDK.

New: `RAGChain._session_chunks: Dict[str, deque[Document]]` keeps
a 12-chunk rolling window per session. `_retrieve` mixes them into
the candidate pool BEFORE the cross-encoder rerank, deduped by
(filename, chunk_index) and gated by the per-call scope so a user
who just swapped filename scope doesn't drag chunks from the old
scope along. The reranker is the safety net: irrelevant carry-over
chunks score low and get dropped; relevant ones survive the cut.

A new `+memory(N)` suffix on `retrieval_mode` (e.g.
`topk+rerank+memory(6)`) surfaces this in the Retrieval-trace tab
so you can see how many chunks crossed over from earlier turns.

### Fixed — IDK threshold was too binary
`ANSWER_SYSTEM` rule 1 used to say "If the answer is not present in
the context, reply exactly: 'I don't know based on the provided
documents.'" Prior assistant turns weren't acknowledged as valid
context, so any follow-up whose answer required pulling from what
was just discussed (not from a fresh retrieval) defaulted to IDK.

Rewrote as a tiered hierarchy:
  (a) document context (primary)
  (b) prior assistant turns in chat_history (secondary, valid for
      building on facts already established this conversation)

IDK is reserved for the case where BOTH are silent. Outside-world
knowledge / guessing remains forbidden.

### Fixed — standalone query was used only for retrieval
The condense chain happily rewrote "Why did they choose mixed-
methods?" into "Why did the study choose a mixed-methods approach?"
but that resolved form was thrown away after retrieval -- the
answer prompt still received the raw "they"-anchored question.
Small models (Qwen3-1.7B) often failed to redo the pronoun
resolution themselves while also juggling the doc context.

Now: when the rewriter produces a meaningfully different standalone
form, the answer prompt receives a `{expanded_query_hint}` line
above the user's raw text:
    "(Follow-up note: the user's expanded intent, with pronouns
     resolved against prior turns, is: '<standalone>'. Their actual
     message is below.)"
Rendered as "" when raw == rewrite so direct questions don't see
this overhead.

### Verified — 4-turn smoke

Turn 1: "What is this paper about?"
  -> mode=topk+rerank, 6 cited sources, grounded summary
Turn 2: "Why did they choose mixed-methods?"
  -> standalone="Why did the study choose mixed-methods approach?"
  -> mode=topk+rerank+memory(5)
  -> grounded answer explaining the qual+quant combo rationale
Turn 3: "And what were the participants like?"
  -> standalone="What were the participants like?"
  -> mode=topk+rerank+memory(7)
  -> grounded answer with the actual demographic table from the paper
Turn 4: "why?"
  -> standalone="Why does the study use a mixed-methods approach?"
  -> mode=topk+rerank+memory(6)
  -> grounded answer

Zero IDKs. Pre-v0.4.0 the same trace on the same PDF / same model
typically hit IDK on turn 2 onward.

[0.4.0]: https://github.com/Fangyuan025/hushdoc/releases/tag/v0.4.0

## [0.3.0] — 2026-05-13

User-tunable Settings page. Two persisted options, two surfaces, one
shared file.

### Added — Settings modal (gear icon in header)
- **Custom model path.** Type any GGUF path, hit *Save*, and the
  backend stops the running `llama-server.exe` + starts a fresh one
  against the new model + rebuilds the RAG chain — all before the PUT
  returns, so the toast "Model swapped" actually means it's loaded
  and your next chat hits the new weights. Validated up front: the
  PUT 400s if the path doesn't exist or isn't `.gguf`, and the
  persisted config never gets touched on rejection.
- **Auto-cleanup on exit.** When checked, the launcher
  (`hushdoc.ps1`) skips its three per-category "Delete X?" prompts on
  exit and just wipes `chat_history/` + `data/uploads/` + `chroma_db/`
  silently, then closes the terminal window with no final pause.
  Unchecked (default) keeps the v0.2.x prompt behavior.
- Path-validity pip in the modal — green check if the GGUF file is
  present, red triangle if it's gone, refreshed every time the modal
  opens.

### Added — backend
- New module `server/config.py` — atomic JSON read/write of
  `./hushdoc_config.json` (tempfile + `os.replace`). Defaults fill
  in for any missing key so the file can be hand-edited without
  breaking the schema; unknown keys are preserved on read, stripped
  on write.
- New endpoints:
  - `GET /api/config` — current settings + a `model_path_valid` hint.
  - `PUT /api/config` — partial update; only the keys you send get
    changed. Triggers `deps.reload_chain_with_model()` if model_path
    moved.
- `deps.reload_chain_with_model(path)` — kills the existing
  llama-server subprocess (drops the `_SHARED` singleton), nulls the
  cached `_chain`, sets the new path explicitly via `LLMConfig(...)`
  on rebuild (env var alone wouldn't work because the dataclass
  default was frozen at module import), and pre-warms by calling
  `get_chain()` so the user's next message doesn't pay cold-start.
- `deps._load_persisted_model_path()` — `@app.on_event("startup")`
  reads the saved config and primes the chain to use the user's
  preferred model from the very first request.

### Added — launcher
- `hushdoc.ps1` cleanup phase now reads `hushdoc_config.json` and
  branches:
  - `auto_cleanup_on_exit: true` → silent wipe of all three dirs,
    then close immediately (no `Start-Sleep`).
  - default → existing per-category `[y/N]` prompts; help text
    mentions the toggle so users learn it exists.

### Build / housekeeping
- `hushdoc_config.json` added to `.gitignore` — user-specific, never
  committed.
- TS check + production build pass clean. 19 backend routes registered.

### Test trace
- `PUT /api/config` with same model_path: chain reload + new
  llama-server up + post-reload chat streams 25 tokens. ~6 s
  round-trip on Qwen3-1.7B.
- `PUT /api/config` with bogus path: HTTP 400, config file untouched,
  running llama-server unaffected.
- `auto_cleanup_on_exit: true` flushed through to JSON file with
  correct shape.

[0.3.0]: https://github.com/Fangyuan025/hushdoc/releases/tag/v0.3.0

## [0.2.2] — 2026-05-13

Ingest UX: cancelable + noticeably faster on multi-file batches.

### Added
- **Cancel ingest button.** During an in-flight upload the Library's
  `+ Add to library` morphs into a red `Cancel` next to it. Click it
  and:
  - the SSE fetch is aborted client-side (no more event traffic to
    process)
  - `POST /api/documents/upload/cancel` flips a server-side flag the
    ingest loop checks BETWEEN files, so the current file's Docling
    parse (in a worker thread, can't be aborted mid-pass) finishes
    cleanly but the rest of the queue is dropped
  - the backend emits a `cancelled` SSE event with the
    `completed / total / total_chunks` counts; the UI marks any still-
    queued rows as `cancelled` and shows a friendly toast
- New SSE event kind: `cancelled` (peer of `all_done`).
- New endpoint: `POST /api/documents/upload/cancel`.

### Changed
- **Per-file LLM summary is now deferred** to a background asyncio
  task that fires after the SSE stream closes. The summary used to
  run inline AFTER chunking + embedding for every file, adding 3-10 s
  per doc to the critical path on Qwen3-1.7B. For a 10-file folder
  pick that's 30-100 s the user used to stare at the progress bar
  for. Now `file_done` arrives the moment the chunks are in chroma;
  the summary lands silently in `doc_summaries.json` ~5-15 s later
  per file. Verified end-to-end: a 91-chunk PDF that previously took
  ~40 s now reports `all_done` at ~32 s, with the summary appearing
  in `/api/documents` within ~10 s after that.
- `file_done` SSE payload's `summary` field is now always `""`
  (frontend doesn't currently surface summaries during ingest, so
  this is invisible to users). The cache still gets populated; the
  chain's *Documents in scope* prompt overview works as before.

### Internals
- `_ingest_cancel: asyncio.Event` cleared at the top of each
  `_ingest_files_streaming` invocation, set by the cancel endpoint.
  Idempotent — repeated cancels are no-ops.
- New `_backfill_summaries(items)` coroutine spawned via
  `asyncio.create_task` so it survives the SSE stream closure. Runs
  serially so it doesn't compete with the user's first chat turn for
  the single llama-server slot.
- `useDocuments.upload` now passes an `AbortController.signal` to the
  upload fetch; the new `useDocuments.cancelUpload` fires the abort
  and the server-side cancel POST together.
- `AbortError` is filtered out of the upload-error toast path —
  cancel isn't an error.

[0.2.2]: https://github.com/Fangyuan025/hushdoc/releases/tag/v0.2.2

## [0.2.1] — 2026-05-13

Hotfix for v0.2.0. **If you're on 0.2.0, upgrade now** — the Library
panel had a TypeError that crashed the React render tree on every
mount, leaving users staring at a blank page. Combined with the
unbounded folder ingest, an accidental "wrong folder" pick on a fresh
v0.2.0 install could lock the machine.

### Fixed
- **Library render crash.** `Library.tsx` called `.has()` / `.size` on
  `scope.selected`, but `useScope` returns a plain `string[]` — not a
  `Set`. Each mount threw `TypeError: scope.selected.has is not a
  function` before the file list could paint, which React handled by
  unmounting the subtree and leaving the page blank. Switched the
  call sites to `.includes()` / `.length`.

### Added
- **Folder-ingest safety net.** `Library.onPickFolder` now caps a
  single folder pick at 50 ingestible files. Anything bigger surfaces
  a native `confirm()` dialog with the file count before kicking off
  Docling. Prevents the "I accidentally picked my entire Desktop"
  scenario from queuing hundreds of Docling parses and writing GBs to
  `chroma_db` before the user can intervene.
- **Top-level `ErrorBoundary`** wrapping `<App/>`. A render exception
  no longer unmounts the entire tree silently — the user sees a
  recovery panel with the error message + a Reload button. Console
  still gets the full stack for bug reports.

### Build
- `tsconfig.app.json` gained `"ignoreDeprecations": "6.0"` so
  `npm run build` no longer errors on the `baseUrl` TS 7 deprecation
  warning (which previously broke the build silently for
  contributors running `npm run build` instead of `npx tsc --noEmit`).
- Two `@ts-expect-error` directives on the `webkitdirectory` /
  `directory` input attributes removed (modern @types/react accepts
  them, so they were flagged as unused-suppression errors).

### Recovery notes for anyone who hit the freeze
1. Kill any leftover `python.exe`, `node.exe`, and `llama-server.exe`
   via Task Manager (the heartbeat watchdog only stops these when the
   browser tab gracefully closes; a frozen page can't issue that).
2. Inspect `chroma_db/` + `data/uploads/` — if either is unexpectedly
   large (>100 MB), an unintended folder ingest started writing. Wipe
   them and re-ingest the files you actually want.
3. Pull `v0.2.1` and re-run `hushdoc.bat` — fresh boot, no further
   action needed.

[0.2.1]: https://github.com/Fangyuan025/hushdoc/releases/tag/v0.2.1

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
