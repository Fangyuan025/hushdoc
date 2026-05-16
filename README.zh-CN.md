# 🤫 Hushdoc

<p align="center">
  <a href="https://github.com/Fangyuan025/hushdoc/releases"><img alt="Release" src="https://img.shields.io/github/v/release/Fangyuan025/hushdoc?style=for-the-badge&color=2ea44f"></a>
  <a href="LICENSE"><img alt="MIT" src="https://img.shields.io/badge/license-MIT-yellow.svg?style=for-the-badge"></a>
  <a href="#why"><img alt="Local-only" src="https://img.shields.io/badge/本地--only-1f6feb.svg?style=for-the-badge&logo=ghostery&logoColor=white"></a>
  <a href="README.md"><img alt="English" src="https://img.shields.io/badge/English-7c3aed.svg?style=for-the-badge&logo=googletranslate&logoColor=white"></a>
</p>

<p align="center">
  <a href="https://www.python.org/"><img alt="Python 3.12" src="https://img.shields.io/badge/python-3.12-3776AB.svg?style=for-the-badge&logo=python&logoColor=white"></a>
  <a href="https://fastapi.tiangolo.com/"><img alt="FastAPI" src="https://img.shields.io/badge/FastAPI-009688.svg?style=for-the-badge&logo=fastapi&logoColor=white"></a>
  <a href="https://react.dev/"><img alt="React 19" src="https://img.shields.io/badge/React-19-61DAFB.svg?style=for-the-badge&logo=react&logoColor=000"></a>
  <a href="https://github.com/ggml-org/llama.cpp"><img alt="llama.cpp" src="https://img.shields.io/badge/llama-cpp-FF6B6B.svg?style=for-the-badge"></a>
  <a href="https://www.trychroma.com/"><img alt="ChromaDB" src="https://img.shields.io/badge/ChromaDB-FFCD42.svg?style=for-the-badge"></a>
</p>

<p align="center">
  <a href="README.md">English</a> · <b>中文</b>
  &nbsp;|&nbsp;
  <a href="https://github.com/Fangyuan025/hushdoc/releases">Releases</a> ·
  <a href="CHANGELOG.md">Changelog</a>
</p>

> **和你的文档对话——本地、离线、不出你这台电脑。**

把 PDF、DOCX、EPUB、甚至一张书页的照片丢进来。用中文或英文随便问。
答案带行内引用流式出来，**PDF 引文查看器**会用黄色高亮直接画出原文。
**没有任何东西离开你的机器。**

`🛡️ 本地优先` · `🚀 GPU 加速` · `🌍 中 / EN` · `🎙️ 语音（英文）`

---

## 为什么 <a id="why"></a>

大多数 AI 文档工具会把你的文件传到别人的云上。对公开 PDF 无所谓，
但对合同、未发表手稿、NDA 范围内的内容就不行了。

| | 云端 RAG | Hushdoc |
|---|---|---|
| 文档存哪 | 它们的服务器 | 你自己的硬盘 |
| 推理跑在哪 | 它们的 GPU | 你自己的 GPU / CPU |
| 能离线用吗 | ❌ | ✅ |
| 对话历史属于谁 | ❌ | ✅ |

唯一的联网请求是 HuggingFace 上嵌入 / ASR / TTS 模型的**一次性**下载。
之后拔网线也能用。

---

## 功能

**文档** — PDF · DOCX · EPUB · 图片（OCR）。拖拽上传、多文件、
替换/追加切换。每文件 `Search scope` 勾选。

**对话** — 流式 markdown 答案，带代码高亮、表格、LaTeX 公式。中英
双语——用什么语言问就用什么语言答。多对话侧边栏 + 第一轮自动起标题。

**行内 `[N]` 引用** — 每个事实型句子末尾挂一个小数字 chip。鼠标移
上去弹出 popover 显示该段引用对应的原文段落；点 *View source* 打开
PDF 跳到该页、用细长条标注那一段。Sources 列表严格等于答案真正
引用的 chunks，不再有无关 chunk 塞列表。"未对齐"的句子（纯综合或
低置信度）会有柔和的波浪下划线，提示你 double-check。

**多版本 regenerate** — 重生成把新答案以 variant 形式挂到同一气泡上，
用 ChatGPT 风格 `< N/M >` 翻页器切换。当前选中的版本会作为下一轮
follow-up 看到的"上一条助手回复"。

**语音（默认关）** — 按住说话麦克风（~1.5 秒静默自动停录）+ 流式
TTS 边生成边读。仅英文。

**设置** — 在线粘贴 `.gguf` 路径即可换模型；关浏览器自动清本地数据
（可选）。配置存到 `hushdoc_config.json`。

---

## 快速开始

需要：**Windows 10/11、Linux、或 macOS** · Python 3.12 · Node 20+
· ~10 GB 空闲磁盘。NVIDIA GPU 可选（自动检测）。

```powershell
# Windows -- 双击这两个就够了
.\setup.bat        # 一次性：venv、npm install、llama-server、默认模型
.\hushdoc.bat      # 之后每次启动
```

```bash
# macOS / Linux
chmod +x setup.sh dev.sh
./setup.sh         # 一次性
./dev.sh           # 之后每次启动
```

`setup` 是幂等的——`git pull` 后再跑一次只会处理变动的部分。会根据
`nvidia-smi` 自动选 CUDA 或 CPU 版的 `llama-server`，可用
`-Cpu` / `-GpuBuild` / `-Force`（Windows）或 `--cpu` / `--gpu-build`
/ `--force`（Unix）强制。默认模型是 Qwen3-1.7B Q4_K_M（~1.2 GB）。

启动后浏览器自动开 <http://localhost:5173>。**首条回答约 15 秒**
（模型预热），之后秒级流式。

### 换其他模型

三种等价方法：

1. 设置 ⚙ → 粘贴任意 `.gguf` 路径 → *Save*。Hushdoc 热切换
   `llama-server`，不用重启。
2. 放一个 `.gguf` 到 `./models/model.gguf` 然后重启。
3. 启动前 `LLAMA_MODEL_PATH=/path/to/your.gguf`。

Hushdoc 走的是 OpenAI 兼容的 llama.cpp API，任何 llama.cpp 能加载的
模型都行：Qwen3-4B、Mistral-7B、Llama-3.1-8B、DeepSeek-R1 等。推理
模型的 `<think>` 块会被自动剥掉。

---

## 实现细节

让 Hushdoc 不止"embed-and-pray"的几个工程决定：

- **混合检索。** BM25 + dense 向量并行召回，用 Reciprocal Rank Fusion
  融合。能抓住 bi-encoder 投影后丢失的精确字符串（文件名、版本号、
  错误码等）。`HUSHDOC_RETRIEVAL_MODE=hybrid|dense|bm25` 可切换。
- **Cross-encoder 重排。** Bi-encoder 粗召回更宽，cross-encoder
  精排——延迟花在最影响最终结果的环节。
- **每文档级摘要缓存。** 每个文件入库时做一次 LLM 摘要，每次回答都
  注入 prompt，让"哪一份是讲 X 的？"这类问题能答。
- **会话级 chunk 记忆。** 前几轮选中的 chunk 会被混回 follow-up 的
  候选池，跨后端重启持久化。
- **GPU 嵌入自动检测**（embedding + reranker），`HUSHDOC_EMBED_DEVICE=cpu|cuda`
  可强制。
- **流式 `<think>` 剥离**，推理模型的思考块在生成过程中被状态机
  实时去掉，标签被切到两个 token chunk 也能正确处理。
- **心跳驱动关停**——关浏览器，后端自动退出，启动器进入清理询问。

**技术栈：** FastAPI + React 19 + Vite + Tailwind/shadcn ·
llama.cpp（`llama-server`）· ChromaDB · IBM Docling · 语音是
Whisper-base.en + Kokoro-82M。

---

## 说明

- **离线部署：** 把另一台联网机器的 `~/.cache/huggingface` 拷过来，
  在 `./models/` 放个 `.gguf` 就齐了。
- **退出自动清理**目前只在 `hushdoc.bat` / `.ps1` 里；`dev.sh` 用户
  Ctrl+C 后手动清。
- **语音仅英文**（Whisper-base.en + Kokoro-82M）。聊天本身完全双语。
- 完整 release notes 看 [CHANGELOG.md](CHANGELOG.md)。

---

## License

MIT —— 见 [`LICENSE`](LICENSE)。
