/**
 * Tiny i18n layer — v0.7.0.
 *
 * Why custom and not react-i18next: the surface is small enough (≈50
 * user-visible strings) that a single typed dictionary + a `t()`
 * function buys all the safety we need without dragging in ~30 KB of
 * framework. Adding a new key is "add it to both `en` and `zh` blocks
 * + use it" -- the TypeScript compiler enforces no key is missing in
 * either language.
 *
 * Interpolation: ``"{n} chunks · ready"`` + ``t("...", { n: 42 })``
 * → ``"42 chunks · ready"``. No nesting, no pluralisation rules --
 * the few plural cases we have just bake the count into one string
 * for each language (Chinese has no plural form so it's free; English
 * we accept the rare "1 chunks" oddity over a heavier system).
 *
 * Edge-case strings (developer-only error toasts, ARIA descriptions
 * for screen-reader-only debug surfaces) deliberately stay English
 * in v0.7.0 -- documented in CHANGELOG.
 */

export type Lang = "en" | "zh"

/** All keys + their English values. Every other language MUST provide
 *  the same key set (TypeScript enforces this via ``Dict``). Strings
 *  may include ``{name}`` placeholders that ``t()`` substitutes. */
const EN = {
  // Brand stays as "Hushdoc" in every language -- no key for it.

  // Header / HealthPill
  "header.connecting": "Connecting…",
  "header.offline": "Backend offline",
  "header.ready": "{n} chunks · ready",
  "header.warming": "{n} chunks · warming up",
  "header.settings": "Settings",
  "header.toggleLight": "Switch to light",
  "header.toggleDark": "Switch to dark",

  // Sidebar
  "sidebar.chats": "CHATS",
  "sidebar.library": "LIBRARY",
  "sidebar.voice": "VOICE",
  "sidebar.newChat": "New chat",
  "sidebar.noChatsYet": "No saved chats yet.",
  "sidebar.addToLibrary": "Add to library",
  "sidebar.libraryEmpty":
    "Your library is empty. Add a PDF, DOCX, image, or markdown note — everything stays on your machine.",

  // Chat empty state
  "chat.emptyTitle": "What would you like to know?",
  "chat.emptyHint":
    "Upload a PDF, DOCX, or document photo from the sidebar, then ask away. Everything runs on your machine — nothing leaves it.",
  "chat.examples.summarize.title": "Summarize my documents",
  "chat.examples.summarize.sub": "give me the key takeaways",
  "chat.examples.compare.title": "Compare two papers",
  "chat.examples.compare.sub": "highlight what's different",
  "chat.examples.fact.title": "Find a specific fact",
  "chat.examples.fact.sub": "with an inline citation",
  "chat.examples.simple.title": "Explain it simply",
  "chat.examples.simple.sub": "for a non-expert",
  "chat.inputPlaceholder": "Ask anything about your documents...",
  "chat.footer": "Hushdoc runs entirely on your machine — nothing leaves it.",

  // Chat message tooltips
  "msg.copy": "Copy answer",
  "msg.copied": "Copied!",
  "msg.regenerate": "Regenerate answer",
  "msg.replayAudio": "Replay audio",
  "msg.pauseResume": "Pause / resume",
  "msg.stop": "Stop",
  "msg.send": "Send",
  "msg.prevVariant": "Previous answer",
  "msg.nextVariant": "Next answer",

  // Settings
  "settings.title": "Settings",
  "settings.loading": "Loading current settings…",
  "settings.upToDate": "All settings up to date",
  "settings.unsaved": "Unsaved changes",
  "settings.saving": "Applying — don't close this window.",
  "settings.save": "Save changes",
  "settings.close": "Close",
  "settings.section.language": "Language",
  "settings.section.language.desc":
    "Interface language. Affects buttons, labels and tooltips. Documents and chat content are not translated.",
  "settings.section.model": "Model file",
  "settings.section.model.tag": ".gguf",
  "settings.section.model.desc":
    "Path to the GGUF the chain should load. Relative paths resolve from the repo root. Changing this stops the running llama-server.exe and starts a fresh one against the new model — chat in progress will be interrupted.",
  "settings.section.model.dirtyWarn":
    "Saving will reload the model. The first request after that takes 10–30 s while the new model loads.",
  "settings.filePresent": "file present",
  "settings.fileMissing": "file missing",
  "settings.section.voice": "Voice features",
  "settings.section.voice.tag": "English-only · CPU",
  "settings.section.voice.desc":
    "Push-to-talk transcription (Whisper-base.en) and read-aloud replies (Kokoro-82M). Adds ~230 MB to disk once downloaded. Toggling this off hides the voice UI but doesn't delete the cached models.",
  "settings.voice.toggle": "Enable voice mode",
  "settings.voice.subReady": "ready",
  "settings.voice.subNotLoaded": "not loaded",
  "settings.voice.subStatusUnknown": "status unknown",
  "settings.voice.dlReady": "Models cached. They'll load on first use.",
  "settings.voice.dlIdle":
    "Models aren't cached yet. Pre-download to avoid a wait on first mic press.",
  "settings.voice.dlRunning": "Preparing models…",
  "settings.voice.dlDone": "Done",
  "settings.voice.dlDownload": "Download",
  "settings.section.cleanup": "Auto-cleanup on exit",
  "settings.cleanup.toggle": "Wipe local data automatically when I quit",
  "settings.cleanup.desc":
    "With this on, the GUI “Quit Hushdoc?” confirm popup is skipped on quit. Hushdoc wipes conversations, uploaded documents, and the vector index immediately, then exits. With it off (the default), you get the per-category confirm popup every time you close the window.",

  // Library
  "library.addToLibrary": "Add to library",
  "library.indexing": "Indexing…",
  "library.pasteText": "Paste text…",
  "library.pasteTitle": "Paste text into library",
  "library.empty":
    "Your library is empty. Add a PDF, DOCX, image, or markdown note — everything stays on your machine.",
  "library.allInScope": "all in scope",
  "library.allNInScope": "all {n} in scope",
  "library.nInScope": "{n}/{total} in scope",
  "library.selectAll": "Select all",
  "library.selectNone": "Select none",
  "library.clearAll": "Clear entire library…",
  "library.cancelTooltip": "Cancel — finish the current file, then stop",

  // Common
  "common.cancel": "Cancel",
  "common.delete": "Delete",
  "common.confirmDelete": "Confirm delete",
  "common.loading": "Loading…",
} as const

/** The structural type every language dict must satisfy. */
export type Dict = Record<keyof typeof EN, string>

const ZH: Dict = {
  // Brand stays "Hushdoc".

  // Header / HealthPill
  "header.connecting": "连接中…",
  "header.offline": "后端离线",
  "header.ready": "{n} 段 · 就绪",
  "header.warming": "{n} 段 · 预热中",
  "header.settings": "设置",
  "header.toggleLight": "切换到浅色",
  "header.toggleDark": "切换到深色",

  // Sidebar
  "sidebar.chats": "对话",
  "sidebar.library": "资料库",
  "sidebar.voice": "语音",
  "sidebar.newChat": "新对话",
  "sidebar.noChatsYet": "还没有保存的对话。",
  "sidebar.addToLibrary": "添加到资料库",
  "sidebar.libraryEmpty":
    "资料库还是空的。添加 PDF、DOCX、图片或 Markdown 笔记 —— 所有内容都只留在你的电脑上。",

  // Chat empty state
  "chat.emptyTitle": "想了解什么?",
  "chat.emptyHint":
    "从左侧上传 PDF、DOCX 或文档照片,然后随便问。一切都在你本地运行 —— 不出网。",
  "chat.examples.summarize.title": "总结我的文档",
  "chat.examples.summarize.sub": "给我提炼重点",
  "chat.examples.compare.title": "对比两篇论文",
  "chat.examples.compare.sub": "高亮差异点",
  "chat.examples.fact.title": "找一个具体事实",
  "chat.examples.fact.sub": "并附上行内引用",
  "chat.examples.simple.title": "用大白话解释",
  "chat.examples.simple.sub": "讲给外行听",
  "chat.inputPlaceholder": "针对你的文档,随便问…",
  "chat.footer": "Hushdoc 完全在你的电脑上运行 —— 不出网。",

  // Chat message tooltips
  "msg.copy": "复制回答",
  "msg.copied": "已复制!",
  "msg.regenerate": "重新生成",
  "msg.replayAudio": "重播语音",
  "msg.pauseResume": "暂停 / 继续",
  "msg.stop": "停止",
  "msg.send": "发送",
  "msg.prevVariant": "上一个回答",
  "msg.nextVariant": "下一个回答",

  // Settings
  "settings.title": "设置",
  "settings.loading": "正在读取当前设置…",
  "settings.upToDate": "所有设置已是最新",
  "settings.unsaved": "有未保存的更改",
  "settings.saving": "正在应用 —— 请不要关闭此窗口。",
  "settings.save": "保存更改",
  "settings.close": "关闭",
  "settings.section.language": "界面语言",
  "settings.section.language.desc":
    "界面显示语言,影响按钮、标签、提示文字。文档与对话内容不参与翻译。",
  "settings.section.model": "模型文件",
  "settings.section.model.tag": ".gguf",
  "settings.section.model.desc":
    "Chain 加载的 GGUF 路径。相对路径相对仓库根解析。改这个会停掉当前的 llama-server.exe,以新模型重启一个 —— 进行中的对话会被打断。",
  "settings.section.model.dirtyWarn":
    "保存后会重新加载模型,之后第一次请求要等 10-30 秒。",
  "settings.filePresent": "文件存在",
  "settings.fileMissing": "文件缺失",
  "settings.section.voice": "语音功能",
  "settings.section.voice.tag": "仅英文 · CPU",
  "settings.section.voice.desc":
    "按住说话转写(Whisper-base.en)和朗读回答(Kokoro-82M)。下载后占用约 230 MB。关掉只是隐藏语音 UI,不会删除已缓存的模型。",
  "settings.voice.toggle": "启用语音模式",
  "settings.voice.subReady": "就绪",
  "settings.voice.subNotLoaded": "未加载",
  "settings.voice.subStatusUnknown": "状态未知",
  "settings.voice.dlReady": "模型已缓存,首次使用时自动加载。",
  "settings.voice.dlIdle":
    "模型还没缓存。预先下载好可以避免第一次按麦克风时干等。",
  "settings.voice.dlRunning": "正在准备模型…",
  "settings.voice.dlDone": "完成",
  "settings.voice.dlDownload": "下载",
  "settings.section.cleanup": "退出时自动清理",
  "settings.cleanup.toggle": "退出时自动清空本地数据",
  "settings.cleanup.desc":
    "勾上后,退出时跳过 GUI 的 “退出 Hushdoc?” 确认弹窗。Hushdoc 会直接清空对话、上传的文档和向量索引,然后退出。不勾(默认),每次关窗口都会弹分类确认。",

  // Library
  "library.addToLibrary": "添加到资料库",
  "library.indexing": "正在索引…",
  "library.pasteText": "粘贴文字…",
  "library.pasteTitle": "粘贴文字到资料库",
  "library.empty":
    "资料库还是空的。添加 PDF、DOCX、图片或 Markdown 笔记 —— 所有内容都只留在你的电脑上。",
  "library.allInScope": "全部在检索范围",
  "library.allNInScope": "全部 {n} 项在检索范围",
  "library.nInScope": "{n}/{total} 在检索范围",
  "library.selectAll": "全选",
  "library.selectNone": "全不选",
  "library.clearAll": "清空整个资料库…",
  // NOTE: 清 (clear), not 请 (please) — easy mis-input to watch for.
  "library.cancelTooltip": "取消 —— 完成当前文件后停止",

  // Common
  "common.cancel": "取消",
  "common.delete": "删除",
  "common.confirmDelete": "确认删除",
  "common.loading": "加载中…",
}

export const DICTS: Record<Lang, Dict> = {
  en: EN as Dict,
  zh: ZH,
}

/** Format a template like ``"{n} chunks · ready"`` with the provided
 *  variables map. Missing variables are left as-is so a typo doesn't
 *  silently produce ``"undefined chunks · ready"``. */
function interpolate(
  template: string,
  vars: Record<string, string | number> | undefined,
): string {
  if (!vars) return template
  return template.replace(/\{(\w+)\}/g, (m, k) =>
    k in vars ? String(vars[k]) : m,
  )
}

/** Look up + interpolate. Pure function, no React. Used both by the
 *  ``useT()`` hook and any non-hook call sites (e.g. toast strings
 *  emitted from outside React). */
export function translate(
  lang: Lang,
  key: keyof Dict,
  vars?: Record<string, string | number>,
): string {
  const dict = DICTS[lang] ?? DICTS.en
  const template = dict[key] ?? DICTS.en[key] ?? String(key)
  return interpolate(template, vars)
}

/** Initial language: explicit user choice from localStorage > browser
 *  preference (``zh`` if navigator.language starts with zh, else en). */
export function detectLang(): Lang {
  try {
    const saved = localStorage.getItem("hushdoc-lang")
    if (saved === "en" || saved === "zh") return saved
  } catch {
    /* localStorage may be blocked in some embeddings */
  }
  if (typeof navigator !== "undefined" && navigator.language?.toLowerCase().startsWith("zh")) {
    return "zh"
  }
  return "en"
}
