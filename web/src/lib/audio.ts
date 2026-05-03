/**
 * Tiny browser-side audio helpers.
 *
 * The backend Whisper pipeline accepts WAV via soundfile + an in-process
 * scipy resample to 16 kHz. The browser MediaRecorder defaults to webm/opus
 * which soundfile can't decode, so we re-encode the recording client-side
 * to mono 16-bit PCM WAV before POSTing it. ~30 lines, no extra dep.
 */

export function audioBufferToWavBlob(buffer: AudioBuffer): Blob {
  const numCh = buffer.numberOfChannels
  const sr = buffer.sampleRate
  const samples = buffer.length

  // Down-mix to mono.
  const mono = new Float32Array(samples)
  for (let c = 0; c < numCh; c++) {
    const data = buffer.getChannelData(c)
    for (let i = 0; i < samples; i++) mono[i] += data[i] / numCh
  }

  const bytes = new ArrayBuffer(44 + samples * 2)
  const view = new DataView(bytes)
  const writeStr = (offset: number, s: string) => {
    for (let i = 0; i < s.length; i++) view.setUint8(offset + i, s.charCodeAt(i))
  }
  // RIFF header
  writeStr(0, "RIFF")
  view.setUint32(4, 36 + samples * 2, true)
  writeStr(8, "WAVE")
  writeStr(12, "fmt ")
  view.setUint32(16, 16, true)
  view.setUint16(20, 1, true) // PCM format
  view.setUint16(22, 1, true) // mono
  view.setUint32(24, sr, true)
  view.setUint32(28, sr * 2, true) // byte rate
  view.setUint16(32, 2, true) // block align
  view.setUint16(34, 16, true) // bits per sample
  writeStr(36, "data")
  view.setUint32(40, samples * 2, true)
  // PCM samples
  let offset = 44
  for (let i = 0; i < samples; i++) {
    const s = Math.max(-1, Math.min(1, mono[i]))
    view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7fff, true)
    offset += 2
  }
  return new Blob([bytes], { type: "audio/wav" })
}

/** True if the text contains a meaningful share of CJK characters. Mirrors
 *  the server's detect_language() so the client can decide whether to
 *  request TTS at all (Kokoro-82M is English only). */
export function isEnglish(text: string): boolean {
  if (!text) return true
  let cjk = 0
  let nonspace = 0
  for (const ch of text) {
    if (!/\s/.test(ch)) nonspace += 1
    const code = ch.codePointAt(0) ?? 0
    if (
      (code >= 0x4e00 && code <= 0x9fff) || // CJK Unified Ideographs
      (code >= 0x3400 && code <= 0x4dbf) // CJK Extension A
    ) {
      cjk += 1
    }
  }
  if (cjk >= 2) return false
  if (nonspace > 0 && cjk / nonspace >= 0.3) return false
  return true
}
