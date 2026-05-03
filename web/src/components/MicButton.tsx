import { Loader2, Mic, Square } from "lucide-react"

import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import type { VoiceState } from "@/hooks/useVoice"

interface MicButtonProps {
  state: VoiceState
  level: number // 0..1
  onStart: () => void
  onCancel: () => void
  disabled?: boolean
}

/**
 * Mic toggle that drives the voice-input path.
 *   idle       → mic icon, neutral
 *   recording  → red pulsing ring (intensity follows RMS), click cancels
 *   processing → spinner
 */
export function MicButton({
  state,
  level,
  onStart,
  onCancel,
  disabled,
}: MicButtonProps) {
  const isRecording = state === "recording"
  const isProcessing = state === "processing"

  return (
    <div className="relative shrink-0">
      {/* Pulse ring while recording */}
      {isRecording && (
        <span
          className="pointer-events-none absolute inset-0 rounded-md bg-destructive/30"
          style={{
            animation: "hushdoc-mic-pulse 1.2s ease-out infinite",
            transform: `scale(${1 + Math.min(0.6, level * 8)})`,
            transition: "transform 80ms linear",
          }}
        />
      )}
      <Button
        type="button"
        variant={isRecording ? "destructive" : "ghost"}
        size="icon"
        disabled={disabled || isProcessing}
        onClick={isRecording ? onCancel : onStart}
        title={
          isProcessing
            ? "Transcribing…"
            : isRecording
              ? "Stop / cancel"
              : "Speak (English only)"
        }
        className={cn(
          "relative",
          isRecording && "bg-destructive text-destructive-foreground",
        )}
      >
        {isProcessing ? (
          <Loader2 className="h-4 w-4 animate-spin" />
        ) : isRecording ? (
          <Square className="h-3.5 w-3.5 fill-current" />
        ) : (
          <Mic className="h-4 w-4" />
        )}
      </Button>
    </div>
  )
}
