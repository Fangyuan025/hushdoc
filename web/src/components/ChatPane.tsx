import {
  forwardRef,
  useCallback,
  useImperativeHandle,
  useRef,
} from "react"
import { ArrowDown, Pause, Sparkles, Volume2, X } from "lucide-react"

import { MicButton } from "@/components/MicButton"
import { Button } from "@/components/ui/button"
import { ScrollArea } from "@/components/ui/scroll-area"
import { useChat } from "@/hooks/useChat"
import { useStickyBottom } from "@/hooks/useStickyBottom"
import type { useVoice } from "@/hooks/useVoice"
import type { ChatMessage as Msg } from "@/types"

import { ChatInput } from "./ChatInput"
import { ChatMessage } from "./ChatMessage"

interface ChatPaneProps {
  sessionId: string
  scope?: string[] | null
  voice: ReturnType<typeof useVoice>
}

export interface ChatPaneHandle {
  clear: () => void
  focusInput: () => void
  cancel: () => void
}

export const ChatPane = forwardRef<ChatPaneHandle, ChatPaneProps>(
  function ChatPane({ sessionId, scope, voice }, ref) {
    // After each completed assistant turn, optionally synthesise + autoplay
    // and stash the resulting blob URL on the message for the replay icon.
    const onDone = useCallback(
      async (msg: Msg) => {
        if (!voice.enabled) return
        if (msg.chitchat) {
          // chitchat path also gets read out — same English-only filter
        }
        const url = await voice.synthesizeAndPlay(msg.content)
        if (url) {
          // Mutate the message in place via the chat hook's setter — easier
          // to expose a tiny patch helper than to plumb a new event through.
          attachAudioRef.current?.(msg.id, url)
        }
      },
      [voice],
    )

    const { messages, send, stop, clear, streaming, error, patchMessage } =
      useChat({ sessionId, scope, onDone })

    const attachAudioRef = useRef<(id: string, url: string) => void>(
      patchMessage,
    )
    attachAudioRef.current = patchMessage

    const { scrollRef, bottomRef, autoFollow, jumpToBottom } =
      useStickyBottom<HTMLDivElement>(messages)

    const inputRef = useRef<HTMLTextAreaElement>(null)
    useImperativeHandle(
      ref,
      () => ({
        clear: () => {
          // Hard-stop everything in flight so a "New chat" click feels
          // truly fresh: kill any ongoing TTS playback, abort streaming,
          // cancel a recording-in-progress, then wipe the messages.
          voice.stopPlayback()
          if (voice.state !== "idle") voice.cancel()
          if (streaming) stop()
          void clear()
        },
        focusInput: () => inputRef.current?.focus(),
        cancel: () => {
          // ESC priority: recording > streaming > playback.
          if (voice.state !== "idle") voice.cancel()
          else if (streaming) stop()
          else if (voice.isPlaying) voice.stopPlayback()
        },
      }),
      [clear, voice, streaming, stop],
    )

    // Voice input — once VAD stops + transcribe returns, send immediately.
    const startVoice = useCallback(async () => {
      const text = await voice.record()
      if (text) send(text)
    }, [voice, send])

    return (
      <div className="relative flex h-full min-h-0 flex-1 flex-col">
        <ScrollArea ref={scrollRef} className="flex-1">
          <div className="mx-auto w-full max-w-3xl space-y-6 px-4 py-6">
            {messages.length === 0 ? (
              <EmptyState />
            ) : (
              messages.map((msg) => (
                <ChatMessage key={msg.id} msg={msg} onReplay={voice.playUrl} />
              ))
            )}
            {error && (
              <div className="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-destructive">
                {error}
              </div>
            )}
            <div ref={bottomRef} />
          </div>
        </ScrollArea>

        {/* Floating "jump to bottom" pill, shown only when the user has
            scrolled away from the live tail. */}
        {!autoFollow && messages.length > 0 && (
          <div className="pointer-events-none absolute inset-x-0 bottom-24 z-10 flex justify-center">
            <Button
              type="button"
              variant="secondary"
              size="sm"
              className="pointer-events-auto h-7 rounded-full px-3 text-xs shadow-md"
              onClick={jumpToBottom}
            >
              <ArrowDown className="h-3 w-3" />
              Jump to latest
            </Button>
          </div>
        )}

        {/* Playback-control pill, only while TTS is playing. Lives just
            above the chat input so it doesn't collide with the answer
            text or the "Jump to latest" pill. */}
        {voice.isPlaying && (
          <div className="mx-auto -mb-1 flex max-w-3xl items-center justify-center gap-2 px-4 pt-2">
            <div className="flex items-center gap-1 rounded-full border bg-card px-2 py-1 text-xs shadow-sm">
              <Volume2 className="h-3 w-3 text-emerald-500" />
              <span className="text-muted-foreground">Reading aloud…</span>
              <Button
                type="button"
                size="icon-sm"
                variant="ghost"
                className="h-6 w-6"
                onClick={voice.togglePause}
                title="Pause / resume"
              >
                <Pause className="h-3 w-3" />
              </Button>
              <Button
                type="button"
                size="icon-sm"
                variant="ghost"
                className="h-6 w-6"
                onClick={voice.stopPlayback}
                title="Stop"
              >
                <X className="h-3 w-3" />
              </Button>
            </div>
          </div>
        )}

        <ChatInput
          ref={inputRef}
          streaming={streaming}
          onSend={send}
          onStop={stop}
          leftSlot={
            voice.enabled ? (
              <MicButton
                state={voice.state}
                level={voice.level}
                onStart={startVoice}
                onCancel={voice.cancel}
                disabled={streaming}
              />
            ) : null
          }
        />
      </div>
    )
  },
)

function EmptyState() {
  return (
    <div className="flex min-h-[60vh] flex-col items-center justify-center gap-3 text-center">
      <div className="flex h-12 w-12 items-center justify-center rounded-full bg-primary/10 text-primary">
        <Sparkles className="h-6 w-6" />
      </div>
      <h2 className="text-xl font-semibold tracking-tight">
        Ask anything about your documents.
      </h2>
      <p className="max-w-md text-sm text-muted-foreground">
        Upload a PDF, DOCX, or document photo from the sidebar, then start
        asking. Answers stream in with inline source citations. Everything
        runs on your machine — nothing leaves it.
      </p>
    </div>
  )
}
