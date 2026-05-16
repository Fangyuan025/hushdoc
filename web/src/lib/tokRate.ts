/**
 * v0.6.2 — tok/s publisher used by the resource panel.
 *
 * useChat dispatches one event per token chunk that lands from the
 * SSE stream. ResourcePanel listens, keeps a rolling window of recent
 * timestamps + token counts, and renders the average over the last
 * ~3 seconds (smooths out network jitter without lagging too far
 * behind the actual stream).
 *
 * Event-driven instead of React-state-shared because the producer
 * (useChat inside ChatPane) and the consumer (ResourcePanel in App)
 * sit in independent subtrees -- prop drilling would touch half the
 * component tree and a context provider would re-render the entire
 * shell on every token. A bare `window.dispatchEvent` is the lightest
 * thing that works.
 */

const EVENT_NAME = "hushdoc:tok"

interface TokDetail {
  /** How many characters arrived in this chunk. We track characters
   *  not "tokens" because the SSE stream gives us text deltas; the
   *  approximation is close enough for a panel readout (Qwen3
   *  tokenisers average ~4 chars/token in English, ~1.5-2 chars/
   *  token in Chinese — calibrating to either is impressionistic
   *  anyway). The label says "ch/s" not "tok/s" to be honest. */
  chars: number
}

export function emitTokens(chars: number) {
  if (chars <= 0) return
  try {
    window.dispatchEvent(
      new CustomEvent<TokDetail>(EVENT_NAME, { detail: { chars } }),
    )
  } catch {
    /* SSR or restricted env — ignore */
  }
}

export function onTokens(handler: (chars: number) => void): () => void {
  const wrapped = (e: Event) => {
    const d = (e as CustomEvent<TokDetail>).detail
    if (d) handler(d.chars)
  }
  window.addEventListener(EVENT_NAME, wrapped)
  return () => window.removeEventListener(EVENT_NAME, wrapped)
}
