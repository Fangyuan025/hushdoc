/**
 * v0.6.0 — inline [N] citation chip with hover popover.
 *
 * Replaces v0.5.x's "chip row below the answer + click to open PDF
 * viewer" with a NotebookLM-style inline experience: the [N] sits
 * right in the answer text, hover lifts a popover showing the exact
 * source paragraph the answer derives that sentence from, and a
 * "View source →" button opens the full-file side panel (commit 6).
 *
 * The chip is purely presentational — popover state is local. Parent
 * passes in the paragraph data already resolved by the chain's
 * sentence-binding pass (no client-side fuzzy matching).
 */
import { useEffect, useRef, useState } from "react"
import { BookOpen, FileText } from "lucide-react"

import { cn } from "@/lib/utils"
import type { ParagraphBinding } from "@/types"

interface CitationChipProps {
  id: number
  /** Resolved paragraph binding for this id from the answer's
   *  sentence_bindings. Undefined when the answer cites an id that
   *  ended up filtered out -- should be rare after v0.6.0 sanitization,
   *  but we render a dim chip in that case rather than throwing. */
  binding?: ParagraphBinding
  /** Click handler for the "View source →" button. Receives the
   *  binding so the parent can decide which doc / page to open. */
  onOpenSource?: (binding: ParagraphBinding) => void
}

export function CitationChip({
  id,
  binding,
  onOpenSource,
}: CitationChipProps) {
  const [open, setOpen] = useState(false)
  const closeTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const cancelClose = () => {
    if (closeTimer.current) {
      clearTimeout(closeTimer.current)
      closeTimer.current = null
    }
  }
  const scheduleClose = () => {
    cancelClose()
    closeTimer.current = setTimeout(() => setOpen(false), 200)
  }
  // Cleanup on unmount.
  useEffect(() => () => cancelClose(), [])

  const unresolved = !binding

  return (
    <span
      className="relative inline-block align-baseline"
      onMouseEnter={() => {
        cancelClose()
        if (!unresolved) setOpen(true)
      }}
      onMouseLeave={scheduleClose}
    >
      <button
        type="button"
        className={cn(
          "mx-0.5 inline-flex h-[1.05em] min-w-[1.5em] items-center",
          "justify-center rounded-md px-1 align-baseline",
          "text-[0.72em] font-medium tabular-nums leading-none",
          "transition-colors",
          unresolved
            ? "border border-dashed border-muted text-muted-foreground/60"
            : "bg-primary/10 text-primary hover:bg-primary/20",
        )}
        onClick={(e) => {
          // Click toggles the popover (mobile / keyboard users); hover
          // already handles desktop. preventDefault stops markdown
          // anchor handlers from firing if the chip ever lands inside
          // a link.
          e.preventDefault()
          e.stopPropagation()
          if (!unresolved) setOpen((o) => !o)
        }}
        title={
          unresolved
            ? `Citation [${id}] not found in sources`
            : `${binding.filename} · p.${binding.page ?? "?"}`
        }
        aria-label={`Citation ${id}`}
      >
        {id}
      </button>
      {open && binding && (
        <CitationPopover
          binding={binding}
          onMouseEnter={cancelClose}
          onMouseLeave={scheduleClose}
          onOpenSource={onOpenSource}
        />
      )}
    </span>
  )
}

function CitationPopover({
  binding,
  onMouseEnter,
  onMouseLeave,
  onOpenSource,
}: {
  binding: ParagraphBinding
  onMouseEnter: () => void
  onMouseLeave: () => void
  onOpenSource?: (binding: ParagraphBinding) => void
}) {
  return (
    <div
      role="tooltip"
      className={cn(
        "absolute left-1/2 top-[calc(100%+6px)] z-30 w-[min(420px,calc(100vw-2rem))]",
        "-translate-x-1/2",
        "rounded-lg border bg-popover px-3 py-2.5 text-popover-foreground",
        "shadow-lg",
        // Subtle entrance: pdf citations are read fast, no big anim.
        "animate-in fade-in-50 zoom-in-95 duration-150",
      )}
      onMouseEnter={onMouseEnter}
      onMouseLeave={onMouseLeave}
      // Stop the bubble's click handler from firing when the user
      // selects text inside the popover for copy.
      onClick={(e) => e.stopPropagation()}
    >
      {/* Header: filename + page */}
      <div className="mb-1.5 flex items-baseline gap-1.5 font-mono text-[11px] text-muted-foreground">
        <FileText className="h-3 w-3 shrink-0 self-center" />
        <span className="truncate font-medium text-foreground">
          {binding.filename}
        </span>
        <span className="ml-auto shrink-0 whitespace-nowrap">
          p.{binding.page ?? "?"}
        </span>
      </div>
      {/* Paragraph excerpt */}
      <div
        className={cn(
          "max-h-[12rem] overflow-y-auto whitespace-pre-wrap",
          "text-[12.5px] leading-relaxed text-foreground/90",
        )}
      >
        {binding.paragraph || "(no paragraph text)"}
      </div>
      {/* Footer: View source link */}
      {onOpenSource && (
        <div className="mt-2 flex items-center justify-end border-t pt-1.5">
          <button
            type="button"
            onClick={() => onOpenSource(binding)}
            className={cn(
              "inline-flex items-center gap-1 rounded px-1.5 py-0.5",
              "text-[11px] text-primary hover:bg-primary/10",
            )}
          >
            <BookOpen className="h-3 w-3" />
            View source
          </button>
        </div>
      )}
      {/* Weak-match badge when the binding score is low -- helps the
       *  user judge "this is a strong support" vs "best we could
       *  find but probably not exact". */}
      {binding.score < 0.1 && (
        <div className="absolute right-2 top-2 rounded bg-amber-500/15 px-1 py-px text-[9px] font-medium uppercase tracking-wide text-amber-600 dark:text-amber-300">
          weak
        </div>
      )}
    </div>
  )
}
