import { useCallback, useRef, useState } from "react"
import { apiClearChat } from "@/lib/api"
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
  sessionId: string
  scope?: string[] | null
  onDone?: (msg: ChatMessage) => void
}

export function useChat({ sessionId, scope, onDone }: UseChatOptions) {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [streaming, setStreaming] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const abortRef = useRef<AbortController | null>(null)

  const send = useCallback(
    async (question: string) => {
      const trimmed = question.trim()
      if (!trimmed || streaming) return

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

      const ac = new AbortController()
      abortRef.current = ac
      try {
        const resp = await fetch("/api/chat", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            question: trimmed,
            session_id: sessionId,
            filenames: scope && scope.length > 0 ? scope : null,
          }),
          signal: ac.signal,
        })
        if (!resp.ok) throw new Error(`/api/chat -> ${resp.status}`)

        for await (const ev of parseSSE(resp, ac.signal)) {
          if (ev.event === "token") {
            const text = (ev.data as { text: string }).text
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
    [sessionId, scope, streaming, onDone],
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
      await apiClearChat(sessionId)
    } catch {
      /* ignore — server-side memory will be reset on next ask anyway */
    }
  }, [sessionId, stop])

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
