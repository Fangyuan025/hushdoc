# 🤫 Hushdoc

<p align="center">
  <a href="https://github.com/Fangyuan025/hushdoc/releases"><img alt="Release" src="https://img.shields.io/github/v/release/Fangyuan025/hushdoc?style=for-the-badge&color=2ea44f"></a>
  <a href="LICENSE"><img alt="MIT" src="https://img.shields.io/badge/license-MIT-yellow.svg?style=for-the-badge"></a>
  <a href="#why"><img alt="Local-only" src="https://img.shields.io/badge/local--only-1f6feb.svg?style=for-the-badge&logo=ghostery&logoColor=white"></a>
  <a href="README.zh-CN.md"><img alt="Bilingual" src="https://img.shields.io/badge/中文-7c3aed.svg?style=for-the-badge&logo=googletranslate&logoColor=white"></a>
</p>

<p align="center">
  <a href="https://www.python.org/"><img alt="Python 3.12" src="https://img.shields.io/badge/python-3.12-3776AB.svg?style=for-the-badge&logo=python&logoColor=white"></a>
  <a href="https://fastapi.tiangolo.com/"><img alt="FastAPI" src="https://img.shields.io/badge/FastAPI-009688.svg?style=for-the-badge&logo=fastapi&logoColor=white"></a>
  <a href="https://react.dev/"><img alt="React 19" src="https://img.shields.io/badge/React-19-61DAFB.svg?style=for-the-badge&logo=react&logoColor=000"></a>
  <a href="https://github.com/ggml-org/llama.cpp"><img alt="llama.cpp" src="https://img.shields.io/badge/llama-cpp-FF6B6B.svg?style=for-the-badge"></a>
  <a href="https://www.trychroma.com/"><img alt="ChromaDB" src="https://img.shields.io/badge/ChromaDB-FFCD42.svg?style=for-the-badge"></a>
</p>

<p align="center">
  <b>English</b> · <a href="README.zh-CN.md">中文</a>
  &nbsp;|&nbsp;
  <a href="https://github.com/Fangyuan025/hushdoc/releases">Releases</a> ·
  <a href="CHANGELOG.md">Changelog</a>
</p>

> **Chat with your documents — privately, offline, on your own machine.**

Drop in a PDF, DOCX, EPUB, or even a phone photo of a page. Ask anything
in English or Chinese. Answers stream in with inline citations and an
in-app PDF viewer that highlights the exact source passage in yellow.
**Nothing leaves your machine.**

`🛡️ Local-first` · `🚀 GPU-accelerated` · `🌍 中 / EN` · `🎙️ Voice (en)`

---

## Why <a id="why"></a>

Most AI document tools ship your files to someone else's cloud. That's
fine for a public PDF — not fine for a contract, an unpublished
manuscript, or anything covered by NDA. Hushdoc was built so you never
make that trade-off.

| | Cloud RAG | Hushdoc |
|---|---|---|
| Documents stored on | Their servers | Your disk |
| Inference runs on | Their GPUs | Your GPU / CPU |
| Works air-gapped? | ❌ | ✅ |
| You own the chat history? | ❌ | ✅ |

The only network calls are one-time HuggingFace downloads of the
embedding / ASR / TTS models. After that you can pull the ethernet.

---

## Features

**Documents** — PDF · DOCX · EPUB · images (OCR). Drag-and-drop,
multi-file, replace-or-append. Per-file `Search scope` toggle.

**Chat** — Streaming markdown answers with code, tables, and LaTeX.
Bilingual (中/EN) — answers in the language you asked in. Multi-thread
sidebar with auto-titled conversations.

**Inline `[N]` citations** — Every fact-bearing sentence ends in a
small numeric chip. Hover lifts a popover showing the exact paragraph
from the cited chunk; click *View source* to open the PDF page with
the paragraph marked. The sources panel is exactly what the answer
referenced — no irrelevant chunks padding the list. Ungrounded
sentences (pure synthesis / low confidence) get a soft wavy
underline so you know what to double-check.

**Multi-variant regenerate** — Regenerate appends a new answer as a
variant on the same bubble; flip between versions with a
ChatGPT-style `< N/M >` pager. The active variant is what the next
follow-up sees as the prior reply.

**Voice (opt-in)** — Push-to-talk mic (~1.5 s silence auto-stop) +
streaming TTS that reads each sentence as it's generated. English only.

**Settings** — Live model swap by typing a new `.gguf` path; auto-clean
local data on browser close (opt-in checkbox). Persists to
`hushdoc_config.json`.

---

## Quick start

Requirements: **Windows 10/11, Linux, or macOS** · Python 3.12 ·
Node 20+ · ~10 GB free disk. NVIDIA GPU optional (auto-detected).

```powershell
# Windows -- double-click these in order
.\setup.bat        # one-time: venv, npm install, llama-server, default model
.\hushdoc.bat      # every time after
```

```bash
# macOS / Linux
chmod +x setup.sh dev.sh
./setup.sh         # one-time
./dev.sh           # every time after
```

`setup` is idempotent — re-run after `git pull` and only the dirty
steps re-execute. It auto-picks CUDA or CPU build of `llama-server`
based on `nvidia-smi`; override with `-Cpu` / `-GpuBuild` / `-Force`
(Windows) or `--cpu` / `--gpu-build` / `--force` (Unix). Default model
is Qwen3-1.7B Q4_K_M (~1.2 GB).

The app opens at <http://localhost:5173>. **First answer takes
~15 s** (model warmup); subsequent ones stream in within a second.

### Use a different model

Three equivalent paths:

1. Settings ⚙ → paste any `.gguf` path → *Save*. Hushdoc hot-swaps
   `llama-server` with no restart.
2. Drop a `.gguf` at `./models/model.gguf` and re-launch.
3. `LLAMA_MODEL_PATH=/path/to/your.gguf` before launching.

Hushdoc speaks the OpenAI-compatible llama.cpp API, so anything llama.cpp
loads works: Qwen3-4B, Mistral-7B, Llama-3.1-8B, DeepSeek-R1, etc.
Reasoning-model `<think>` blocks are stripped automatically.

---

## Under the hood

A few engineering choices that take Hushdoc past "embed-and-pray":

- **Hybrid retrieval.** BM25 + dense embedding fuse via Reciprocal Rank
  Fusion. Catches exact filenames / model versions / error codes the
  bi-encoder flattens. Mode via `HUSHDOC_RETRIEVAL_MODE=hybrid|dense|bm25`.
- **Cross-encoder reranker.** Wider bi-encoder recall, then cross-encoder
  rescore — latency where it matters.
- **Per-document summary cache.** Each file gets one LLM summary at
  ingest, fed into every prompt so "which of these is about X?" works.
- **Session chunk memory.** Chunks from earlier turns get mixed back into
  the candidate pool on follow-ups, persisted across backend restarts.
- **GPU auto-detect** for the embedding + reranker; override via
  `HUSHDOC_EMBED_DEVICE=cpu|cuda`.
- **Streaming `<think>` stripper** for reasoning models (state machine
  survives split tokens).
- **Heartbeat shutdown** — close the browser, the server self-exits and
  the launcher offers to wipe local data.

**Stack:** FastAPI + React 19 + Vite + Tailwind/shadcn ·
llama.cpp (`llama-server`) · ChromaDB + sentence-transformers
(`all-MiniLM-L6-v2`) · IBM Docling · Whisper-base.en + Kokoro-82M
for voice.

---

## Quality

Numbers, not vibes. Hushdoc ships an offline [Ragas](https://github.com/explodinggradients/ragas)
harness that scores the full RAG pipeline against a labelled question
set — using the same local llama.cpp as the judge LLM, so the whole
evaluation is air-gapped.

**Run setup** — v0.6.4 RAG pipeline · indexed corpus is the original
[*Attention Is All You Need*](https://arxiv.org/abs/1706.03762) paper
(42 chunks after Docling ingest) · bundled **Qwen3-1.7B-Q4_K_M**
serves as both the generator AND the Ragas judge LLM (no external API
in the loop). Three columns below — **CP** = Context Precision
(fraction of top-k chunks on-topic), **F** = Faithfulness (every
answer claim traces back to a retrieved chunk, i.e. no hallucination),
**AR** = Answer Relevancy (answer actually addresses the question):

| # | Question | Model answer (verbatim, v0.6.4) | CP | F | AR |
|---|---|---|---:|---:|---:|
| 1 | What dataset was used for the English-German translation experiments? | The dataset used for the English-German translation experiments was the standard WMT 2014 English-German dataset consisting of about 4.5 million sentence pairs. This dataset was used for training the model, with sentences encoded using byte-pair encoding and a shared source-target vocabulary of about 37000 tokens. The training was conducted on 8 P100 GPUs, and the results are summarized in Table 2. | 0.967 | — | 0.996 |
| 2 | What is the dimensionality of the model (`d_model`) in the base Transformer? | The dimensionality of the model (`d_model`) in the base Transformer is 512 [4]. | 0.750 | 1.000 | 1.000 |
| 3 | How many encoder and decoder layers does the base Transformer have? | The base Transformer has N = 6 encoder and decoder layers [3]. | 1.000 | — | 0.927 |
| | **Mean** | | **0.906** | **1.000** | **0.974** |

A `—` means Ragas's claim-extractor couldn't pull a checkable claim
out of that answer (common when the answer is a single short
factoid), so the question is skipped for that metric — it does NOT
mean "failed".

Reproduce against your own corpus + question set:

```bash
# 1. One-time: the eval-only extras (ragas, datasets, pyarrow).
#    Kept out of the main requirements so the chat path stays slim.
pip install -r requirements-eval.txt

# 2. Score against your own labelled test set. The file is a JSON
#    list of {question, ground_truth} objects; index the relevant
#    documents into Hushdoc first, then point evaluate.py at it.
python evaluate.py \
  --test-set my_questions.json \
  --include-context-precision \
  --include-faithfulness
```

Minimal `my_questions.json`:

```json
[
  {"question": "What is X?", "ground_truth": "X is ..."},
  {"question": "How does Y work?", "ground_truth": "Y works by ..."}
]
```

Results land under `eval_results/` as paired JSON + CSV (per-question
breakdown in the CSV).

---

## Notes

- **Air-gapped install:** copy `~/.cache/huggingface` from a connected
  machine, drop a `.gguf` at `./models/`, and you're set.
- **Auto-cleanup on exit** currently lives in `hushdoc.bat` / `.ps1`
  only; `dev.sh` users Ctrl+C and clean up by hand.
- **Voice is English-only** (Whisper-base.en + Kokoro-82M). Text chat
  is fully bilingual.
- Full release notes in [CHANGELOG.md](CHANGELOG.md).

---

## License

MIT — see [`LICENSE`](LICENSE).
