# 🤫 Hushdoc

**English** · [中文](README.zh-CN.md)
&nbsp;&nbsp;|&nbsp;&nbsp;
[Releases](https://github.com/Fangyuan025/hushdoc/releases) ·
[Changelog](CHANGELOG.md)

> **Chat with your documents — privately, offline, on your own machine.**

Drop in a PDF, Word doc, or even a photo of a page, and Hushdoc lets you
ask anything about it in plain English or Chinese. Answers stream in
with quoted sources, in seconds. **Nothing is uploaded** — every word
stays on your computer.

```
🛡️ Local-first    🚀 GPU-accelerated    🌍 Bilingual    🎙️ Voice optional
```

---

## What you can do with it

- **Read a 200-page paper in 30 seconds.** Drop the PDF in, ask
  *"summarize the key findings"* — get a paragraph with citations
  back to the exact pages.
- **Compare two contracts / two papers / two reports side by side.**
  Select both in the sidebar and ask *"where do these disagree?"* —
  Hushdoc balances retrieval across files so neither one drowns out
  the other.
- **Find that one fact you remember reading.** *"What did chapter 4
  say about the budget?"* — answers come back with `[file p.12]`
  citation chips.
- **OCR a phone photo of a page.** Hand-written notes, a textbook
  page someone snapped — JPG / PNG / TIFF / BMP all get OCR'd and
  indexed automatically.
- **Voice-driven Q&A.** Hold the mic button, ask out loud, hear the
  answer read back. (English only for now.)
- **Keep your work organized.** Multi-conversation history with
  auto-generated titles, just like ChatGPT — switch between threads
  in the sidebar.

---

## Why "Hushdoc"?

Most AI document tools ship your files to someone else's cloud. That
might be fine for a public PDF — it is not fine for a contract, a
medical record, an unpublished manuscript, or anything covered by
NDA. Hushdoc was built so you never have to make that trade-off.

| | Cloud RAG (ChatGPT / Claude / Gemini) | Hushdoc |
|---|---|---|
| Where are your documents stored? | Their servers | Your disk only |
| Where does inference run? | Their GPUs | Your GPU (or CPU) |
| Works on a plane / in a SCIF / off-grid? | ❌ | ✅ |
| Free after one-time model download? | ❌ | ✅ |
| You own the conversation history? | ❌ | ✅ |

The only network calls Hushdoc makes are the **one-time downloads** of
the embedding, speech-to-text, and text-to-speech models from
HuggingFace. Once those are cached, you can run completely
air-gapped — Wi-Fi off, ethernet unplugged, doesn't matter.

---

## Features at a glance

#### Documents
- **PDF, DOCX, and image** ingestion — tables, code blocks, math, and
  hand-written pages all preserved.
- **Drag-and-drop** upload. Multi-file. Replace-or-append toggle.
- **Search scope:** restrict any question to a specific subset of files
  with one click; leave it empty to search everything.

#### Chat
- **Streaming answers** with markdown, code highlighting, GFM tables,
  and proper LaTeX math rendering.
- **Inline citations** as `[file p.5]` chips that link back to the
  exact retrieved excerpt — nothing made up, nothing hidden.
- **Bilingual** (中 / EN) — Hushdoc detects your question's language
  and answers in the same one, even if your documents are in the other.
- **Multi-conversation history,** auto-titled after the first turn.
  Sidebar list, click to switch, click-to-confirm delete.

#### Voice (opt-in)
- **Push-to-talk** mic button beside the chat input. Auto-stops on
  ~1.5 s of silence so you don't have to babysit it.
- **Streaming TTS** — the answer is read aloud sentence-by-sentence
  *while it's still being generated*, no awkward pause at the end.
- **Replay** any prior answer with the 🔊 icon next to the message.

#### Polish
- **Dark mode** that's actually comfortable at night (charcoal, not
  the typical inky black).
- **Keyboard shortcuts:** `Ctrl/Cmd + K` to focus input, `Ctrl/Cmd + L`
  for new chat, `Esc` cancels.
- **One-click launcher** (`hushdoc.bat`) starts everything, opens the
  browser, and on exit asks whether to wipe your local data —
  per-category, opt-in.

---

## Quick start

You need:

- Windows 10 / 11 (Linux / macOS works via `dev.sh` — see Notes)
- [Python 3.12](https://www.python.org/downloads/release/python-3120/)
  (tick *Add to PATH* during install)
- [Node.js 20+](https://nodejs.org/) (LTS is fine)
- **~10 GB free disk** for a complete install. Roughly:
  - `.venv/` — Python deps (~6 GB; ~4.4 GB of that is PyTorch w/ CUDA)
  - `~/.cache/huggingface/` — embedding + Whisper + Kokoro + Docling
    layout/table models (~1.3 GB, downloaded lazily on first use)
  - `models/model.gguf` — default LLM (~1.2 GB)
  - `runtime/` — `llama-server.exe` + DLLs (~750 MB GPU build, ~50 MB CPU)
  - `web/node_modules/` — frontend deps (~200 MB)
- An NVIDIA GPU is **optional** — speeds up large models, but the
  default Qwen3-1.7B runs comfortably on CPU.

### Get a stable build

For a tested snapshot, download the source archive from the latest
[release](https://github.com/Fangyuan025/hushdoc/releases) (zip or
tar.gz), extract, and continue with `setup.bat` / `setup.sh` below.
For bleeding-edge features clone `master` instead.

### The easy way: two double-clicks (Windows)

```powershell
.\setup.bat        # one-time: installs deps + downloads runtime + model
.\hushdoc.bat      # every time after: launches the app
```

`setup.bat` is fully automatic and re-runnable. It will:

1. Create the Python venv (`.venv\`) and `pip install` everything
2. Run `npm install` for the frontend
3. Detect whether you have an NVIDIA GPU and pick the right
   `llama-server.exe` build into `.\runtime\`:
   - **GPU detected:** CUDA 12.4 build (~205 MB) + matching cudart
     runtime DLLs (~373 MB) — fast inference, drop-in for larger models
   - **No GPU:** CPU build (~15 MB) — works everywhere, plenty fast for
     the default 1.7B model
4. Download the default model **Qwen3-1.7B Q4_K_M** (~1.2 GB) from
   [HuggingFace](https://huggingface.co/MaziyarPanahi/Qwen3-1.7B-GGUF)
   into `.\models\model.gguf`

Each step skips itself if it's already done, so you can re-run setup
safely after `git pull`.

> **Override the GPU/CPU pick:**
> - `.\setup.bat -Cpu` forces the CPU build (smaller download, useful
>   if your CUDA install is broken).
> - `.\setup.bat -GpuBuild` forces the CUDA build even when `nvidia-smi`
>   isn't on PATH.
> - `.\setup.bat -Force` re-downloads the runtime and model (e.g. after
>   a llama.cpp upgrade). Does **not** recreate the venv.

### macOS / Linux

```bash
chmod +x setup.sh dev.sh
./setup.sh         # one-time
./dev.sh           # every time after
```

`setup.sh` mirrors `setup.bat` end-to-end: venv, npm install,
auto-detects an NVIDIA GPU on Linux (CUDA build of `llama-server`),
falls back to CPU on macOS / no-GPU machines, downloads the same
Qwen3-1.7B model. Same `--cpu` / `--gpu-build` / `--force` overrides.

> Auto-cleanup-on-exit currently lives only in the Windows `hushdoc.bat`
> flow. `dev.sh` starts the stack but you'll Ctrl+C to stop and clean
> up by hand.

### Want a different / larger model?

Drop any `.gguf` file at `.\models\model.gguf`, replacing the one
setup downloaded. Pick something that fits your RAM — for example:

- **Qwen3-4B Q4_K_M** (~2.5 GB) — better reasoning, still fits on 8 GB RAM
- **Mistral-7B-Instruct Q4_K_M** (~4.5 GB) — strong English baseline
- **Llama-3.1-8B Q4_K_M** (~4.7 GB) — excellent general model

You can also point at any other path via the `LLAMA_MODEL_PATH` env var.

> **Note on prompts:** the system prompts include Qwen3's `/no_think` soft
> switch (which suppresses Qwen3's `<think>...</think>` reasoning block).
> Non-Qwen3 chat templates ignore this token, so swapping in Llama /
> Mistral / Phi / Qwen2.5 etc. **just works** — the 9-character prefix
> is silently dropped by their tokenizers. A separate streaming filter
> strips any `<think>` blocks that do leak through (relevant for
> reasoning models like DeepSeek-R1 / QwQ), so the user-visible answer
> is always clean regardless of model family.

### Manual setup (if you'd rather not use the script)

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
cd web && npm install && cd ..
# Then download a llama-server.exe and a .gguf yourself, place at
# .\runtime\llama-server.exe and .\models\model.gguf, and launch:
.\hushdoc.bat
```

---

After setup, Hushdoc opens in your default browser at
<http://localhost:5173>. Drop a PDF in the sidebar and ask away.

> **First answer takes 10–20 s** while the model warms up. Subsequent
> answers stream in within a second.

---

## Tips

- **Big documents work best when you ask focused questions.** "What
  does section 3 conclude?" beats "tell me everything in this paper."
- **Use scope when you have many files.** Selecting 1–3 specific
  documents in the *Search scope* panel makes answers tighter and
  faster.
- **Follow-ups work naturally.** *"Can you elaborate?"* or *"为什么?"*
  is understood in context — Hushdoc rewrites them into standalone
  questions internally.
- **The Sources panel only shows what was actually cited.** If
  Hushdoc didn't use a chunk, it won't pad the citation list with it.
- **Closing the browser auto-stops the server** and offers to clean
  up your local data (conversations / uploads / vector index) — say
  *no* to keep things, *y* to wipe.

---

## Under the hood

For the curious — Hushdoc isn't just "embed and pray." A few engineering
choices that make it actually usable:

- **Per-document summary cache.** Pure top-k retrieval can't answer
  *"which of these is about ML?"* because chunks alone don't carry
  document themes. Hushdoc summarises each file once at ingest and
  prepends a *Documents in scope* overview to every prompt.
- **Balanced multi-doc retrieval.** When 2+ files are selected, the
  retrieval budget is split per filename so a wordy doc can't crowd
  the others out.
- **Cross-encoder reranker.** Bi-encoder fetches a wider candidate
  set; a stronger cross-encoder rescores them. Spend the latency
  where it matters — final ordering.
- **Per-turn language directive.** Small models drift toward whatever
  the documents are written in. Hushdoc detects your language and
  pins the answer language at the prompt's most-recent-token slot —
  the one small models obey most reliably.
- **Inline reasoning stripper.** Reasoning-model `<think>` blocks
  are removed *while streaming*, even when the open or close tag is
  split across two token chunks.
- **Heartbeat-driven shutdown.** The browser pings a heartbeat every
  few seconds. Close the tab, the server self-exits, and the
  launcher's cleanup prompt kicks in.

Stack: **FastAPI** (Python 3.12) + **React 19** + **Vite** +
**Tailwind / shadcn**, talking to **llama.cpp**'s standalone
`llama-server` over its OpenAI-compatible HTTP API. Vector store is
**ChromaDB**. Document parsing is **IBM Docling**. Voice uses
**Whisper-base.en** (ASR) and **Kokoro-82M** (TTS).

---

## Project layout

```
hushdoc/
├── server/          FastAPI backend (routes, conv store, SSE adapter)
├── web/             React + Vite frontend (components, hooks, lib)
├── ingest.py        Docling parsing + HybridChunker
├── vector_store.py  ChromaDB + balanced retrieval
├── reranker.py      cross-encoder over-fetch + rescore
├── llm_chain.py     RAGChain — streaming, scope, follow-up, voice
├── llama_server.py  llama-server.exe lifecycle manager
├── doc_summaries.py per-file summary cache
├── voice.py         Whisper ASR + Kokoro TTS
├── setup.bat / .sh  one-time installer (deps + runtime + model)
├── hushdoc.bat      Windows one-click launcher with cleanup prompt
├── dev.sh / dev.ps1 plain dev launchers (no auto-cleanup)
├── VERSION          read by /api/health and shown in the UI footer
└── CHANGELOG.md
                     (gitignored: runtime/  models/*.gguf)
```

---

## Notes

- **Linux / macOS users:** see the *macOS / Linux* section above —
  `setup.sh` then `dev.sh`. Auto-cleanup currently lives in the
  `.bat` / `.ps1` flow only.
- **CPU-only** also works — set `n_gpu_layers=0` in `LLMConfig`. First
  token will take longer; quality is identical.
- **Air-gapped install:** pre-download the embedding (`all-MiniLM-L6-v2`),
  Whisper-base.en, and Kokoro-82M models on a connected machine, copy
  the HuggingFace cache (`~/.cache/huggingface`) over, and you're set.
- **Voice mode is English only** for now — Whisper-base.en and
  Kokoro-82M are both English-only models. The chat itself is fully
  bilingual; you can type in Chinese any time.

---

## License

MIT — see [`LICENSE`](LICENSE).
