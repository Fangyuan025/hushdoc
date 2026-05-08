# 🤫 Hushdoc

<p align="center">
  <a href="https://github.com/Fangyuan025/hushdoc/releases"><img alt="最新版本" src="https://img.shields.io/github/v/release/Fangyuan025/hushdoc?style=for-the-badge&color=2ea44f&label=release"></a>
  <a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-yellow?style=for-the-badge"></a>
  <img alt="完全本地运行" src="https://img.shields.io/badge/🛡️_local-only-1f6feb?style=for-the-badge">
  <img alt="中英双语" src="https://img.shields.io/badge/🌍_bilingual-中%2FEN-7c3aed?style=for-the-badge">
</p>

<p align="center">
  <img alt="Python 3.12" src="https://img.shields.io/badge/python-3.12-3776AB?style=for-the-badge&logo=python&logoColor=white">
  <img alt="FastAPI" src="https://img.shields.io/badge/FastAPI-009688?style=for-the-badge&logo=fastapi&logoColor=white">
  <img alt="React 19" src="https://img.shields.io/badge/React-19-61DAFB?style=for-the-badge&logo=react&logoColor=000">
  <img alt="Vite" src="https://img.shields.io/badge/Vite-646CFF?style=for-the-badge&logo=vite&logoColor=white">
  <img alt="llama.cpp" src="https://img.shields.io/badge/llama.cpp-subprocess-FF6B6B?style=for-the-badge">
  <img alt="ChromaDB" src="https://img.shields.io/badge/ChromaDB-vectors-FFCD42?style=for-the-badge">
</p>

<p align="center">
  <a href="README.md">English</a> · <b>中文</b>
  &nbsp;|&nbsp;
  <a href="https://github.com/Fangyuan025/hushdoc/releases">Releases</a> ·
  <a href="CHANGELOG.md">Changelog</a>
</p>

> **和你的文档对话——本地、离线、不出你这台电脑。**

把 PDF、Word 文档、甚至一张书页的手机照片丢进来，用中文或英文随便问。
答案逐字流式返回，每条都带原文出处。**没有任何上传**——一切都在你自己的机器上完成。

```
🛡️ 本地优先    🚀 GPU 加速    🌍 中英双语    🎙️ 可选语音模式
```

---

## 你能用它做什么

- **30 秒读完一篇 200 页论文。** 把 PDF 拖进去，问"总结核心发现"——
  几秒后拿到一段带页码引用的回答。
- **左右对照两份合同 / 两篇论文 / 两份报告。** 在侧边栏勾选两份文件，
  问"它们在哪里观点不一致"——Hushdoc 会平衡两份文档的检索预算，
  不会让其中一份"压住"另一份。
- **找回一个你记得读过的细节。** "第四章里关于预算说了什么？"——
  回答带 `[文件 p.12]` 的引用 chip，原文在哪一目了然。
- **OCR 一张手机拍的书页。** 手写笔记、教材某一页——JPG / PNG /
  TIFF / BMP 都能自动 OCR 后入库。
- **语音问答。** 按住麦克风按钮说话，听到答案被读出来。
  （目前仅英文。）
- **对话历史归档。** 多对话存档 + 自动生成标题，跟 ChatGPT 一样，
  侧边栏直接切换。

---

## 为什么叫"Hushdoc"？

大多数 AI 文档工具会把你的文件传到别人的云上。这对一份公开 PDF
没什么——但合同、病历、未发表的手稿、任何 NDA 涵盖的内容，都不应该
那样处理。Hushdoc 是为了让你**永远不用做这个权衡**而做的。

| | 云端 RAG（ChatGPT / Claude / Gemini） | Hushdoc |
|---|---|---|
| 文档存在哪？ | 它们的服务器 | 你自己的硬盘 |
| 推理跑在哪？ | 它们的 GPU | 你自己的 GPU（或 CPU） |
| 飞机上 / 内网 / 断网时能用？ | ❌ | ✅ |
| 一次性下载模型后免费？ | ❌ | ✅ |
| 对话记录归你所有？ | ❌ | ✅ |

Hushdoc **唯一**的网络请求是首次启动时从 HuggingFace 下载 embedding、
ASR、TTS 三个模型。下载完成进缓存之后，可以**完全断网运行**——
拔网线、关 Wi-Fi 都不影响。

---

## 功能一览

#### 文档
- **PDF、DOCX、图片** 全支持——表格、代码块、数学公式、手写页都
  能正确保留结构。
- **拖拽** 上传，多文件，支持"替换/追加"切换。
- **检索范围（Search scope）**：一键将提问限定到某几个文件；不勾选
  默认对全库检索。

#### 对话
- **流式回答**，带 markdown、代码高亮、GFM 表格、LaTeX 数学公式渲染。
- **行内引用** `[文件 p.5]` 直接链回原文片段——既不编造，也不藏。
- **中英双语**——Hushdoc 检测你提问的语言，**用同一种语言回答**，
  哪怕你的文档是另一种语言。
- **多对话历史**，第一轮结束后自动生成标题。侧边栏列表点击切换、
  二次点击删除。

#### 语音（默认关闭）
- 聊天框旁的**按住说话**麦克风按钮，~1.5 秒静默自动停录，不用手动结束。
- **流式 TTS**——回答**还在生成时**就会按句子边读边播，没有"全部生成完
  再开始读"的尴尬停顿。
- 历史回答旁边的 🔊 图标可以**回放**之前合成过的音频。

#### 细节
- **深色模式**——是真正适合夜间看的炭灰，不是死黑。
- **键盘快捷键：** `Ctrl/Cmd + K` 聚焦输入框，`Ctrl/Cmd + L` 新建对话，
  `Esc` 取消。
- **一键启动**（`hushdoc.bat`）：自动启服务、开浏览器，关浏览器后
  会问你要不要清理本地数据——按类别 opt-in。

---

## 快速开始

环境要求：

- Windows 10 / 11（Linux / macOS 用 `dev.sh`，详见下方"备注"）
- [Python 3.12](https://www.python.org/downloads/release/python-3120/)
  （安装时勾上 *Add to PATH*）
- [Node.js 20+](https://nodejs.org/)（LTS 版即可）
- **~10 GB 空闲硬盘**（完整安装）。大致分布：
  - `.venv/` — Python 依赖（~6 GB；其中 ~4.4 GB 是 PyTorch CUDA 版）
  - `~/.cache/huggingface/` — embedding + Whisper + Kokoro + Docling
    版式 / 表格模型（~1.3 GB，首次使用时按需下载）
  - `models/model.gguf` — 默认 LLM（~1.2 GB）
  - `runtime/` — `llama-server.exe` + DLLs（GPU 版 ~750 MB，CPU 版 ~50 MB）
  - `web/node_modules/` — 前端依赖（~200 MB）
- NVIDIA 显卡**可选**——能加速大模型，但默认的 Qwen3-1.7B 在 CPU 上
  也跑得很顺。

### 拿稳定版

需要经过测试的快照版本，去
[Releases 页](https://github.com/Fangyuan025/hushdoc/releases) 下载最新
release 的 source archive（zip 或 tar.gz），解压后按下面的 `setup.bat`
/ `setup.sh` 继续即可。想要最新功能就直接 clone `master`。

### 简单做法：双击两次（Windows）

```powershell
.\setup.bat        # 一次性：装依赖、下载 runtime 和模型
.\hushdoc.bat      # 之后每次启动用这个
```

`setup.bat` 全自动可重跑。它会：

1. 创建 Python venv（`.venv\`）并 `pip install` 所有依赖
2. 跑 `npm install` 装前端依赖
3. 检测你是否有 NVIDIA 显卡，自动选对应版本的 `llama-server.exe`
   下载到 `.\runtime\`：
   - **检测到 GPU：** CUDA 12.4 build（~205 MB）+ cudart 运行时 DLL
     （~373 MB）——大模型也能飞快推理
   - **没 GPU：** CPU build（~15 MB）——任何机器都能跑，对默认 1.7B
     模型来说足够快
4. 从 [HuggingFace](https://huggingface.co/MaziyarPanahi/Qwen3-1.7B-GGUF)
   下载默认模型 **Qwen3-1.7B Q4_K_M**（~1.2 GB）到 `.\models\model.gguf`

每一步都会先检查"是否已经做过"，做过就跳过，所以 `git pull` 之后
重跑 setup 是安全的。

> **覆盖 GPU/CPU 自动判断：**
> - `.\setup.bat -Cpu` 强制 CPU build（下载小，CUDA 装坏时也能跑）。
> - `.\setup.bat -GpuBuild` 强制 CUDA build（即使 `nvidia-smi` 不在 PATH 上）。
> - `.\setup.bat -Force` 重新下载 runtime 和 model（比如 llama.cpp
>   升级后）。**不会**重建 venv。

### macOS / Linux

```bash
chmod +x setup.sh dev.sh
./setup.sh         # 一次性
./dev.sh           # 之后每次启动
```

`setup.sh` 跟 `setup.bat` 步骤等价：venv、npm install、Linux 上
auto-detect NVIDIA GPU（→ CUDA 版 `llama-server`），macOS 或
没 GPU 的机器自动 fallback CPU 版，下同样的 Qwen3-1.7B 模型。
`--cpu` / `--gpu-build` / `--force` 三个 flag 跟 Windows 版一一对应。

> 退出时的自动清理询问目前**只**在 Windows `hushdoc.bat` 里实现。
> `dev.sh` 能起服务但 Ctrl+C 后不会弹清理 prompt，要手动收拾。

### 想换更大的模型？

把任意 `.gguf` 文件命名为 `.\models\model.gguf` 替换默认那个。
按你的内存挑：

- **Qwen3-4B Q4_K_M**（~2.5 GB）——推理更强，8 GB 内存就能跑
- **Mistral-7B-Instruct Q4_K_M**（~4.5 GB）——英文场景的强基线
- **Llama-3.1-8B Q4_K_M**（~4.7 GB）——综合能力很好

也可以通过环境变量 `LLAMA_MODEL_PATH` 指向任意路径。

> **关于 prompt：** system prompt 里嵌了 Qwen3 的 `/no_think` 软开关
> （用来禁用 Qwen3 的 `<think>...</think>` 推理块）。非 Qwen3 的 chat
> template 不识别这个 token，所以换 Llama / Mistral / Phi / Qwen2.5 等
> **直接就能用**——开头那 9 个字符会被它们的 tokenizer 静默丢掉。
> Streaming 阶段还有一个独立的 `<think>` 过滤器兜底（对 DeepSeek-R1 /
> QwQ 这类 reasoning 模型有意义），所以无论换什么模型，**用户看到的
> 回答都是干净的**。

### 手动安装（不想用脚本）

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
cd web && npm install && cd ..
# 然后自己下载 llama-server.exe 和 .gguf，分别放到
# .\runtime\llama-server.exe 和 .\models\model.gguf 后启动：
.\hushdoc.bat
```

---

启动完成后 Hushdoc 会在默认浏览器打开 <http://localhost:5173>。
往侧边栏拖一个 PDF 然后开问。

> **第一条回答会等 10–20 秒**（模型 warm-up），之后每条都几乎
> 立刻开始流式输出。

---

## 使用建议

- **大文档要问得具体。** "第三章的结论是什么？"比"把这篇论文都讲一遍"
  好得多。
- **文件多的时候用 scope。** 在 *Search scope* 面板勾选 1–3 个具体文件，
  回答会更准更快。
- **追问可以自然问。** "能展开讲讲吗？"或"为什么？"会被自动结合上下文，
  Hushdoc 内部把它改写成独立完整的问题再去检索。
- **Sources 面板只显示真正被引用的片段。** 没用到的 chunk 不会被凑数。
- **关浏览器自动停服务**，并询问要不要清理本地数据（对话 / 上传文件 /
  向量索引）——按类别 *n* 保留、*y* 清掉。

---

## 实现细节

给好奇的人——Hushdoc 不只是 "embed and pray"。让它真正可用的几个
工程决定：

- **每文档级摘要缓存。** 单纯 top-k 检索回答不了 "哪一份是讲机器
  学习的？" 因为 chunk 本身不带文档级主题。Hushdoc 在入库时给每个
  文件做一次 LLM 摘要，回答时把"当前 scope 内的文档摘要"一并放进
  prompt。
- **多文档平衡检索。** 2 个以上文件在 scope 时，检索预算按文件名
  均分，话多的那份不会盖过另一份。
- **Cross-encoder 重排。** Bi-encoder 先粗召回更宽的候选，强一点的
  cross-encoder 再精排——把延迟花在真正影响最终结果的环节上。
- **逐轮语言指令。** 小模型容易往文档语言漂。Hushdoc 检测你提问的
  语言，把指令塞在 prompt 的最末位置——这是小模型最听话的 token slot。
- **行内 `<think>` 剥离。** 推理模型的 `<think>` 块在 streaming 阶段
  就被状态机吃掉，即使开/闭标签被切到两个 token chunk 里也能正确处理。
- **心跳驱动关停。** 浏览器每 10 秒 ping 一次后端；关 tab 之后心跳
  停下，后端自动退出，启动器接着进入清理询问流程。

技术栈：**FastAPI**（Python 3.12）+ **React 19** + **Vite** +
**Tailwind / shadcn**，通过 OpenAI 兼容的 HTTP API 调 **llama.cpp**
独立的 `llama-server`。向量库是 **ChromaDB**，文档解析用
**IBM Docling**。语音模式用 **Whisper-base.en**（ASR）+
**Kokoro-82M**（TTS）。

---

## 项目结构

```
hushdoc/
├── server/          FastAPI 后端（routes、对话存储、SSE 适配器）
├── web/             React + Vite 前端（components、hooks、lib）
├── ingest.py        Docling 解析 + HybridChunker
├── vector_store.py  ChromaDB + 平衡检索
├── reranker.py      cross-encoder 重排
├── llm_chain.py     RAGChain——streaming、scope、follow-up、voice
├── llama_server.py  llama-server.exe 生命周期管理
├── doc_summaries.py 每文件摘要缓存
├── voice.py         Whisper ASR + Kokoro TTS
├── setup.bat / .sh  一次性安装器（依赖 + runtime + 模型）
├── hushdoc.bat      Windows 一键启动器（带退出清理询问）
├── dev.sh / dev.ps1 普通开发启动器（不带自动清理）
├── VERSION          /api/health 读它，UI 角落显示版本号
└── CHANGELOG.md
                     （gitignored：runtime/  models/*.gguf）
```

---

## 备注

- **Linux / macOS 用户：** 见上方 *macOS / Linux* 一节——
  `setup.sh` 然后 `dev.sh`。退出清理目前只在 Windows 的 `.bat` / `.ps1`
  流程里。
- **纯 CPU** 也能跑——把 `LLMConfig` 里的 `n_gpu_layers` 设成 `0`。
  首 token 慢一点，但回答质量一致。
- **完全离线安装：** 在能联网的机器上预先下好 embedding
  （`all-MiniLM-L6-v2`）、Whisper-base.en、Kokoro-82M 三个模型，
  把整个 HuggingFace 缓存（`~/.cache/huggingface`）拷过去就行。
- **语音模式仅支持英文。** Whisper-base.en 和 Kokoro-82M 都是英文
  模型。聊天本身是完全双语的——任何时候都可以打中文。

---

## License

MIT——见 [`LICENSE`](LICENSE)。
