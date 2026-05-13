import { useState } from "react"
import {
  Check,
  Copy,
  Loader2,
  RefreshCw,
  Sparkles,
  User,
  Volume2,
} from "lucide-react"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import remarkMath from "remark-math"
import rehypeKatex from "rehype-katex"

import { cn } from "@/lib/utils"
import type { ChatMessage as Msg } from "@/types"

import { Sources } from "./Sources"

// Inserting the cursor character into the streaming content directly
// (rather than via a CSS ::after on the prose container) puts it inline
// at the actual last text position — even mid-paragraph, mid-list, or
// mid-code-block — which is what the user expects from a typewriter.
const STREAMING_CURSOR = "▍"

/** Loading indicator shown in the assistant bubble while it's streaming
 *  but no tokens have arrived yet. Cold-start latency is masked by the
 *  warmup ping in App.tsx, so a single neutral status line is all the
 *  user needs here. */
function StreamingPlaceholder() {
  return (
    <div className="flex items-center gap-2 text-muted-foreground">
      <Loader2 className="h-3.5 w-3.5 animate-spin" />
      <span className="text-sm">Generating…</span>
    </div>
  )
}

interface ChatMessageProps {
  msg: Msg
  /** Optional handler for the 🔊 replay icon next to the assistant
   *  bubble. Receives the cached blob URL to re-play. */
  onReplay?: (audioUrl: string) => void
  /** v0.2.0: regenerate this answer (re-run the same standalone query
   *  from the user message that produced it). Falsy = button hidden. */
  onRegenerate?: () => void
}

/** One chat bubble. Assistant supports markdown + code + GFM tables. */
export function ChatMessage({ msg, onReplay, onRegenerate }: ChatMessageProps) {
  const isUser = msg.role === "user"
  const [copied, setCopied] = useState(false)

  const onCopy = async () => {
    try {
      await navigator.clipboard.writeText(msg.content)
      setCopied(true)
      setTimeout(() => setCopied(false), 1200)
    } catch {
      /* clipboard might be blocked in some embeds; silently no-op */
    }
  }

  const showActionsRow =
    !isUser && !msg.streaming && msg.content && msg.content.length > 0

  return (
    <div className={cn("flex gap-3", isUser && "justify-end")}>
      {!isUser && (
        <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-primary/10 text-primary">
          <Sparkles className="h-4 w-4" />
        </div>
      )}
      <div className={cn("max-w-[78ch] min-w-0", isUser && "order-1")}>
        <div
          className={cn(
            "text-[15px] leading-relaxed",
            isUser
              ? "rounded-2xl bg-primary px-4 py-2.5 text-primary-foreground"
              : "py-0.5",
          )}
        >
          {isUser ? (
            <div className="whitespace-pre-wrap break-words">{msg.content}</div>
          ) : (
            <div className="prose prose-sm dark:prose-invert max-w-none [&_p]:my-1.5 [&_pre]:my-2 [&_.katex-display]:my-2">
              {msg.content ? (
                <ReactMarkdown
                  remarkPlugins={[remarkGfm, remarkMath]}
                  rehypePlugins={[rehypeKatex]}
                >
                  {msg.streaming
                    ? msg.content + STREAMING_CURSOR
                    : msg.content}
                </ReactMarkdown>
              ) : msg.streaming ? (
                <StreamingPlaceholder />
              ) : null}
            </div>
          )}
        </div>

        {/* Sources / trace row (skip chitchat — no docs anyway). */}
        {!isUser && !msg.chitchat && (
          (msg.sources && msg.sources.length > 0) ||
          (msg.retrievalTrace && msg.retrievalTrace.length > 0)
        ) && (
          <Sources
            docs={msg.sources || []}
            standaloneQuery={msg.standaloneQuery}
            trace={msg.retrievalTrace}
            mode={msg.retrievalMode}
          />
        )}

        {/* v0.2.0 action row: regenerate / copy / replay-audio. Only
            renders on completed assistant messages so the streaming
            bubble doesn't flicker. */}
        {showActionsRow && (
          <div className="mt-1 flex items-center gap-0.5 text-muted-foreground">
            {onRegenerate && (
              <ActionBtn
                title="Regenerate answer"
                onClick={onRegenerate}
              >
                <RefreshCw className="h-3.5 w-3.5" />
              </ActionBtn>
            )}
            <ActionBtn
              title={copied ? "Copied!" : "Copy answer"}
              onClick={onCopy}
            >
              {copied ? (
                <Check className="h-3.5 w-3.5 text-emerald-500" />
              ) : (
                <Copy className="h-3.5 w-3.5" />
              )}
            </ActionBtn>
            {msg.audioUrl && onReplay && (
              <ActionBtn
                title="Replay audio"
                onClick={() => onReplay(msg.audioUrl!)}
              >
                <Volume2 className="h-3.5 w-3.5" />
              </ActionBtn>
            )}
          </div>
        )}
      </div>
      {isUser && (
        <div className="order-2 flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-secondary text-secondary-foreground">
          <User className="h-4 w-4" />
        </div>
      )}
    </div>
  )
}

function ActionBtn({
  title,
  onClick,
  children,
}: {
  title: string
  onClick: () => void
  children: React.ReactNode
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={title}
      className="rounded-md p-1 transition-colors hover:bg-accent hover:text-foreground"
    >
      {children}
    </button>
  )
}
