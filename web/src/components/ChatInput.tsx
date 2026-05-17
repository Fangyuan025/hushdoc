import { forwardRef, useState, type KeyboardEvent } from "react"
import { useQuery } from "@tanstack/react-query"
import { ArrowUp, Square } from "lucide-react"
import TextareaAutosize from "react-textarea-autosize"

import { Button } from "@/components/ui/button"
import { apiHealth } from "@/lib/api"
import { useT } from "@/lib/lang-context"
import { cn } from "@/lib/utils"

interface ChatInputProps {
  disabled?: boolean
  streaming?: boolean
  placeholder?: string
  onSend: (text: string) => void
  onStop?: () => void
  /** Slot for an optional mic button rendered to the left of the textarea. */
  leftSlot?: React.ReactNode
}

/** Bottom-anchored textarea + send button. Enter sends, Shift+Enter newline. */
export const ChatInput = forwardRef<HTMLTextAreaElement, ChatInputProps>(
  function ChatInput(
    {
      disabled,
      streaming,
      placeholder,
      onSend,
      onStop,
      leftSlot,
    },
    ref,
  ) {
  const t = useT()
  const ph = placeholder ?? t("chat.inputPlaceholder")
  const [value, setValue] = useState("")

  // Surface the app version under the input. Same query-key as the
  // HealthPill so TanStack returns the cached response — no duplicate fetch.
  const { data: health } = useQuery({
    queryKey: ["health"],
    queryFn: apiHealth,
    refetchInterval: 5_000,
    staleTime: 5_000,
  })

  const submit = () => {
    const t = value.trim()
    if (!t || disabled) return
    onSend(t)
    setValue("")
  }

  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault()
      submit()
    }
  }

  return (
    <div className="border-t bg-background/80 px-4 py-3 backdrop-blur supports-[backdrop-filter]:bg-background/60">
      <div className="mx-auto flex max-w-3xl items-end gap-2 rounded-2xl border bg-card px-3 py-2 shadow-sm focus-within:ring-1 focus-within:ring-ring">
        {leftSlot}
        <TextareaAutosize
          ref={ref}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={onKey}
          placeholder={ph}
          minRows={1}
          maxRows={8}
          disabled={disabled}
          className={cn(
            "flex-1 resize-none border-0 bg-transparent px-1 py-1.5 text-[15px] outline-hidden",
            "placeholder:text-muted-foreground disabled:opacity-50",
          )}
        />
        {streaming ? (
          <Button
            type="button"
            size="icon"
            variant="secondary"
            onClick={onStop}
            title={t("msg.stop")}
          >
            <Square className="h-4 w-4 fill-current" />
          </Button>
        ) : (
          <Button
            type="button"
            size="icon"
            onClick={submit}
            disabled={disabled || !value.trim()}
            title={t("msg.send")}
          >
            <ArrowUp className="h-4 w-4" />
          </Button>
        )}
      </div>
      <div className="mx-auto mt-1.5 max-w-3xl text-center text-[10px] text-muted-foreground/70">
        {t("chat.footer")}
        {health?.version && (
          <span className="ml-2 opacity-60">v{health.version}</span>
        )}
      </div>
    </div>
  )
})
