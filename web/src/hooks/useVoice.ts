import { useCallback, useEffect, useRef, useState } from "react"
import { toast } from "sonner"

import { apiSynthesize, apiTranscribe } from "@/lib/api"
import { isEnglish } from "@/lib/audio"
import { recordWithVAD } from "@/lib/vad"

const STORAGE_KEY = "hushdoc-voice-mode"

export type VoiceState = "idle" | "recording" | "processing"

/**
 * Top-level voice-mode coordination.
 *
 * `enabled` is the user-controlled toggle (off by default, persisted in
 * sessionStorage so it survives a soft reload).
 *
 * `record()` opens the mic, runs the VAD loop, posts the resulting WAV to
 * /api/voice/transcribe, and returns the recognised English text. The
 * caller (ChatInput) is expected to send that text as the next chat turn
 * immediately so the user "talks then waits" instead of "talks then
 * hits send".
 *
 * `synthesizeAndPlay(text)` posts the assistant answer to
 * /api/voice/synthesize and pipes the WAV into a hidden global <audio>
 * element so the answer auto-plays without any visible progress bar.
 * Returns the blob URL so the message can cache it for replay.
 */
export function useVoice() {
  const [enabled, setEnabled] = useState<boolean>(() => {
    try {
      return sessionStorage.getItem(STORAGE_KEY) === "true"
    } catch {
      return false
    }
  })
  useEffect(() => {
    try {
      sessionStorage.setItem(STORAGE_KEY, String(enabled))
    } catch {
      /* ignore */
    }
  }, [enabled])

  const [state, setState] = useState<VoiceState>("idle")
  const [error, setError] = useState<string | null>(null)
  const [level, setLevel] = useState(0) // last RMS for the mic-pulse meter
  const abortRef = useRef<AbortController | null>(null)

  const cancel = useCallback(() => {
    abortRef.current?.abort()
    abortRef.current = null
    setState("idle")
    setLevel(0)
  }, [])

  const record = useCallback(async (): Promise<string | null> => {
    if (state !== "idle") return null
    setError(null)
    setState("recording")
    setLevel(0)
    const ac = new AbortController()
    abortRef.current = ac
    try {
      const wav = await recordWithVAD(ac.signal, {
        onLevel: (rms) => setLevel(rms),
      })
      setState("processing")
      const text = await apiTranscribe(wav)
      return text || null
    } catch (err) {
      if ((err as Error).name === "AbortError") return null
      const msg = err instanceof Error ? err.message : String(err)
      setError(msg)
      // Common case: user denied mic permission. Surface a friendlier note.
      if (/permission|denied|NotAllowed/i.test(msg)) {
        toast.error(
          "Microphone access denied. Allow it in your browser to use voice mode.",
        )
      } else if (/no speech/i.test(msg)) {
        toast.warning("Didn't hear anything — try again?")
      } else {
        toast.error(`Voice input failed: ${msg}`)
      }
      return null
    } finally {
      setState("idle")
      setLevel(0)
      abortRef.current = null
    }
  }, [state])

  // Hidden audio element used for autoplay. Created once, reused.
  const playerRef = useRef<HTMLAudioElement | null>(null)
  const [isPlaying, setIsPlaying] = useState(false)
  if (typeof document !== "undefined" && !playerRef.current) {
    const el = document.createElement("audio")
    el.style.display = "none"
    document.body.appendChild(el)
    playerRef.current = el
  }
  // Wire <audio> events to React state so the UI can show pause/play.
  useEffect(() => {
    const el = playerRef.current
    if (!el) return
    const onPlay = () => setIsPlaying(true)
    const onPauseOrEnd = () => setIsPlaying(false)
    el.addEventListener("play", onPlay)
    el.addEventListener("playing", onPlay)
    el.addEventListener("pause", onPauseOrEnd)
    el.addEventListener("ended", onPauseOrEnd)
    el.addEventListener("emptied", onPauseOrEnd)
    return () => {
      el.removeEventListener("play", onPlay)
      el.removeEventListener("playing", onPlay)
      el.removeEventListener("pause", onPauseOrEnd)
      el.removeEventListener("ended", onPauseOrEnd)
      el.removeEventListener("emptied", onPauseOrEnd)
    }
  }, [])

  const playUrl = useCallback((url: string) => {
    const el = playerRef.current
    if (!el) return
    el.src = url
    el.currentTime = 0
    void el.play().catch(() => undefined)
  }, [])

  /** Stop playback and clear the source. Used when the user starts a new
   *  chat or hits the global cancel hotkey. */
  const stopPlayback = useCallback(() => {
    const el = playerRef.current
    if (!el) return
    el.pause()
    el.removeAttribute("src")
    el.currentTime = 0
    el.load()  // forces "emptied" event so isPlaying flips to false
  }, [])

  /** Toggle pause/resume on the current audio. No-op if nothing is loaded. */
  const togglePause = useCallback(() => {
    const el = playerRef.current
    if (!el || !el.src) return
    if (el.paused) void el.play().catch(() => undefined)
    else el.pause()
  }, [])

  const synthesizeAndPlay = useCallback(
    async (text: string): Promise<string | null> => {
      if (!enabled || !text || !isEnglish(text)) return null
      try {
        const wav = await apiSynthesize(text)
        const url = URL.createObjectURL(wav)
        playUrl(url)
        return url
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err)
        setError(msg)
        toast.error(`TTS failed: ${msg}`)
        return null
      }
    },
    [enabled, playUrl],
  )

  // ----------------------------------------------------------------------
  // Streaming TTS: synth + play one sentence at a time as tokens arrive.
  //
  // Streamlit-era we waited for the full answer to land before kicking
  // off TTS, which left a 5-15s gap of dead air. Now we maintain a
  // queue: feed() pushes a sentence to a synth-and-enqueue worker,
  // and as each WAV finishes synth we chain it into the same hidden
  // <audio> via 'ended' so playback stays continuous. End-of-stream
  // is signalled with a sentinel so the queue can drain cleanly.
  // ----------------------------------------------------------------------
  type Job = { text: string } | "DONE"
  const queueRef = useRef<Job[]>([])
  const jobUrlsRef = useRef<string[]>([])
  const drainingRef = useRef<boolean>(false)
  const streamingActiveRef = useRef<boolean>(false)

  const drainQueue = useCallback(async () => {
    if (drainingRef.current) return
    drainingRef.current = true
    const el = playerRef.current
    // Snapshot the generation so a cancel() raced against an in-flight
    // synth aborts cleanly rather than playing a stale clip.
    while (streamingActiveRef.current) {
      const next = queueRef.current.shift()
      if (!next) {
        drainingRef.current = false
        return
      }
      if (next === "DONE") {
        streamingActiveRef.current = false
        drainingRef.current = false
        return
      }
      try {
        const wav = await apiSynthesize(next.text)
        if (!streamingActiveRef.current) break // cancelled during synth
        const url = URL.createObjectURL(wav)
        jobUrlsRef.current.push(url)
        if (el) {
          // Wait for the previous clip to finish before starting this one.
          if (!el.paused && el.src) {
            await new Promise<void>((resolve) => {
              const onDone = () => {
                el.removeEventListener("ended", onDone)
                el.removeEventListener("emptied", onDone)
                resolve()
              }
              el.addEventListener("ended", onDone, { once: true })
              el.addEventListener("emptied", onDone, { once: true })
            })
          }
          if (!streamingActiveRef.current) break // cancelled mid-wait
          el.src = url
          el.currentTime = 0
          await el.play().catch(() => undefined)
        }
      } catch (err) {
        // One sentence failing shouldn't kill the whole stream.
        const msg = err instanceof Error ? err.message : String(err)
        toast.error(`TTS chunk failed: ${msg}`)
      }
    }
    drainingRef.current = false
  }, [])

  /** Push one sentence into the streaming-TTS queue. Skips silently when
   *  voice mode is off or the sentence isn't English. */
  const feedStreamingTTS = useCallback(
    (sentence: string) => {
      if (!enabled) return
      const t = sentence.trim()
      if (!t || !isEnglish(t)) return
      queueRef.current.push({ text: t })
      streamingActiveRef.current = true
      void drainQueue()
    },
    [enabled, drainQueue],
  )

  /** Mark the streaming TTS stream as complete. Plays whatever's left in
   *  the queue then stops the worker. */
  const finishStreamingTTS = useCallback(() => {
    if (!streamingActiveRef.current) return
    queueRef.current.push("DONE")
    void drainQueue()
  }, [drainQueue])

  /** Drop everything queued (used by stopPlayback / new-chat / ESC). */
  const cancelStreamingTTS = useCallback(() => {
    queueRef.current.length = 0
    streamingActiveRef.current = false
    drainingRef.current = false
    for (const u of jobUrlsRef.current) URL.revokeObjectURL(u)
    jobUrlsRef.current = []
  }, [])

  // Wire the streaming-TTS queue cancel into the existing stopPlayback
  // so a single call drops both: any queued sentences AND the currently
  // playing clip.
  const stopPlaybackEverything = useCallback(() => {
    cancelStreamingTTS()
    stopPlayback()
  }, [cancelStreamingTTS, stopPlayback])

  return {
    enabled,
    isPlaying,
    stopPlayback: stopPlaybackEverything,
    togglePause,
    setEnabled,
    state,
    level,
    error,
    record,
    cancel,
    synthesizeAndPlay,
    playUrl,
    feedStreamingTTS,
    finishStreamingTTS,
    cancelStreamingTTS,
  }
}
