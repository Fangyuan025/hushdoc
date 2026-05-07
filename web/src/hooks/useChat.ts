import { useCallback, useEffect, useRef, useState } from "react"
import { apiClearChat } from "@/lib/api"
import { conversationApi } from "@/hooks/useConversations"
import type { ChatMessage, DoneEvent, SourceDoc } from "@/types"

/**
 * Parses an SSE byte stream from a `fetch` Response into typed events.
 *
 * EventSource doesn't accept POST bodies, so we hand-roll the SSE state
 * machine on top of the response body's ReadableStream. Frame format:
 *   event: <name>\n
 *   data: <json>\n
 *   \n
 */
async function* parseSSE(
  resp: Response,
  signal: AbortSignal,
): AsyncGenerator<{ event: string; data: unknown }> {
  if (!resp.body) throw new Error("response has no body")
  const reader = resp.body.getReader()
  const decoder = new TextDecoder("utf-8")
  let buf = ""
  let cur = { event: "message", data: [] as string[] }
  const flush = () => {
    if (cur.data.length) {
      let payload: unknown = cur.data.join("\n")
      try {
        payload = JSON.parse(payload as string)
      } catch {
        /* leave as string */
      }
      const out = { event: cur.event, data: payload }
      cur = { event: "message", data: [] }
      return out
    }
    cur = { event: "message", data: [] }
    return null
  }

  while (!signal.aborted) {
    const { done, value } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })

    // Process complete lines.
    let nl: number
    while ((nl = buf.indexOf("\n")) !== -1) {
      const raw = buf.slice(0, nl).replace(/\r$/, "")
      buf = buf.slice(nl + 1)
      if (raw === "") {
        const out = flush()
        if (out) yield out
      } else if (raw.startsWith(":")) {
        // SSE comment, ignore
      } else if (raw.startsWith("event:")) {
        cur.event = raw.slice(6).trim()
      } else if (raw.startsWith("data:")) {
        cur.data.push(raw.slice(5).replace(/^ /, ""))
      }
    }
  }
  // Final flush in case stream ended without trailing blank line.
  const tail = flush()
  if (tail) yield tail
}

interface UseChatOptions {
  /** Optional persistent conversation id. When set, the backend
   *  appends each turn to ./chat_history/<id>.json AND emits a `title`
   *  event after the first turn so the sidebar list updates. */
  conversationId?: string | null
  /** Legacy in-memory key, used only if conversationId is null. */
  sessionId?: string
  scope?: string[] | null
  onDone?: (msg: ChatMessage) => void
  /** Called for each completed sentence as the answer streams in. Used
   *  by the voice pipeline to enqueue per-sentence TTS so audio plays
   *  while the rest of the answer is still arriving. */
  onSentence?: (sentence: string) => void
  /** Called once after the answer's last token. Lets the voice pipeline
   *  drain its sentence queue. */
  onStreamComplete?: () => void
  /** Called when the backend emits a `title` SSE event after the
   *  first turn of a fresh conversation. */
  onTitle?: (conversationId: string, title: string) => void
  /** When set to the current conversationId, suppresses the hydration
   *  fetch ONCE — the next conversationId change does fetch as usual.
   *  Used to avoid clobbering optimistic messages immediately after a
   *  brand-new conversation is auto-created on send. */
  skipHydrationFor?: string | null
  /** Fires after the suppressed hydration so the parent can reset
   *  ``skipHydrationFor`` to null. */
  onHydrationConsumed?: () => void
}

/** Find the index just past the last sentence terminator in `text`,
 *  or -1 if none. Handles both ASCII and full-width punctuation, and
 *  ignores common abbreviations like "e.g." / "Mr." that would
 *  otherwise split mid-thought. */
function findLastSentenceBreak(text: string): number {
  const re = /[.!?。！？\n](?:["')\]]+)?(\s|$)/g
  let last = -1
  for (;;) {
    const m = re.exec(text)
    if (!m) break
    const end = m.index + m[0].length
    // Reject obvious abbreviations: "e.g.", "Mr.", "Dr.", "p.5"
    const before = text.slice(Math.max(0, m.index - 4), m.index + 1)
    if (/(?:^|\s)(?:e\.g|i\.e|Mr|Mrs|Ms|Dr|Jr|Sr|St|Fig|p|pp)\.$/i.test(before))
      continue
    last = end
  }
  return last
}

export function useChat({
  conversationId,
  sessionId = "default",
  scope,
  onDone,
  onSentence,
  onStreamComplete,
  onTitle,
  skipHydrationFor,
  onHydrationConsumed,
}: UseChatOptions) {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [streaming, setStreaming] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const abortRef = useRef<AbortController | null>(null)

  // When the active conversation id changes, fetch its persisted messages
  // and replace the local state so switching conversations in the sidebar
  // restores the right history. null/undefined → empty pane.
  //
  // We track which id we've already settled (either fetched or
  // explicitly skipped) in a ref so the effect re-running because
  // skipHydrationFor flipped to null doesn't re-fetch and clobber
  // the in-flight optimistic send.
  const settledConvIdRef = useRef<string | null>(null)
  useEffect(() => {
    // Already settled — typical on re-renders where skipHydrationFor
    // flipped back to null after the auto-create handshake. Bail out
    // BEFORE touching abortRef / streaming state, otherwise we'd
    // cancel the in-flight optimistic fetch that send() just started.
    if (conversationId && settledConvIdRef.current === conversationId) {
      return
    }
    // Auto-create-on-send: parent just minted this conv ID and send()
    // is already streaming the optimistic user/assistant pair into it.
    // Don't abort, don't reset streaming, don't wipe messages — just
    // mark settled and let the in-flight fetch run to completion.
    if (
      conversationId &&
      skipHydrationFor &&
      skipHydrationFor === conversationId
    ) {
      settledConvIdRef.current = conversationId
      onHydrationConsumed?.()
      return
    }
    // From here down: genuine switch or initial mount with persisted id.
    let cancelled = false
    abortRef.current?.abort()
    abortRef.current = null
    if (!conversationId) {
      setMessages([])
      setError(null)
      setStreaming(false)
      settledConvIdRef.current = null
      return
    }
    setError(null)
    setStreaming(false)
    settledConvIdRef.current = conversationId
    conversationApi
      .get(conversationId)
      .then((conv) => {
        if (cancelled) return
        setMessages(
          conv.messages.map((m, i) => ({
            id: `${conversationId}-${i}`,
            role: m.role as "user" | "assistant",
            content: m.content,
          })),
        )
      })
      .catch((err) => {
        if (cancelled) return
        setError(err instanceof Error ? err.message : String(err))
      })
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conversationId, skipHydrationFor])

  const send = useCallback(
    async (
      question: string,
      opts?: { conversationId?: string | null },
    ) => {
      const trimmed = question.trim()
      if (!trimmed || streaming) return
      // Caller can override conversationId for the fresh-conv case where
      // setState hasn't propagated yet — we need to send to the just-
      // created id, not the one captured in this closure.
      const convIdForSend = opts?.conversationId ?? conversationId

      const userMsg: ChatMessage = {
        id: `u-${Date.now()}`,
        role: "user",
        content: trimmed,
      }
      const aiId = `a-${Date.now()}`
      const aiMsg: ChatMessage = {
        id: aiId,
        role: "assistant",
        content: "",
        streaming: true,
      }
      setMessages((m) => [...m, userMsg, aiMsg])
      setStreaming(true)
      setError(null)

      // Sentence-buffer for streaming TTS: accumulate raw tokens and
      // emit completed sentences to the caller. We track the byte
      // offset of what we've already emitted so we don't double-feed.
      let ttsBuf = ""
      let ttsEmitted = 0
      const flushSentences = (final = false) => {
        if (!onSentence) return
        const remaining = ttsBuf.slice(ttsEmitted)
        const breakAt = findLastSentenceBreak(remaining)
        if (breakAt > 0) {
          const ready = remaining.slice(0, breakAt).trim()
          if (ready) onSentence(ready)
          ttsEmitted += breakAt
        }
        if (final) {
          const tail = remaining.slice(breakAt > 0 ? breakAt : 0).trim()
          if (tail) onSentence(tail)
          ttsEmitted = ttsBuf.length
        }
      }

      const ac = new AbortController()
      abortRef.current = ac
      try {
        const resp = await fetch("/api/chat", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            question: trimmed,
            conversation_id: convIdForSend ?? null,
            session_id: sessionId,
            filenames: scope && scope.length > 0 ? scope : null,
          }),
          signal: ac.signal,
        })
        if (!resp.ok) throw new Error(`/api/chat -> ${resp.status}`)

        for await (const ev of parseSSE(resp, ac.signal)) {
          if (ev.event === "token") {
            const text = (ev.data as { text: string }).text
            ttsBuf += text
            flushSentences(false)
            setMessages((m) =>
              m.map((msg) =>
                msg.id === aiId ? { ...msg, content: msg.content + text } : msg,
              ),
            )
          } else if (ev.event === "standalone") {
            const q = (ev.data as { query: string }).query
            setMessages((m) =>
              m.map((msg) =>
                msg.id === aiId ? { ...msg, standaloneQuery: q } : msg,
              ),
            )
          } else if (ev.event === "sources") {
            const docs = (ev.data as { docs: SourceDoc[] }).docs
            setMessages((m) =>
              m.map((msg) =>
                msg.id === aiId ? { ...msg, sources: docs } : msg,
              ),
            )
          } else if (ev.event === "done") {
            const done = ev.data as DoneEvent
            // Drain any leftover sentence fragment that didn't end with
            // punctuation (e.g. last token was "...feedback").
            flushSentences(true)
            onStreamComplete?.()
            const finalMsg: ChatMessage = {
              ...aiMsg,
              content: done.answer || "",
              streaming: false,
              chitchat: done.chitchat,
              sources: done.source_documents,
              standaloneQuery: done.standalone_question,
            }
            setMessages((m) =>
              m.map((msg) => (msg.id === aiId ? finalMsg : msg)),
            )
            onDone?.(finalMsg)
          } else if (ev.event === "title") {
            const tev = ev.data as { conversation_id: string; title: string }
            onTitle?.(tev.conversation_id, tev.title)
          } else if (ev.event === "error") {
            const errMsg =
              (ev.data as { message?: string }).message || "stream error"
            throw new Error(errMsg)
          }
        }
      } catch (err) {
        if ((err as Error).name === "AbortError") return
        const msg = err instanceof Error ? err.message : String(err)
        setError(msg)
        setMessages((m) =>
          m.map((msgIt) =>
            msgIt.id === aiId
              ? { ...msgIt, streaming: false, content: msgIt.content || `❌ ${msg}` }
              : msgIt,
          ),
        )
      } finally {
        setStreaming(false)
        abortRef.current = null
      }
    },
    [conversationId, sessionId, scope, streaming, onDone, onSentence, onStreamComplete, onTitle],
  )

  const stop = useCallback(() => {
    abortRef.current?.abort()
    abortRef.current = null
    setStreaming(false)
    setMessages((m) =>
      m.map((msg) => (msg.streaming ? { ...msg, streaming: false } : msg)),
    )
  }, [])

  const clear = useCallback(async () => {
    stop()
    setMessages([])
    setError(null)
    try {
      // Clear the chain's in-memory chat history for whichever key
      // this conversation uses on the server.
      await apiClearChat(conversationId ?? sessionId)
    } catch {
      /* ignore — server-side memory will be reset on next ask anyway */
    }
  }, [conversationId, sessionId, stop])

  /** Patch a single field on a message — used by voice mode to attach the
   *  TTS audio URL to the assistant message after it streams in. */
  const patchMessage = useCallback(
    (id: string, audioUrl: string) => {
      setMessages((m) =>
        m.map((msg) => (msg.id === id ? { ...msg, audioUrl } : msg)),
      )
    },
    [],
  )

  return { messages, send, stop, clear, streaming, error, patchMessage }
}
