import { audioBufferToWavBlob } from "./audio"

export interface VadOptions {
  /** RMS threshold (0..1) above which a frame counts as speech. */
  thresholdRms?: number
  /** Sustained silence duration in ms after speech that ends the recording. */
  silenceMs?: number
  /** Hard upper bound for the recording in ms. */
  maxMs?: number
  /** Called continuously during recording with the current normalized RMS
   *  level so the UI can render a meter. */
  onLevel?: (rms: number, speaking: boolean) => void
}

/**
 * Capture audio from the default microphone, automatically stop after a
 * sustained pause, and return the recording as a 16-bit mono WAV blob
 * ready to POST to /api/voice/transcribe.
 *
 * Behaviour:
 *   1. Open the mic via getUserMedia.
 *   2. Record raw bytes via MediaRecorder (browser-default codec).
 *   3. In parallel, hook AnalyserNode and compute frame RMS.
 *   4. Once speech is detected (RMS > threshold), watch for a sustained
 *      window of silence longer than `silenceMs` and stop. If no speech
 *      ever arrives, stop after `maxMs` so dead recordings don't hang.
 *   5. After stop, decode the captured blob in a fresh AudioContext and
 *      re-encode as PCM WAV.
 *
 * Returns a Promise that resolves with the WAV blob, or rejects if the
 * caller invoked the abort signal before the recording could finish.
 */
export async function recordWithVAD(
  signal: AbortSignal,
  opts: VadOptions = {},
): Promise<Blob> {
  const thresholdRms = opts.thresholdRms ?? 0.012
  const silenceMs = opts.silenceMs ?? 1500
  const maxMs = opts.maxMs ?? 30_000
  const onLevel = opts.onLevel

  const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
  const audioCtx: AudioContext = new (window.AudioContext ||
    // @ts-expect-error - webkit fallback
    window.webkitAudioContext)()
  const source = audioCtx.createMediaStreamSource(stream)
  const analyser = audioCtx.createAnalyser()
  analyser.fftSize = 1024
  source.connect(analyser)

  const recorder = new MediaRecorder(stream)
  const chunks: Blob[] = []
  recorder.ondataavailable = (e) => {
    if (e.data.size > 0) chunks.push(e.data)
  }
  recorder.start(250)

  const cleanup = () => {
    try {
      recorder.state !== "inactive" && recorder.stop()
    } catch {
      /* ignore */
    }
    stream.getTracks().forEach((t) => t.stop())
    void audioCtx.close()
  }

  // Abort handling.
  const aborted: { value: boolean } = { value: false }
  const onAbort = () => {
    aborted.value = true
    cleanup()
  }
  signal.addEventListener("abort", onAbort, { once: true })

  // VAD loop.
  const buf = new Uint8Array(analyser.frequencyBinCount)
  let started = performance.now()
  let speakingDetected = false
  let silenceStartedAt = 0

  const tick = () => {
    if (aborted.value || recorder.state === "inactive") return
    analyser.getByteTimeDomainData(buf)
    let sum = 0
    for (const v of buf) {
      const n = (v - 128) / 128
      sum += n * n
    }
    const rms = Math.sqrt(sum / buf.length)
    const now = performance.now()

    const isSpeaking = rms > thresholdRms
    onLevel?.(rms, isSpeaking)

    if (isSpeaking) {
      speakingDetected = true
      silenceStartedAt = 0
    } else if (speakingDetected) {
      if (silenceStartedAt === 0) silenceStartedAt = now
      if (now - silenceStartedAt > silenceMs) {
        recorder.stop()
        return
      }
    }

    if (now - started > maxMs) {
      recorder.stop()
      return
    }

    requestAnimationFrame(tick)
  }
  requestAnimationFrame(tick)

  // Wait for stop, then decode + re-encode.
  return new Promise<Blob>((resolve, reject) => {
    recorder.onstop = async () => {
      signal.removeEventListener("abort", onAbort)
      stream.getTracks().forEach((t) => t.stop())

      if (aborted.value) {
        reject(new DOMException("aborted", "AbortError"))
        return
      }
      if (chunks.length === 0 || !speakingDetected) {
        await audioCtx.close().catch(() => undefined)
        reject(new Error("no speech detected"))
        return
      }
      try {
        const recordedBlob = new Blob(chunks, { type: recorder.mimeType })
        const arrayBuffer = await recordedBlob.arrayBuffer()
        // Use a fresh AudioContext for decoding because the live one is
        // about to be closed.
        const decoder: AudioContext = new (window.AudioContext ||
          // @ts-expect-error - webkit fallback
          window.webkitAudioContext)()
        const buffer = await decoder.decodeAudioData(arrayBuffer)
        await decoder.close().catch(() => undefined)
        await audioCtx.close().catch(() => undefined)
        resolve(audioBufferToWavBlob(buffer))
      } catch (err) {
        reject(err as Error)
      }
    }
    recorder.onerror = (e) => {
      cleanup()
      reject(new Error(`MediaRecorder error: ${(e as Event).type}`))
    }
  })
}
