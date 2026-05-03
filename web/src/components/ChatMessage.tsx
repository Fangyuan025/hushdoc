import { User, Sparkles, Volume2 } from "lucide-react"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"

import { cn } from "@/lib/utils"
import type { ChatMessage as Msg } from "@/types"

import { Sources } from "./Sources"

interface ChatMessageProps {
  msg: Msg
  /** Optional handler for the 🔊 replay icon next to the assistant
   *  bubble. Receives the cached blob URL to re-play. */
  onReplay?: (audioUrl: string) => void
}

/** One chat bubble. Assistant supports markdown + code + GFM tables. */
export function ChatMessage({ msg, onReplay }: ChatMessageProps) {
  const isUser = msg.role === "user"

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
            "rounded-2xl px-4 py-2.5 text-[15px] leading-relaxed",
            isUser
              ? "bg-primary text-primary-foreground"
              : "bg-card border border-border",
          )}
        >
          {isUser ? (
            <div className="whitespace-pre-wrap break-words">{msg.content}</div>
          ) : (
            <div
              className={cn(
                "prose prose-sm dark:prose-invert max-w-none [&_p]:my-1.5 [&_pre]:my-2",
                msg.streaming && "streaming-cursor",
              )}
            >
              {msg.content ? (
                <ReactMarkdown remarkPlugins={[remarkGfm]}>
                  {msg.content}
                </ReactMarkdown>
              ) : msg.streaming ? (
                <span className="text-muted-foreground">…</span>
              ) : null}
            </div>
          )}
        </div>

        {!isUser && (msg.audioUrl || (msg.sources && msg.sources.length > 0)) && (
          <div className="mt-1 flex items-center justify-between gap-2">
            <div className="min-w-0 flex-1">
              {!msg.chitchat && msg.sources && msg.sources.length > 0 && (
                <Sources
                  docs={msg.sources}
                  standaloneQuery={msg.standaloneQuery}
                />
              )}
            </div>
            {msg.audioUrl && onReplay && !msg.streaming && (
              <button
                type="button"
                onClick={() => onReplay(msg.audioUrl!)}
                className="shrink-0 rounded-md p-1 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
                title="Replay audio"
              >
                <Volume2 className="h-3.5 w-3.5" />
              </button>
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
