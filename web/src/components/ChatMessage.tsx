import { User, Sparkles } from "lucide-react"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"

import { cn } from "@/lib/utils"
import type { ChatMessage as Msg } from "@/types"

import { Sources } from "./Sources"

/** One chat bubble. Assistant supports markdown + code + GFM tables. */
export function ChatMessage({ msg }: { msg: Msg }) {
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

        {!isUser && !msg.chitchat && msg.sources && msg.sources.length > 0 && (
          <Sources docs={msg.sources} standaloneQuery={msg.standaloneQuery} />
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
