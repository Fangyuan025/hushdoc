"""
Step 3: Local LLM Engine, Conversational Memory, and RAG Chain.

- Loads a local GGUF model via llama-cpp-python (no network calls).
- Maintains chat history with LangChain's RunnableWithMessageHistory.
- Implements a "Standalone Query Generator" that rewrites the latest user
  question into a context-independent search query using the chat history.
- Retrieves relevant chunks from the local Chroma store and grounds the
  final answer on them.
"""
from __future__ import annotations

import os
# llama-cpp-python and PyTorch (via sentence-transformers) both ship their own
# OpenMP runtimes on Windows. Loading both into one process triggers a duplicate
# OpenMP runtime check that segfaults. This flag tells Intel OpenMP to tolerate
# it. MUST be set BEFORE numpy/torch/llama_cpp are imported.
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from langchain_openai import ChatOpenAI
from langchain_core.chat_history import BaseChatMessageHistory, InMemoryChatMessageHistory
from langchain_core.documents import Document
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables import (
    Runnable,
    RunnableLambda,
    RunnablePassthrough,
)
from langchain_core.runnables.history import RunnableWithMessageHistory

from vector_store import LocalVectorStore, build_default_store
from llama_server import (
    LlamaServer,
    ServerConfig,
    get_shared_server,
    DEFAULT_MODEL_PATH as SERVER_DEFAULT_MODEL_PATH,
)
import doc_summaries
from chain_grounding import bind_answer_to_sources, bindings_to_payload

logger = logging.getLogger("llm_chain")
if not logger.handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    )


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DEFAULT_MODEL_PATH = SERVER_DEFAULT_MODEL_PATH


@dataclass
class LLMConfig:
    model_path: Path = DEFAULT_MODEL_PATH
    # Qwen3 1.7B supports up to 40K. n_ctx is TOTAL across llama-server
    # slots; per-slot ctx = n_ctx / parallel. With server parallel=4 the
    # 32768 here gives 8192 per slot, plenty for ragas judge prompts.
    # KV cache lives on the GPU; on a 4GB card the model + 32K KV ≈ 2-3GB.
    n_ctx: int = 32768
    # -1 = offload ALL layers to GPU (requires CUDA-enabled llama-server).
    # Set to 0 for CPU-only.
    n_gpu_layers: int = -1
    temperature: float = 0.2
    # Generous so answers + ragas judge JSON don't get truncated.
    max_tokens: int = 2048
    top_p: float = 0.95
    repeat_penalty: float = 1.1
    # llama-server HTTP endpoint config (port etc.) is in ServerConfig.
    server_config: Optional[ServerConfig] = None
    extra_kwargs: dict = field(default_factory=dict)


# Qwen3 chat-template directive that disables the <think> reasoning block.
# Append this to a user message and the model will answer directly.
# Reference: Qwen3 uses /think and /no_think soft switches in the prompt.
QWEN3_NO_THINK = "/no_think"


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------
# Putting /no_think at the very START of the system message is the most
# reliable way to disable Qwen3's <think> block - putting it inside or at
# the end of the user message can cause the model to echo it back as content.
CONDENSE_QUESTION_SYSTEM = (
    QWEN3_NO_THINK + "\n"
    "You rewrite the user's latest message into a single self-contained "
    "search query that captures what they actually want to look up.\n\n"
    "Rules:\n"
    "- Resolve every pronoun, ellipsis, and implicit reference using the "
    "  chat history. Short follow-ups like 'why?', 'and?', 'tell me more', "
    "  '为什么', '继续', '再说说' MUST be expanded using what was just "
    "  discussed.\n"
    "- Output ONLY the rewritten query in plain prose (no quotes, no "
    "  slashes, no special tokens, no preamble like 'Standalone query:').\n"
    "- If the latest message is already self-contained, return it UNCHANGED.\n"
    "- Keep the rewrite under 25 words.\n\n"
    "Examples:\n"
    "  History: 'Q: What is the Transformer architecture? A: It is a model "
    "  that uses self-attention...'\n"
    "  Latest: '为什么？'\n"
    "  Output: Why does the Transformer architecture use self-attention "
    "  instead of recurrence?\n\n"
    "  History: (empty)\n"
    "  Latest: 'How does multi-head attention work?'\n"
    "  Output: How does multi-head attention work?"
)

CONDENSE_PROMPT = ChatPromptTemplate.from_messages(
    [
        ("system", CONDENSE_QUESTION_SYSTEM),
        MessagesPlaceholder("chat_history"),
        ("human", "Latest message: {question}\n\nRewritten query:"),
    ]
)

ANSWER_SYSTEM = (
    QWEN3_NO_THINK + "\n"
    "You are Hushdoc, a precise assistant answering questions about user-"
    "uploaded documents (PDFs, Word .docx, or scanned image documents).\n\n"
    "LANGUAGE: Reply in the SAME natural language as the user's latest "
    "question. If the user writes in Chinese, answer in Chinese; if in "
    "English, answer in English. Do not switch languages mid-answer.\n\n"
    "Follow these rules:\n"
    "1. SOURCES OF TRUTH, in priority order:\n"
    "   (a) the provided context (document summaries + retrieved excerpts)\n"
    "   (b) prior assistant turns in the chat history -- if you already\n"
    "       established a fact in this conversation, you may build on it\n"
    "       in a follow-up answer without re-retrieving.\n"
    "   Only reply with 'I don't know based on the provided documents.'\n"
    "   (or '根据提供的文档我无法回答。') when BOTH of those are silent on\n"
    "   the user's question -- not when the current retrieval happens to\n"
    "   miss a chunk you previously discussed. Do NOT pull in outside\n"
    "   world knowledge. Do NOT guess author names, dates, or numbers\n"
    "   that aren't literally present in (a) or (b). If you mention a\n"
    "   dataset / model / number / year, it MUST appear verbatim in (a)\n"
    "   or have been stated in (b) -- otherwise omit it.\n"
    "2. The context block starts with a 'Documents in scope' summary list "
    "   (one line per file describing what that document is about), "
    "   followed by retrieved excerpts each prefixed with "
    "   '--- From <filename> (page <n>) ---'. Use the summaries to answer "
    "   high-level questions ('which one is about X', 'what is the topic', "
    "   'summarize this paper'); use the excerpts for specific details.\n"
    "3. For comparative or cross-document questions, attribute every claim "
    "   to the file it actually came from. Do NOT invent commonalities. "
    "   Do NOT mix facts across files. If the documents have nothing "
    "   meaningful in common, say so plainly.\n"
    "4. Synthesize ONE coherent prose answer. Do NOT enumerate context "
    "   excerpts one-by-one - the user wants the conclusion, not a "
    "   chunk-by-chunk commentary.\n"
    "5. When the context contains tables, code, or formulas, preserve their "
    "   structure verbatim. For LaTeX formulas in the context, keep them in "
    "   LaTeX form in your answer.\n"
    "6. CITATIONS — MANDATORY for every fact:\n"
    "   Each excerpt above is tagged with a number — [1], [2], [3], ... "
    "   — in its header line. Whenever your answer states ANY specific "
    "   fact, finding, number, name, definition, or claim from those "
    "   excerpts, you MUST append the source tag(s) at the end of that "
    "   sentence, immediately before the period.\n"
    "   Examples (the [N] is required on the ✓ rows):\n"
    "     ✓ \"The study sampled 47 participants [1].\"\n"
    "     ✓ \"P3 reported emotional disclosure to the chatbot [2].\"\n"
    "     ✓ \"The privacy paradox extends to AI chatbots [1][3].\"\n"
    "     ✓ \"该研究采用混合方法 [2]。\"  (Chinese works the same way)\n"
    "     ✗ \"In summary, ...\"   (discourse connector — no [N])\n"
    "     ✗ \"It is important to ...\"  (no specific claim)\n"
    "   Hard rules:\n"
    "   - Cite ONLY the numeric tags shown above. Do NOT invent new "
    "     numbers. Do NOT use legacy formats like [paper.pdf p.5].\n"
    "   - Multiple tags on one sentence is fine: [1][3].\n"
    "   - If you mention a number, year, author name, or quoted phrase "
    "     and can't trace it to a specific [N], OMIT it entirely.\n"
    "   - Do NOT add a 'References:' / 'Sources:' section. Inline [N] "
    "     tags are the only citation surface.\n"
    "7. Be concise. Do not repeat yourself. Do not fabricate quotes."
)

ANSWER_PROMPT = ChatPromptTemplate.from_messages(
    [
        ("system", ANSWER_SYSTEM),
        MessagesPlaceholder("chat_history"),
        (
            "human",
            # v0.4.0: when the user's message was a follow-up that
            # needed pronoun resolution / context expansion, the
            # rewriter produced a clarified standalone form. Showing
            # it to the answer model alongside the user's raw text
            # gives Qwen3-1.7B (and friends) a fighting chance at
            # the resolution without losing the user's actual wording.
            # Rendered as "" when the rewrite matches the raw question.
            "{expanded_query_hint}"
            "Context:\n----------\n{context}\n----------\n\n"
            "Question: {question}\n\n"
            # Last-line language directive. Putting it immediately before
            # 'Answer:' makes it the most recent instruction the model sees
            # and reliably overrides drift toward the context's language
            # (the source PDFs are usually English even when the question
            # is Chinese).
            "{language_directive}\n"
            "Answer:",
        ),
    ]
)


# Conversational fallback prompt for greetings / meta-questions / chitchat
# where doing a vector search would be silly. Reply in the user's language.
CHITCHAT_SYSTEM = (
    QWEN3_NO_THINK + "\n"
    "Reply in the SAME language as the user's message (Chinese for Chinese, "
    "English for English).\n"
    "You are Hushdoc, a friendly LOCAL-ONLY document assistant. The user "
    "can upload PDFs, Word documents (.docx), or photos of documents "
    "(JPG/PNG/TIFF) and ask questions about them - everything runs on "
    "their own machine, nothing leaves it. Voice input and read-aloud "
    "are also available (English only for now).\n"
    "The user has just sent a greeting, an introduction, a thank-you, or "
    "a meta-question about your capabilities - NOT a question about a "
    "specific document. Reply briefly and naturally. Do NOT call yourself "
    "a 'PDF assistant'; PDFs are just one of the supported formats. "
    "Do NOT refuse, do NOT say 'I don't know based on the provided "
    "documents'. Keep it under 3 sentences."
)

CHITCHAT_PROMPT = ChatPromptTemplate.from_messages(
    [
        ("system", CHITCHAT_SYSTEM),
        MessagesPlaceholder("chat_history"),
        ("human", "{question}\n\n{language_directive}"),
    ]
)


# ---------------------------------------------------------------------------
# Chitchat detector — short messages that match a greeting / thanks / meta
# pattern in CN or EN, and shouldn't trigger document retrieval.
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Language detection — decides which language directive to inject per turn.
# ---------------------------------------------------------------------------
# CJK ranges: CJK Unified Ideographs (most Chinese), plus Extension A.
# Hiragana/Katakana intentionally NOT included - we treat Japanese as 'other'.
_CJK_RE = re.compile(r"[一-鿿㐀-䶿]")


def detect_language(text: str) -> str:
    """Return 'zh' if the text contains a meaningful proportion of CJK
    characters, else 'en'. Used to pick the per-turn language directive."""
    if not text:
        return "en"
    cjk = sum(1 for ch in text if _CJK_RE.match(ch))
    # Even a few CJK chars in a short message means the user is writing
    # Chinese (e.g. '为什么？' is 3 chars). Threshold: >= 2 CJK chars OR
    # >= 30% of non-whitespace chars are CJK.
    nonspace = sum(1 for ch in text if not ch.isspace())
    if cjk >= 2 or (nonspace > 0 and cjk / nonspace >= 0.3):
        return "zh"
    return "en"


def language_directive(lang: str) -> str:
    """The last-line instruction we splice into prompts. Designed to be the
    most recent token the model reads, since trailing instructions tend to
    win over earlier system-prompt-level ones in small models.

    v0.6.0: now ALSO carries the citation reminder, because small models
    (Qwen3-1.7B in particular) routinely ignored the system-prompt-level
    [N] rule. Putting "cite as [N]" in the same trailing slot as the
    language directive consistently triggers the format."""
    if lang == "zh":
        return (
            "IMPORTANT: 必须用中文回答。即使上下文是英文，回答也必须是中文。"
            "Reply in Chinese only. 在陈述具体事实的每个句子末尾、句号"
            "之前，必须加上对应摘录的 [N] 标签（N 是上文摘录开头方括号"
            "里的数字）。例：「该研究采用混合方法 [2]。」"
        )
    return (
        "Reply in English. For EVERY sentence that states a specific "
        "fact, finding, number, or claim from the excerpts, append the "
        "matching [N] tag(s) right before the period — e.g. "
        "\"The study sampled 47 participants [1].\"."
    )


# Two pattern groups:
#   STRICT  — unambiguously chitchat (greetings, thanks, farewells, identity
#             questions). These short-circuit retrieval regardless of history.
#   META    — ambiguous on their own ('why?', 'really?', 'how?'). These are
#             chitchat ONLY when there's no chat history; with history they
#             are follow-ups that need retrieval-with-rewrite.
# Trailing punctuation a real user would type after a greeting / meta-q.
# Used as the end-of-message anchor for "must be the whole message" patterns.
_END = r"[\s!?。.！？～~]*$"

_CHITCHAT_STRICT_PATTERNS = [
    # ---- Greetings (prefix-match OK; a long real question rarely starts
    #      with these and the >40-char guard below catches "Hi there, can
    #      you summarize this paper?"-shaped messages anyway) ----
    r"^\s*(hi|hello|hey|yo|sup|hiya|howdy)\b",
    r"^\s*(good\s+)?(morning|afternoon|evening|night|day)\b",
    r"^\s*mornin'?\b",
    r"^\s*(thanks|thank\s+you|thx|ty|cheers|bye|goodbye|see\s+you|nice\s+to\s+meet)\b",
    r"^\s*(how\s+are\s+you|how's\s+it\s+going|what'?s?\s+up|how\s+do\s+you\s+do)\b",

    # ---- English meta-questions about Hushdoc itself.
    #      MUST be (essentially) the whole message — without the end anchor,
    #      "what is this" would swallow "What is this paper about?" and
    #      "help" would swallow "Help me find the budget number." ----
    r"^\s*who\s+are\s+you" + _END,
    r"^\s*what\s+(are\s+you|can\s+you\s+do|do\s+you\s+do)" + _END,
    r"^\s*what\s+is\s+this(\s+(app|tool|thing|hushdoc))?" + _END,
    r"^\s*(introduce\s+yourself|tell\s+me\s+about\s+yourself|please\s+introduce(\s+yourself)?)" + _END,
    r"^\s*help" + _END,
    r"^\s*what\s+(should|can)\s+i\s+(do|ask)" + _END,
    r"^\s*how\s+do\s+i\s+use\s+(this|you)" + _END,

    # ---- Chinese greetings — 你好 / 您好 / 嗨 / 哈喽 / 哈罗 ----
    r"^\s*(你好|您好|嗨+|哈[喽啰罗囉])" + _END,
    # 早 / 早安 / 早上好 / 早晨好 / 早呀 / 早啊
    r"^\s*早(上|晨)?(好|安)?[\s!?。.！？呀啊嘛哦呢～~]*$",
    r"^\s*(午安|中午好|下午好)[\s!?。.！？呀啊～~]*$",
    r"^\s*晚(上)?(好|安)?[\s!?。.！？呀啊～~]*$",
    # Chinese thanks / farewells / casual — short, end-anchored
    r"^\s*(谢谢|多谢|感谢|拜拜|再见|回头见|辛苦了|加油)" + _END,
    r"^\s*(在吗|你在吗|忙吗|最近怎么样|怎么样啊?)" + _END,

    # ---- Chinese meta-questions about Hushdoc — also end-anchored so
    #      "你是谁写的?" / "介绍一下这篇文章" don't get classified as chitchat.
    r"^\s*(你是谁|你叫什么|你能(做|干)什么|你是什么)" + _END,
    r"^\s*介绍(一?下)?(你?自己)" + _END,
    r"^\s*(你会(做)?什么|你能帮(我|忙)什么)" + _END,
    r"^\s*(帮助|怎么(用|玩))" + _END,
    # "请介绍一下你自己" / "麻烦介绍下自己"
    r"^\s*(请|麻烦)介绍(一?下)?(你?自己)" + _END,
]
_CHITCHAT_STRICT_RE = re.compile("|".join(_CHITCHAT_STRICT_PATTERNS), re.IGNORECASE)

# v0.4.0: pronoun heuristic for follow-up detection. Bare 'why?' is an
# obvious follow-up via the length gate, but 'Why did they choose
# mixed methods?' (35 chars) is just as anchored to prior turn — the
# 'they' is meaningless without it. We trip the boost when EITHER the
# message is short OR it's medium-length and pronoun-heavy.
_FOLLOWUP_PRONOUN_RE = re.compile(
    r"\b(they|them|their|theirs|it|its|this|that|those|these|"
    r"he|she|him|her|his|hers|"
    r"why|how|"
    r"the\s+(authors?|paper|study|document|finding|result))\b",
    re.IGNORECASE,
)
_FOLLOWUP_PRONOUN_CN_RE = re.compile(
    r"(他们|她们|它们|他|她|它|这|那|为什么|怎么|为何|"
    r"这篇|那篇|这项|这个|作者|论文|研究)"
)


def is_likely_followup(text: str, has_history: bool) -> bool:
    """True when this message likely needs context from a previous turn
    to retrieve well. Three signals, any of which counts:

      1. Very short (< 15 chars) -- bare 'why?' / '继续' / '再说说'.
      2. Pronoun present (en or zh) AND not too long (< 120 chars).
         'Why did they choose mixed methods?' qualifies.
      3. Starts with 'and ' / 'so ' / 'but ' -- discourse markers that
         continue the previous topic.

    Only meaningful when there IS prior history; otherwise an
    isolated 'they are weird' isn't a follow-up at all."""
    if not has_history or not text:
        return False
    t = text.strip()
    if len(t) < 15:
        return True
    if len(t) < 120 and (
        _FOLLOWUP_PRONOUN_RE.search(t)
        or _FOLLOWUP_PRONOUN_CN_RE.search(t)
    ):
        return True
    if re.match(r"^\s*(and|so|but|also|then|继续|然后|那么|另外)\b", t, re.IGNORECASE):
        return True
    return False


def is_chitchat(text: str, has_history: bool = False) -> bool:
    """Return True if the message looks like a greeting / chitchat / meta-q
    that should bypass document retrieval.

    Parameters
    ----------
    text : str
        The user's message.
    has_history : bool
        Whether there's any prior chat history. Affects ambiguous short
        utterances: a bare 'why?' / '为什么' counts as chitchat only when
        there is no history; with history it's a follow-up that needs
        retrieval-with-rewrite, NOT a fresh greeting.
    """
    if not text:
        return False
    t = text.strip()
    # Long messages are very unlikely to be pure chitchat - assume they're
    # real questions even if they happen to start with a greeting word.
    if len(t) > 40:
        return False
    return bool(_CHITCHAT_STRICT_RE.search(t))


# ---------------------------------------------------------------------------
# Streaming <think>...</think> filter for reasoning models.
# ---------------------------------------------------------------------------
class _ThinkStripFilter:
    """
    Incremental filter that swallows the first ``<think>...</think>`` block
    of a streamed model output. Used to keep CoT tokens out of the user's
    chat bubble while still streaming the actual answer.

    Handles partial tags split across chunk boundaries (e.g. one chunk ends
    with ``<thi`` and the next starts with ``nk>``) by buffering up to
    ``len('</think>') - 1`` characters at the tail.
    """

    OPEN = "<think>"
    CLOSE = "</think>"

    def __init__(self) -> None:
        self._buf = ""
        self._in_think = False
        self._post_think = False  # already passed through one think block

    def feed(self, chunk: str) -> str:
        """Consume the next streamed chunk, return text safe to display."""
        if self._post_think:
            return chunk

        self._buf += chunk
        out = ""
        while self._buf:
            if self._in_think:
                idx = self._buf.find(self.CLOSE)
                if idx < 0:
                    # Keep just enough tail in case </think> straddles.
                    keep = max(0, len(self._buf) - (len(self.CLOSE) - 1))
                    self._buf = self._buf[keep:]
                    return out
                self._buf = self._buf[idx + len(self.CLOSE):]
                self._in_think = False
                self._post_think = True
                out += self._buf
                self._buf = ""
                return out
            else:
                idx = self._buf.find(self.OPEN)
                if idx < 0:
                    # No opener seen yet. Emit everything except a possible
                    # partial-prefix tail like "<thin".
                    keep = max(0, len(self._buf) - (len(self.OPEN) - 1))
                    out += self._buf[:keep]
                    self._buf = self._buf[keep:]
                    return out
                # Emit pre-tag text, then enter think state.
                out += self._buf[:idx]
                self._buf = self._buf[idx + len(self.OPEN):]
                self._in_think = True
        return out

    def flush(self) -> str:
        """Emit anything still buffered when the stream ends."""
        if self._in_think:
            return ""  # truncated mid-think → drop
        out, self._buf = self._buf, ""
        return out


# ---------------------------------------------------------------------------
# LLM loader
# ---------------------------------------------------------------------------
def load_local_llm(config: Optional[LLMConfig] = None) -> ChatOpenAI:
    """
    Start (or reuse) the local llama-server process and return a langchain
    ChatOpenAI pointed at its OpenAI-compatible endpoint.

    Why a server, not in-process bindings? The Windows CUDA wheels for
    llama-cpp-python are stuck at v0.3.4 which doesn't know the qwen3 GGUF
    architecture. The standalone llama-server.exe from upstream llama.cpp
    is current, GPU-enabled, and OpenAI-compatible.
    """
    cfg = config or LLMConfig()
    model_path = Path(cfg.model_path).expanduser().resolve()

    if not model_path.exists():
        raise FileNotFoundError(
            f"GGUF model not found at: {model_path}\n"
            "Place a quantized .gguf file at ./models/model.gguf or set the "
            "LLAMA_MODEL_PATH environment variable."
        )

    # Build the server config and start the subprocess.
    server_cfg = cfg.server_config or ServerConfig(
        model_path=model_path,
        n_ctx=cfg.n_ctx,
        n_gpu_layers=cfg.n_gpu_layers,
    )
    try:
        server = get_shared_server(server_cfg)
    except Exception as exc:
        logger.exception("Failed to start llama-server.")
        raise RuntimeError(f"Could not start llama-server: {exc}") from exc

    try:
        logger.info(
            "Connecting ChatOpenAI client to llama-server at %s "
            "(n_ctx=%d, n_gpu_layers=%d)",
            server_cfg.openai_base_url,
            cfg.n_ctx,
            cfg.n_gpu_layers,
        )
        # llama-server doesn't enforce auth; ChatOpenAI requires *some* key.
        llm = ChatOpenAI(
            base_url=server_cfg.openai_base_url,
            api_key="not-needed",
            # The model name is whatever llama-server is configured with - it
            # ignores the field but langchain validates non-emptiness.
            model="local",
            temperature=cfg.temperature,
            max_tokens=cfg.max_tokens,
            top_p=cfg.top_p,
            # repeat_penalty maps onto OpenAI's frequency_penalty loosely;
            # llama-server passes through model_kwargs as sampler params.
            model_kwargs={
                "frequency_penalty": 0.0,
                "presence_penalty": 0.0,
            },
            timeout=600,
            max_retries=2,
            **cfg.extra_kwargs,
        )
        logger.info("ChatOpenAI client ready.")
        return llm
    except Exception as exc:
        logger.exception("Failed to build ChatOpenAI client.")
        raise RuntimeError(f"Could not build ChatOpenAI client: {exc}") from exc


# ---------------------------------------------------------------------------
# Helper: format retrieved docs for the prompt
# ---------------------------------------------------------------------------
def format_documents(
    docs: List[Document],
    summaries: Optional[Dict[str, str]] = None,
) -> str:
    """Render retrieved chunks (and optional doc-level summaries) for the
    answer prompt.

    v0.6.0: every chunk is tagged with a numeric id [1], [2], ... shown
    in its header. The model cites these ids inline -- e.g. "..[2].." --
    and the server uses the same id to look up exactly which chunk a
    citation refers to. The pre-v0.6.0 "[filename p.5]" pattern produced
    page-level (file, page) tuples that conflated multiple chunks on
    the same page; numeric ids are chunk-level and unambiguous.

    We stamp the assigned id back into each Document's metadata under
    ``prompt_id`` so downstream code (citation filter, trace tagging,
    sentence-paragraph binding) addresses the same numbering the model
    saw. Mutation in-place is OK because the chain owns these Documents
    -- they came out of retrieval and aren't shared with caller code.

    When ``summaries`` is provided, the rendered context begins with a
    compact 'Documents in scope' overview followed by 'Excerpts'. This
    gives the model document-level awareness even when the retrieved
    excerpts skew toward one file.
    """
    parts: List[str] = []
    if summaries:
        parts.append(doc_summaries.format_overview(summaries))
        parts.append("")  # blank line
        parts.append(
            "Excerpts (each has a numeric tag in brackets; cite as [N]):"
        )
    for i, d in enumerate(docs, start=1):
        meta = dict(d.metadata or {})
        filename = meta.get("filename", "unknown")
        page = meta.get("page") or meta.get("pages", "?")
        heading = meta.get("headings", "")
        meta["prompt_id"] = i
        d.metadata = meta
        header = f"[{i}] {filename} (page {page})"
        if heading:
            header += f", section: {heading}"
        parts.append(f"{header}\n{d.page_content}")
    return "\n\n".join(parts) if parts else "(no relevant context found)"


# ---------------------------------------------------------------------------
# Citation parsing for source filtering.
# ---------------------------------------------------------------------------
# Match `[filename.ext p.5]`, `[filename.ext p. 5]`, `[filename.ext, p.5]`,
# `[filename.ext p.5-7]` for any of our supported source extensions.
# v0.2.0 broadened beyond .pdf to also accept .docx / .md / .txt / images,
# otherwise citations on pasted text / markdown notes never matched and
# the Sources panel silently fell through to showing every retrieved doc.
_CITATION_RE = re.compile(
    r"\[([^\[\]]+?\.(?:pdf|docx|epub|md|markdown|txt|jpe?g|png|tiff?|bmp))"
    r"\s*[, ]\s*(?:p\.?|page)\s*(\d+(?:\s*[-–]\s*\d+)?)\]",
    re.IGNORECASE,
)


def parse_citations(answer_text: str) -> List[tuple[str, str]]:
    """Extract (filename, page-or-range) tuples cited in the answer."""
    if not answer_text:
        return []
    return [(m.group(1).strip(), m.group(2).strip())
            for m in _CITATION_RE.finditer(answer_text)]


def filter_sources_by_citations(
    docs: List[Document],
    answer_text: str,
) -> List[Document]:
    """Legacy v0.5.x source filter that matched on `[filename p.5]`-style
    citations. Kept ONLY as a fallback for when an answer somehow leaked
    the old format despite the v0.6.0 prompt; the strict path goes
    through ``filter_sources_by_inline_ids``.

    Returns the cited subset, or an empty list when nothing parses (the
    v0.6.0 contract: an un-cited answer has zero sources).
    """
    cites = parse_citations(answer_text)
    if not cites:
        return []

    cited_pairs: set[tuple[str, str]] = set()
    for fn, page in cites:
        # Range like "5-7" → all pages in [5,7]
        if "-" in page or "–" in page:
            try:
                lo, hi = re.split(r"[-–]", page)
                for p in range(int(lo.strip()), int(hi.strip()) + 1):
                    cited_pairs.add((fn.lower(), str(p)))
            except Exception:
                cited_pairs.add((fn.lower(), page.strip()))
        else:
            cited_pairs.add((fn.lower(), page.strip()))

    out: List[Document] = []
    seen_keys: set[tuple[str, str]] = set()
    for d in docs:
        meta = d.metadata or {}
        fn = str(meta.get("filename", "")).lower()
        page = str(meta.get("page", "") or "")
        key = (fn, page)
        if key in cited_pairs and key not in seen_keys:
            out.append(d)
            seen_keys.add(key)
    return out


# ---------------------------------------------------------------------------
# v0.6.0 numeric inline citations.
# ---------------------------------------------------------------------------
# Matches `[1]`, `[12]`, ... — never a word inside brackets, never `[1-3]`.
# We deliberately ignore `[1, 2]` patterns; model is told to write `[1][2]`.
_INLINE_CITATION_RE = re.compile(r"\[(\d{1,3})\]")


def parse_inline_citations(answer_text: str) -> List[int]:
    """Return the 1-based source ids the answer cites, in order of first
    appearance. Deduped. Out-of-range / non-numeric refs are filtered by
    the caller against ``len(docs)``."""
    if not answer_text:
        return []
    seen: List[int] = []
    seen_set: set[int] = set()
    for m in _INLINE_CITATION_RE.finditer(answer_text):
        try:
            n = int(m.group(1))
        except ValueError:
            continue
        if n in seen_set:
            continue
        seen.append(n)
        seen_set.add(n)
    return seen


def filter_sources_by_inline_ids(
    docs: List[Document],
    answer_text: str,
) -> List[Document]:
    """v0.6.0 strict source selection. Returns ONLY the chunks whose
    1-based ``prompt_id`` (assigned by ``format_documents`` at prompt
    render time) appears as ``[N]`` in the answer.

    Critical behaviour: an answer with zero citations gets zero
    sources. Pre-v0.6.0 the page-based filter fell back to "return
    every retrieved doc" in that case, which is exactly what produced
    the "Sources panel full of irrelevant chunks" pain point. Here we
    accept that an uncited answer shows no chips -- that's the right
    signal to the user.

    Falls through to the legacy (filename, page) filter ONLY when the
    answer contains no ``[N]`` patterns at all AND the legacy regex
    finds at least one ``[file p.5]`` mention -- belt-and-braces in
    case the model ignores the new format on some prompt template.
    """
    if not docs or not answer_text:
        return []
    ids = parse_inline_citations(answer_text)
    if ids:
        max_id = len(docs)
        # Build prompt_id -> doc index, preferring the first occurrence
        # if for some reason the same id appears twice (shouldn't happen
        # because format_documents enumerates).
        by_id: Dict[int, Document] = {}
        for d in docs:
            pid = (d.metadata or {}).get("prompt_id")
            if isinstance(pid, int) and pid not in by_id:
                by_id[pid] = d
        return [by_id[n] for n in ids if 1 <= n <= max_id and n in by_id]
    # No numeric citations -- try legacy parser before giving up.
    return filter_sources_by_citations(docs, answer_text)


def sanitize_answer_citations(answer_text: str, max_id: int) -> str:
    """Strip ``[N]`` whose N is outside ``[1, max_id]`` -- those are
    hallucinated citations the user shouldn't see (rendering them would
    let the model fabricate references that don't resolve to anything).
    Leaves valid citations untouched."""
    if not answer_text or max_id <= 0:
        return answer_text

    def _repl(m: re.Match) -> str:
        try:
            n = int(m.group(1))
        except ValueError:
            return ""
        return m.group(0) if 1 <= n <= max_id else ""

    return _INLINE_CITATION_RE.sub(_repl, answer_text)


# ---------------------------------------------------------------------------
# Stateful RAG chain
# ---------------------------------------------------------------------------
class RAGChain:
    """
    Stateful RAG chain combining:
      - per-session chat history
      - standalone-question rewriting
      - local Chroma retrieval
      - local llama.cpp answer generation
    """

    def __init__(
        self,
        vector_store: Optional[LocalVectorStore] = None,
        llm: Optional[ChatOpenAI] = None,
        llm_config: Optional[LLMConfig] = None,
        k: int = 6,
        use_reranker: bool = True,
        rerank_multiplier: int = 3,
    ) -> None:
        self.vector_store = vector_store or build_default_store()
        self.llm = llm or load_local_llm(llm_config)
        self.k = k
        # Cross-encoder reranker: over-fetch k * rerank_multiplier candidates
        # by bi-encoder similarity, then keep the top-k after the cross-encoder
        # rescores them. Disable with use_reranker=False (e.g. for ragas where
        # determinism matters more than precision).
        self.use_reranker = use_reranker
        self.rerank_multiplier = max(1, rerank_multiplier)
        self._sessions: Dict[str, BaseChatMessageHistory] = {}

        # v0.4.0: per-session rolling window of chunks the chain saw on
        # the LAST few turns. We mix these into the candidate pool of
        # every subsequent retrieve call so context from earlier turns
        # (the methodology section, say) doesn't disappear just because
        # the new query's bi-encoder score on those chunks is weak. The
        # cross-encoder rerank that follows is the safety net: irrelevant
        # carry-over chunks score low and get dropped.
        from collections import deque  # local import to keep top tidy
        self._SESSION_CHUNK_MAX = 12
        self._session_chunks: Dict[str, deque] = {}

        self._chain = self._build_chain()

    # --------------------------------------------------- session chunk memory
    def _recent_session_chunks(self, session_id: str) -> List[Document]:
        """Return up to SESSION_CHUNK_MAX chunks the chain selected for
        previous turns in this session. Newest at the end (so the
        deque's natural order is fine)."""
        if not session_id:
            return []
        return list(self._session_chunks.get(session_id, []))

    def export_session_memory(self, session_id: str) -> List[Dict]:
        """v0.5.0: serialise the rolling chunk window for persistence.
        Each chunk becomes a flat dict {filename, chunk_index,
        page_content, metadata} so it round-trips through
        ConversationStore.set_recent_chunks → JSON → preload at
        backend restart. Items keep their deque order (oldest first)."""
        if not session_id:
            return []
        dq = self._session_chunks.get(session_id)
        if not dq:
            return []
        out: List[Dict] = []
        for d in dq:
            md = dict(d.metadata or {})
            out.append({
                "page_content": d.page_content,
                "metadata": md,
            })
        return out

    def preload_session_memory(
        self, session_id: str, items: List[Dict],
    ) -> None:
        """v0.5.0: restore a previously-exported rolling chunk window
        (typically pulled out of a conv JSON at backend boot). Chunks
        whose filename is no longer in the vector store are silently
        dropped -- the user might have deleted that doc between
        sessions, in which case carrying its chunks across would
        produce stale citations."""
        if not session_id or not items:
            return
        from collections import deque
        try:
            live_files = set(self.vector_store.list_filenames())
        except Exception:
            live_files = set()
        restored: List[Document] = []
        for it in items[-self._SESSION_CHUNK_MAX:]:
            if not isinstance(it, dict):
                continue
            md = it.get("metadata") or {}
            fn = md.get("filename")
            if live_files and fn not in live_files:
                continue
            page_content = it.get("page_content") or ""
            if not page_content:
                continue
            restored.append(Document(page_content=page_content, metadata=md))
        if not restored:
            return
        self._session_chunks[session_id] = deque(
            restored, maxlen=self._SESSION_CHUNK_MAX,
        )
        logger.info(
            "Restored %d recent-turn chunks for session %s.",
            len(restored), session_id,
        )

    def _remember_session_chunks(
        self, session_id: str, docs: List[Document],
    ) -> None:
        """Push the chunks we used on this turn into the session's
        rolling window. Dedupes against what's already in the deque
        by (filename, chunk_index) so the same chunk doesn't crowd
        out variety across multiple turns about the same passage."""
        if not session_id or not docs:
            return
        from collections import deque
        dq = self._session_chunks.setdefault(
            session_id, deque(maxlen=self._SESSION_CHUNK_MAX),
        )
        seen = {
            (d.metadata.get("filename"), d.metadata.get("chunk_index"))
            for d in dq
        }
        for d in docs:
            key = (d.metadata.get("filename"), d.metadata.get("chunk_index"))
            if key in seen:
                continue
            dq.append(d)
            seen.add(key)
        self._chain_with_history = RunnableWithMessageHistory(
            self._chain,
            self._get_session_history,
            input_messages_key="question",
            history_messages_key="chat_history",
            output_messages_key="answer",
        )

        # Chitchat short-circuit: skips retrieval, calls LLM directly with
        # the conversational prompt. Same memory backing as the main chain.
        self._chitchat_chain = (
            CHITCHAT_PROMPT
            | self.llm
            | StrOutputParser()
            | RunnableLambda(self._strip_reasoning)
        )
        self._chitchat_with_history = RunnableWithMessageHistory(
            self._chitchat_chain,
            self._get_session_history,
            input_messages_key="question",
            history_messages_key="chat_history",
        )

    # ----------------------------------------------------------- session mgmt
    def _get_session_history(self, session_id: str) -> BaseChatMessageHistory:
        if session_id not in self._sessions:
            self._sessions[session_id] = InMemoryChatMessageHistory()
        return self._sessions[session_id]

    def hydrate_session(
        self,
        session_id: str,
        messages: List[Dict[str, str]],
    ) -> None:
        """Replace the in-memory chat history for ``session_id`` with the
        given list of ``{role, content}`` dicts. Used to load a saved
        conversation from disk so the rewriter / chat memory carry the
        right context across server restarts.

        v0.5.0: assistant messages may be in variants shape; we project
        to the active variant's content so the chain only ever sees a
        flat ``{role, content}`` history."""
        history = self._get_session_history(session_id)
        history.clear()
        for m in messages:
            role = m.get("role")
            if role == "assistant" and isinstance(m.get("variants"), list):
                variants = m["variants"] or []
                if not variants:
                    continue
                idx = m.get("active_variant", 0)
                if not isinstance(idx, int) or idx < 0 or idx >= len(variants):
                    idx = 0
                content = (variants[idx].get("content") or "")
            else:
                content = m.get("content") or ""
            if not content:
                continue
            if role == "user":
                history.add_user_message(content)
            elif role == "assistant":
                history.add_ai_message(content)

    def generate_title(self, user_msg: str, assistant_msg: str) -> str:
        """Ask the local LLM for a short title summarising the first turn
        of a conversation. Used for auto-titles in the sidebar list."""
        if not user_msg.strip():
            return "New chat"
        prompt_text = (
            f"{QWEN3_NO_THINK}\n"
            "Generate a concise title (4 to 6 words, no quotes, no "
            "trailing period) summarising this conversation. Reply in the "
            "SAME language as the user's message.\n\n"
            f"User: {user_msg[:600]}\n"
            f"Assistant: {(assistant_msg or '')[:600]}\n\n"
            "Title:"
        )
        try:
            resp = self.llm.invoke([
                SystemMessage(content=QWEN3_NO_THINK),
                HumanMessage(content=prompt_text),
            ])
            text = getattr(resp, "content", str(resp)) or ""
            cleaned = self._strip_reasoning(text).strip()
            # Drop quotes / Markdown emphasis the model loves to add.
            cleaned = re.sub(r"^[\"'`*]+|[\"'`*.!?]+$", "", cleaned).strip()
            # Clip to first line, max 80 chars.
            cleaned = cleaned.splitlines()[0][:80] if cleaned else ""
            return cleaned or "New chat"
        except Exception:
            logger.exception("Auto-title generation failed.")
            return "New chat"

    def reset_session(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

    # ----------------------------------------------------------- chain wiring
    @staticmethod
    def _strip_reasoning(text: str) -> str:
        """
        Remove <think>...</think> blocks emitted by reasoning models
        (DeepSeek-R1, Qwen-Thinking, etc.). Also handles the case where
        the model gets truncated mid-think and never closes the tag —
        in which case we drop everything from <think> onward.
        """
        if not text:
            return ""
        # Closed think blocks
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE)
        # Unclosed leading think (model still inside CoT when output ended)
        text = re.sub(r"<think>.*\Z", "", text, flags=re.DOTALL | re.IGNORECASE)
        # Strip stray tags
        text = re.sub(r"</?think>", "", text, flags=re.IGNORECASE)
        return text.strip()

    def _build_chain(self) -> Runnable:
        condense_chain = CONDENSE_PROMPT | self.llm | StrOutputParser()

        def _post_condense_factory():
            """Closure that has access to the original question for sanity checks."""
            def _post(inputs: dict) -> str:
                raw = inputs["_raw"]
                original = inputs["question"]
                cleaned = self._strip_reasoning(raw)
                # Strip quotes / labels the model loves to add.
                cleaned = re.sub(r'^["\'\s]+|["\'\s]+$', "", cleaned)
                cleaned = re.sub(
                    r"^(query|search query|standalone query)\s*[:\-]\s*",
                    "", cleaned, flags=re.IGNORECASE,
                )

                # Defensive fallback: small models often regurgitate prior
                # turns into the rewrite. If the rewrite is much longer than
                # the original, or is empty, prefer the user's actual question.
                if not cleaned:
                    logger.info("Rewriter empty -> using original question.")
                    return original
                if len(cleaned) > max(120, 3 * len(original)):
                    logger.info(
                        "Rewriter output suspiciously long (%d vs original %d) "
                        "-> using original question.",
                        len(cleaned), len(original),
                    )
                    return original

                logger.info("Query rewritten to: %s", cleaned)
                return cleaned
            return _post

        post = _post_condense_factory()
        # Keep both the raw rewrite and the original question in scope.
        condense_chain = (
            RunnablePassthrough.assign(_raw=condense_chain)
            | RunnableLambda(post)
        )

        def _retrieve(state: dict) -> List[Document]:
            # Fall back to the raw question if the rewriter produced nothing
            # useful (common with reasoning models that get truncated).
            query = state.get("standalone") or state["question"]
            if not query.strip():
                query = state["question"]

            # Follow-up boost: when the user's message looks like a
            # follow-up (short OR pronoun-heavy OR continuation marker),
            # append a snippet of the previous assistant turn to the
            # search query. Small models often produce a weak rewrite
            # for bare 'why?' / '为什么' / 'Why did THEY do that?', and
            # similarity search on those alone returns garbage.
            original = state.get("question", "")
            history = state.get("chat_history") or []
            if is_likely_followup(original, bool(history)):
                last_ai = next(
                    (m for m in reversed(history)
                     if getattr(m, "type", "") == "ai"
                     or m.__class__.__name__ == "AIMessage"),
                    None,
                )
                if last_ai is not None:
                    ai_snippet = (getattr(last_ai, "content", "") or "")[:300]
                    if ai_snippet:
                        query = f"{query} (context: {ai_snippet})"
                        logger.info(
                            "Follow-up boost engaged (original=%r, len=%d).",
                            original[:60], len(original),
                        )

            # Optional per-call scope: list of filenames to restrict
            # retrieval to. Prevents cross-document interference when the
            # vector store holds multiple PDFs.
            filenames = state.get("filenames") or None

            # Decide effective scope: explicit list, else everything indexed.
            effective_scope = (
                filenames if filenames else self.vector_store.list_filenames()
            )

            # Over-fetch when reranker is on so the cross-encoder has
            # something to rescore. Cap at 30 to keep rerank latency bounded.
            candidate_k = (
                min(self.k * self.rerank_multiplier, 30)
                if self.use_reranker
                else self.k
            )

            # v0.5.0: retrieval mode is configurable via
            # HUSHDOC_RETRIEVAL_MODE. 'hybrid' (BM25 + dense + RRF) is
            # the default. 'dense' restores the v0.4.x behaviour
            # (balanced when multi-doc, topk otherwise). 'bm25' is pure
            # keyword -- useful for exact-name / exact-code queries
            # where the embedding model is blind. Per-candidate
            # provenance ('dense' / 'bm25' / 'both') is stashed in the
            # ``_rrf_source`` metadata field so the trace tab can show
            # which channel found each chunk.
            retrieval_mode_env = os.environ.get(
                "HUSHDOC_RETRIEVAL_MODE", "hybrid",
            ).strip().lower()
            if retrieval_mode_env not in {"hybrid", "dense", "bm25"}:
                retrieval_mode_env = "hybrid"

            if retrieval_mode_env == "bm25":
                candidates = self.vector_store.similarity_search_bm25(
                    query, k=candidate_k, filenames=filenames,
                )
                for d in candidates:
                    (d.metadata or {}).setdefault("_rrf_source", "bm25")
                base_mode = "bm25"
            elif retrieval_mode_env == "hybrid":
                # Hybrid runs dense + BM25 over the full effective scope
                # (no balanced split -- the BM25 channel naturally
                # surfaces hits from rarely-mentioned docs that dense
                # would drown out). For single-doc scopes that's
                # equivalent to plain hybrid; for multi-doc it's
                # actually a stronger anti-crosstalk signal than the
                # balanced dense-only path.
                fused = self.vector_store.similarity_search_hybrid(
                    query, k=candidate_k, filenames=filenames,
                )
                candidates = []
                for doc, ranks in fused:
                    dense_rank, bm25_rank = ranks[0], ranks[1]
                    if dense_rank and bm25_rank:
                        source = "both"
                    elif dense_rank:
                        source = "dense"
                    else:
                        source = "bm25"
                    if doc.metadata is None:
                        doc.metadata = {}
                    doc.metadata["_rrf_source"] = source
                    candidates.append(doc)
                base_mode = "hybrid"
            elif len(effective_scope) >= 2:
                candidates = self.vector_store.similarity_search_balanced(
                    query, k=candidate_k, filenames=effective_scope,
                )
                for d in candidates:
                    (d.metadata or {}).setdefault("_rrf_source", "dense")
                base_mode = "balanced"
            else:
                candidates = self.vector_store.similarity_search(
                    query, k=candidate_k, filenames=filenames,
                )
                for d in candidates:
                    (d.metadata or {}).setdefault("_rrf_source", "dense")
                base_mode = "topk"

            # v0.4.0: mix in chunks the chain selected on previous turns of
            # this session. They're added to the candidate pool BEFORE the
            # cross-encoder rerank -- so if they're irrelevant to this
            # query, the reranker drops them; if they ARE relevant
            # (follow-up about the same passage), they survive even when
            # bi-encoder similarity on this query would have ranked them
            # too low to make the top-k. Deduped against fresh candidates
            # by (filename, chunk_index).
            session_id = state.get("session_id", "")
            mode_extra = ""
            if session_id:
                recent = self._recent_session_chunks(session_id)
                if recent:
                    seen = {
                        (d.metadata.get("filename"),
                         d.metadata.get("chunk_index"))
                        for d in candidates
                    }
                    added = 0
                    for d in recent:
                        key = (
                            d.metadata.get("filename"),
                            d.metadata.get("chunk_index"),
                        )
                        if key in seen:
                            continue
                        # Also respect the per-call filenames scope: if
                        # the user just changed scope, don't carry over
                        # chunks from the previous (different) scope.
                        if filenames and d.metadata.get("filename") not in filenames:
                            continue
                        # Tag carry-over so the trace tab can show "this
                        # chunk came from a previous turn's selection,
                        # not from this turn's retrieval channels."
                        if d.metadata is None:
                            d.metadata = {}
                        d.metadata = dict(d.metadata)
                        d.metadata["_rrf_source"] = "memory"
                        candidates.append(d)
                        seen.add(key)
                        added += 1
                    if added:
                        mode_extra = f"+memory({added})"
                        logger.info(
                            "Mixed %d recent-turn chunks into candidate pool.",
                            added,
                        )

            # Cross-encoder rerank: rescores (query, candidate) pairs and
            # keeps the top-k. No-op if reranker load failed or candidates
            # already fit the budget. The 'trace' variant also returns
            # per-candidate score / rank info for the UI's Retrieval-trace
            # panel; we stash it on state so the streaming path can ship
            # it out in the `done` event without restructuring the chain.
            from reranker import rerank_with_trace, adaptive_keep, mmr_reorder
            docs, trace = rerank_with_trace(query, candidates, top_k=self.k)
            reranked = self.use_reranker and len(candidates) > self.k

            # v0.6.0: post-rerank shaping.
            # (a) Adaptive truncation: drop tail docs whose score is on
            #     the wrong side of a cliff. Means an answer model with
            #     a clear top-3 doesn't get padded out to k=6 with weak
            #     chunks that would only add noise + spurious citations.
            # (b) MMR diversification: rotate near-duplicate top-k
            #     entries so the model sees distinct paragraphs instead
            #     of the same passage three times.
            #
            # Both are gated behind env vars so a user with bad results
            # on a specific corpus can flip them off without recompiling.
            adapt_on = os.environ.get(
                "HUSHDOC_ADAPTIVE_K", "1"
            ).strip().lower() not in {"0", "false", "off", ""}
            mmr_lambda_raw = os.environ.get("HUSHDOC_MMR_LAMBDA", "0.7").strip()
            try:
                mmr_lambda = float(mmr_lambda_raw)
            except ValueError:
                mmr_lambda = 0.7
            mmr_on = 0.0 < mmr_lambda < 1.0
            mode_suffix = ""
            if adapt_on and reranked:
                before = len(docs)
                docs, trace = adaptive_keep(docs, trace, min_keep=2)
                if len(docs) < before:
                    mode_suffix += f"+adaptive({len(docs)})"
            if mmr_on and len(docs) > 1:
                docs, trace = mmr_reorder(docs, trace, lambda_=mmr_lambda)
                mode_suffix += "+mmr"

            mode = base_mode + ("+rerank" if reranked else "") + mode_suffix + mode_extra

            # Remember the kept chunks for use on the NEXT turn. We track
            # what the chain actually selected (post-rerank), not the
            # full candidate set, so the memory stays high-precision.
            if session_id:
                self._remember_session_chunks(session_id, docs)

            # Tag each trace entry with which retrieval mode produced it
            # (frontend renders this as a small badge above the trace tab).
            state["retrieval_trace"] = trace
            state["retrieval_mode"] = mode

            scope_label = ",".join(filenames) if filenames else "ALL"
            logger.info(
                "Retrieved %d/%d chunks (%s, scope: %s).",
                len(docs), len(candidates), mode, scope_label,
            )
            return docs

        def _build_context(state: dict) -> str:
            # Pull doc summaries for whichever filenames showed up in the
            # retrieved excerpts (so the model sees both excerpt details
            # AND a doc-level overview to disambiguate cross-doc questions).
            seen = []
            for d in state["source_documents"]:
                fn = (d.metadata or {}).get("filename")
                if fn and fn not in seen:
                    seen.append(fn)
            summaries = doc_summaries.get_summaries_for(seen)
            return format_documents(state["source_documents"], summaries=summaries)

        # Bind helpers we'll need from the streaming path too.
        self._post_condense = post
        self._retrieve_fn = _retrieve
        self._condense_chain = condense_chain
        self._build_context = _build_context

        def _compute_expanded_hint(state: dict) -> str:
            """v0.4.0: when the condense chain produced a meaningfully
            different standalone form, surface it to the answer model.
            Empty string when raw == rewrite so the prompt stays clean
            on direct, pronoun-free questions."""
            standalone = (state.get("standalone") or "").strip()
            question = (state.get("question") or "").strip()
            if standalone and standalone != question:
                return (
                    f"(Follow-up note: the user's expanded intent, with "
                    f"pronouns resolved against prior turns, is: "
                    f"\"{standalone}\". Their actual message is below.)\n\n"
                )
            return ""

        # Pipeline: produce {question, chat_history, standalone, context,
        # source_documents, expanded_query_hint}
        pipeline = (
            RunnablePassthrough.assign(standalone=condense_chain)
            .assign(source_documents=_retrieve)
            .assign(context=_build_context)
            .assign(expanded_query_hint=RunnableLambda(_compute_expanded_hint))
        )

        answer_chain = (
            ANSWER_PROMPT
            | self.llm
            | StrOutputParser()
            | RunnableLambda(self._strip_reasoning)
        )

        chain = pipeline.assign(answer=answer_chain)
        return chain

    # --------------------------------------------------------------- main API
    def ask(
        self,
        question: str,
        session_id: str = "default",
        filenames: Optional[List[str]] = None,
    ) -> dict:
        """Run a single conversational turn. Returns the full pipeline state.

        Parameters
        ----------
        question : str
            User input.
        session_id : str
            Conversational memory key.
        filenames : list[str], optional
            Restrict retrieval to these source files. None / empty = search
            the entire vector store. Greetings short-circuit and ignore this.

        Greetings / meta-questions short-circuit to a chitchat reply that
        skips vector retrieval entirely.
        """
        if not question or not question.strip():
            raise ValueError("Question cannot be empty.")

        history = self._get_session_history(session_id)
        has_history = bool(history.messages)

        lang = detect_language(question)
        lang_directive = language_directive(lang)

        if is_chitchat(question, has_history=has_history):
            logger.info("Chitchat detected, bypassing retrieval (lang=%s).", lang)
            try:
                answer = self._chitchat_with_history.invoke(
                    {"question": question, "language_directive": lang_directive},
                    config={"configurable": {"session_id": session_id}},
                )
            except Exception as exc:
                logger.exception("Chitchat chain invocation failed.")
                raise RuntimeError("Chitchat chain failed.") from exc
            return {
                "question": question,
                "standalone_question": question,
                "answer": answer,
                "source_documents": [],
                "all_source_documents": [],
                "chitchat": True,
                "scope": None,
            }

        inputs: Dict[str, object] = {
            "question": question,
            "language_directive": lang_directive,
        }
        if filenames:
            inputs["filenames"] = list(filenames)

        try:
            result = self._chain_with_history.invoke(
                inputs,
                config={"configurable": {"session_id": session_id}},
            )
        except Exception as exc:
            logger.exception("RAG chain invocation failed.")
            raise RuntimeError("RAG chain failed to produce an answer.") from exc

        all_docs = result.get("source_documents", [])
        answer_text = result.get("answer", "")
        # v0.6.0: strict citation filter. Sources surfaced to the UI are
        # exactly the [N]-tagged chunks the answer referenced, deduped
        # and ordered. Hallucinated [N] outside the valid range are
        # stripped from the answer text so the UI never renders a chip
        # that points at nothing.
        answer_text = sanitize_answer_citations(answer_text, len(all_docs))
        cited = filter_sources_by_inline_ids(all_docs, answer_text)
        sentence_bindings = bindings_to_payload(
            bind_answer_to_sources(answer_text, cited)
        )

        return {
            "question": question,
            "standalone_question": result.get("standalone", question),
            "answer": answer_text,
            "source_documents": cited,
            "all_source_documents": all_docs,
            "chitchat": False,
            "scope": list(filenames) if filenames else None,
            "sentence_bindings": sentence_bindings,
        }

    def ask_no_memory(
        self,
        question: str,
        filenames: Optional[List[str]] = None,
    ) -> dict:
        """Stateless one-shot query, useful for evaluation."""
        inputs: Dict[str, object] = {
            "question": question,
            "chat_history": [],
            "language_directive": language_directive(detect_language(question)),
        }
        if filenames:
            inputs["filenames"] = list(filenames)
        try:
            result = self._chain.invoke(inputs)
        except Exception as exc:
            logger.exception("Stateless RAG chain invocation failed.")
            raise RuntimeError("Stateless RAG chain failed.") from exc
        return {
            "question": question,
            "standalone_question": result.get("standalone", question),
            "answer": result.get("answer", ""),
            "source_documents": result.get("source_documents", []),
            "scope": list(filenames) if filenames else None,
        }

    # ------------------------------------------------------------ streaming
    def stream(
        self,
        question: str,
        session_id: str = "default",
        filenames: Optional[List[str]] = None,
    ):
        """
        Token-by-token streaming variant of ``ask``. Yields tagged events:

          - ``("standalone", str)``   — the rewritten / fallback search query
          - ``("sources", List[Doc])``— retrieved chunks (RAG path only)
          - ``("token", str)``        — incremental answer text (with
            ``<think>...</think>`` blocks already stripped)
          - ``("done", dict)``        — final result, same shape as ``ask``

        Memory is updated with the cleaned full answer at the end of the
        stream, mirroring what RunnableWithMessageHistory does for ``ask``.
        """
        if not question or not question.strip():
            raise ValueError("Question cannot be empty.")

        history = self._get_session_history(session_id)
        chat_history = list(history.messages)
        has_history = bool(chat_history)

        lang = detect_language(question)
        lang_directive = language_directive(lang)

        # ---- chitchat path ----------------------------------------------
        if is_chitchat(question, has_history=has_history):
            logger.info("Chitchat detected (streaming, lang=%s).", lang)
            yield ("standalone", question)
            yield ("sources", [])

            messages = CHITCHAT_PROMPT.format_messages(
                chat_history=chat_history,
                question=question,
                language_directive=lang_directive,
            )
            full_raw = ""
            stripper = _ThinkStripFilter()
            for chunk in self.llm.stream(messages):
                piece = getattr(chunk, "content", str(chunk)) or ""
                if not piece:
                    continue
                full_raw += piece
                visible = stripper.feed(piece)
                if visible:
                    yield ("token", visible)
            tail = stripper.flush()
            if tail:
                yield ("token", tail)

            cleaned = self._strip_reasoning(full_raw)
            history.add_user_message(question)
            history.add_ai_message(cleaned)

            yield ("done", {
                "question": question,
                "standalone_question": question,
                "answer": cleaned,
                "source_documents": [],
                "all_source_documents": [],
                "chitchat": True,
                "scope": None,
            })
            return

        # ---- RAG path ---------------------------------------------------
        # 1. Standalone-question rewrite (non-streaming, short).
        try:
            raw = self._condense_chain.invoke(
                {"question": question, "chat_history": chat_history}
            )
        except Exception as exc:
            logger.exception("Condense chain failed during streaming.")
            raise RuntimeError("Failed to rewrite query.") from exc
        standalone = self._post_condense({"_raw": raw, "question": question})
        yield ("standalone", standalone)

        # 2. Retrieve. Pass chat_history so the follow-up retrieval boost
        # can pick up the previous assistant message for short questions,
        # and session_id so _retrieve can mix in chunks from previous turns.
        state = {
            "question": question,
            "standalone": standalone,
            "chat_history": chat_history,
            "session_id": session_id,
        }
        if filenames:
            state["filenames"] = list(filenames)
        docs = self._retrieve_fn(state)
        yield ("sources", docs)

        # 3. Stream the grounded answer (with doc summaries prepended).
        seen_files: List[str] = []
        for d in docs:
            fn = (d.metadata or {}).get("filename")
            if fn and fn not in seen_files:
                seen_files.append(fn)
        summaries = doc_summaries.get_summaries_for(seen_files)
        # v0.4.0: surface the rewriter's expanded standalone query to the
        # answer model when it materially differs from the raw question
        # (typical for pronoun-anchored follow-ups). Helps Qwen3-1.7B-class
        # models resolve 'they/it/this' without having to mentally trace
        # the chat history.
        expanded_hint = ""
        if standalone and standalone.strip() != question.strip():
            expanded_hint = (
                f"(Follow-up note: the user's expanded intent, with pronouns "
                f"resolved against prior turns, is: \"{standalone.strip()}\". "
                f"Their actual message is below.)\n\n"
            )
        messages = ANSWER_PROMPT.format_messages(
            chat_history=chat_history,
            context=format_documents(docs, summaries=summaries),
            question=question,
            expanded_query_hint=expanded_hint,
            language_directive=lang_directive,
        )
        # v0.6.0 debug: dump the final rendered system + human so we
        # can verify [N] tags + citation directive actually reach the
        # model. Cheap (just a debug log), gated behind log level.
        if logger.isEnabledFor(logging.DEBUG):
            for m in messages:
                logger.debug("prompt[%s] %s", m.type, m.content[:1500])
        full_raw = ""
        stripper = _ThinkStripFilter()
        try:
            for chunk in self.llm.stream(messages):
                piece = getattr(chunk, "content", str(chunk)) or ""
                if not piece:
                    continue
                full_raw += piece
                visible = stripper.feed(piece)
                if visible:
                    yield ("token", visible)
            tail = stripper.flush()
            if tail:
                yield ("token", tail)
        except Exception as exc:
            logger.exception("Streaming answer generation failed.")
            raise RuntimeError("Streaming generation failed.") from exc

        # v0.6.0: strip any hallucinated [N] (outside the valid id range)
        # from the raw answer BEFORE we cache it as history -- otherwise a
        # follow-up turn would see a citation that doesn't resolve.
        cleaned = self._strip_reasoning(full_raw)
        cleaned = sanitize_answer_citations(cleaned, len(docs))
        history.add_user_message(question)
        history.add_ai_message(cleaned)

        # Strict citation filter (v0.6.0): the UI's Sources list is
        # exactly the [N]-tagged chunks the answer referenced. No
        # "return everything if the model forgot to cite" fallback --
        # that path was the root cause of the "Sources panel full of
        # irrelevant chunks" complaint in v0.5.0.
        cited = filter_sources_by_inline_ids(docs, cleaned)

        # Trace tagging matches the same numeric id space. Each candidate
        # chunk has a prompt_id stamped by format_documents; the cited
        # entries are those whose id appears in the answer.
        cited_ids = set(parse_inline_citations(cleaned))
        # Legacy fallback for trace tagging: if numeric ids found
        # nothing, fall back to the page-based key set so we still
        # surface SOMETHING on the trace tab.
        legacy_pairs: set[tuple[str, str]] = set()
        if not cited_ids:
            for fn, page in parse_citations(cleaned):
                if "-" in page or "–" in page:
                    try:
                        lo, hi = re.split(r"[-–]", page)
                        for p in range(int(lo.strip()), int(hi.strip()) + 1):
                            legacy_pairs.add((fn.lower(), str(p)))
                    except Exception:
                        legacy_pairs.add((fn.lower(), page.strip()))
                else:
                    legacy_pairs.add((fn.lower(), page.strip()))
        trace = state.get("retrieval_trace") or []
        for entry in trace:
            pid = entry.get("chunk_index")
            # The trace's chunk_index is the index from rerank, not our
            # prompt_id. So we resolve via filename+page or prompt_id.
            # Cleanest: look up each cited doc's filename+page and mark
            # the trace row that matches.
            entry["cited"] = False
        cited_keys = {
            (
                str((d.metadata or {}).get("filename", "")).lower(),
                str((d.metadata or {}).get("page", "")),
            )
            for d in cited
        } | legacy_pairs
        for entry in trace:
            key = (
                str(entry.get("filename", "")).lower(),
                str(entry.get("page", "")),
            )
            entry["cited"] = key in cited_keys

        # v0.6.0: per-sentence binding of the answer to specific
        # paragraphs inside the cited chunks. The frontend renders each
        # [N] chip with a hover popover containing the matched paragraph.
        sentence_bindings = bindings_to_payload(
            bind_answer_to_sources(cleaned, cited)
        )

        yield ("done", {
            "question": question,
            "standalone_question": standalone,
            "answer": cleaned,
            "source_documents": cited,
            "all_source_documents": docs,
            "chitchat": False,
            "scope": list(filenames) if filenames else None,
            # v0.2.0: per-candidate retrieval trace for the UI's drawer.
            "retrieval_trace": trace,
            "retrieval_mode": state.get("retrieval_mode", ""),
            # v0.6.0: sentence-level grounding for the inline citation
            # hover popover. List of {text, start, end, citations[],
            # paragraphs[{prompt_id, filename, page, paragraph, score}]}.
            "sentence_bindings": sentence_bindings,
        })

    # ----------------------------------------------------------- summaries
    SUMMARY_SYSTEM = (
        QWEN3_NO_THINK + "\n"
        "Summarize the document in 2-3 sentences. Be concrete: name the "
        "topic / domain, the methodology or approach, and the main "
        "contribution or finding. No filler, no preamble. Keep it under "
        "60 words."
    )

    def summarize_document(self, filename: str, full_text: str) -> str:
        """Generate a 2-3 sentence summary of a document and cache it.
        Idempotent — returns the cached summary if one already exists."""
        existing = doc_summaries.get_summary(filename)
        if existing:
            return existing
        # 12K chars covers most paper abstracts + intros; the model only
        # needs the gist, not the entire body.
        excerpt = (full_text or "")[:12000]
        if not excerpt.strip():
            return ""
        try:
            messages = [
                SystemMessage(content=self.SUMMARY_SYSTEM),
                HumanMessage(content=(
                    f"Document: {filename}\n\nContent:\n{excerpt}\n\n"
                    "Summary:"
                )),
            ]
            resp = self.llm.invoke(messages)
            text = self._strip_reasoning(getattr(resp, "content", str(resp)))
            text = " ".join(text.split())
            doc_summaries.set_summary(filename, text)
            logger.info("Summarized %s: %s", filename, text[:80])
            return text
        except Exception as exc:
            logger.exception("Failed to summarize %s", filename)
            return ""



# ---------------------------------------------------------------------------
# CLI: simple REPL for local testing
# ---------------------------------------------------------------------------
def main() -> None:
    chain = RAGChain()
    session_id = "cli"
    print("Local RAG REPL. Ctrl-C to quit.\n")
    try:
        while True:
            q = input("you> ").strip()
            if not q:
                continue
            result = chain.ask(q, session_id=session_id)
            print(f"\nbot> {result['answer']}\n")
            srcs = result["source_documents"]
            if srcs:
                print("sources:")
                for d in srcs:
                    meta = d.metadata
                    print(f"  - {meta.get('filename')} p.{meta.get('page', '?')}")
                print()
    except (KeyboardInterrupt, EOFError):
        print("\nbye.")


if __name__ == "__main__":
    main()
