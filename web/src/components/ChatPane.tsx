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
  /** Active persistent conversation id, or null when the user is on
   *  the empty / "new chat" state. */
  conversationId: string | null
  /** Called by ChatPane when the user sends the first message of a
   *  brand-new chat — the parent creates a conversation server-side
   *  and reports the new id back so ChatPane can resume the send. */
  onEnsureConversation: () => Promise<string>
  /** Called when the backend's auto-title SSE event arrives so the
   *  sidebar can update without a full refetch. */
  onTitleEvent?: (conversationId: string, title: string) => void
  /** Conv id whose hydration fetch should be skipped this render
   *  (set to the freshly-created id by the auto-create flow). */
  skipHydrationFor?: string | null
  /** Fires once after a suppressed hydration so the parent can reset. */
  onHydrationConsumed?: () => void
  scope?: string[] | null
  voice: ReturnType<typeof useVoice>
}

export interface ChatPaneHandle {
  clear: () => void
  focusInput: () => void
  cancel: () => void
}

export const ChatPane = forwardRef<ChatPaneHandle, ChatPaneProps>(
  function ChatPane({
    conversationId,
    onEnsureConversation,
    onTitleEvent,
    skipHydrationFor,
    onHydrationConsumed,
    scope,
    voice,
  }, ref) {
    // After each completed assistant turn, cache the FULL synthesised
    // audio on the message so the 🔊 replay button works. This is a
    // background fetch — the streaming-TTS pipeline already played the
    // answer aloud sentence-by-sentence as tokens arrived (via
    // onSentence below), so the user has already heard everything.
    // We only need a single blob URL stashed for replay.
    const onDone = useCallback(
      async (msg: Msg) => {
        if (!voice.enabled || !msg.content) return
        // Don't auto-play here — streaming TTS already did. Just cache.
        try {
          const wav = await (await fetch("/api/voice/synthesize", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ text: msg.content }),
          })).blob()
          const url = URL.createObjectURL(wav)
          attachAudioRef.current?.(msg.id, url)
        } catch {
          /* replay is a nice-to-have; don't toast on failure */
        }
      },
      [voice],
    )

    const { messages, send, stop, clear, streaming, error, patchMessage } =
      useChat({
        conversationId,
        scope,
        onDone,
        // Sentence-by-sentence streaming TTS: enqueue each completed
        // sentence to the voice worker as soon as it lands. Audio
        // starts playing while the rest of the answer is still being
        // generated, eliminating the 5-15s gap of dead air the old
        // "synthesise after done" approach left.
        onSentence: voice.feedStreamingTTS,
        onStreamComplete: voice.finishStreamingTTS,
        onTitle: onTitleEvent,
        skipHydrationFor,
        onHydrationConsumed,
      })

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

    // Wrap send so any in-flight TTS for the previous answer is killed
    // before a new turn starts. If there's no active conversation yet
    // (user just clicked "+ New chat" or we're at first launch), ask
    // the parent to create one server-side and use the freshly-minted
    // id directly — bypassing React's setState propagation lag.
    const sendWithCancel = useCallback(
      async (text: string) => {
        voice.stopPlayback()
        let id = conversationId
        if (!id) {
          try {
            id = await onEnsureConversation()
          } catch {
            // onEnsureConversation already toasts on failure
            return
          }
        }
        await send(text, { conversationId: id })
      },
      [conversationId, onEnsureConversation, send, voice],
    )

    // Voice input — once VAD stops + transcribe returns, send immediately.
    const startVoice = useCallback(async () => {
      const text = await voice.record()
      if (text) await sendWithCancel(text)
    }, [voice, sendWithCancel])

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
          onSend={sendWithCancel}
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
