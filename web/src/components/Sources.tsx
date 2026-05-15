/**
 * Sources surface — v0.2.0 redesign.
 *
 * Inline under the assistant message we render only the citation chips
 * (one per unique filename+page). Clicking a chip OR the "Trace" link
 * opens a right-side drawer with two tabs:
 *
 *   - Sources       — the cited chunks, snippet + page + headings.
 *                     Selecting a chip from the bubble scrolls the
 *                     matching row into view + highlights it.
 *   - Retrieval     — the full bi-encoder candidate set with
 *                     rank_before -> rank_after + cross-encoder score,
 *                     so the user can see EXACTLY which chunks the
 *                     query touched and which ones the reranker
 *                     dropped. Cited rows get a ✓ flag.
 *
 * Replaces the previous inline accordion (which crammed everything
 * directly below the bubble and got crowded fast once retrieval_trace
 * was added).
 */
import { useEffect, useState } from "react"
import { Activity, Sparkles, X } from "lucide-react"

import { cn } from "@/lib/utils"
import type { RetrievalTraceEntry, SourceDoc } from "@/types"

interface SourcesProps {
  docs: SourceDoc[]
  standaloneQuery?: string
  trace?: RetrievalTraceEntry[]
  /** topk / topk+rerank / balanced / balanced+rerank — shown as a small
   *  badge in the trace tab so the user knows what pipeline ran. */
  mode?: string
}

export function Sources({ docs: _docs, standaloneQuery, trace, mode }: SourcesProps) {
  // v0.6.0: sources are now inline as [N] chips with hover popovers;
  // the redundant chip row + Sources tab in the drawer are gone. This
  // component is now a thin "show retrieval trace" affordance for
  // debugging / power users, plus the standalone-query badge.
  if (!trace || trace.length === 0) return null
  const [open, setOpen] = useState(false)

  return (
    <div className="mt-1 flex justify-end">
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="flex items-center gap-1 text-[10px] uppercase tracking-wide text-muted-foreground/60 hover:text-foreground"
        title="See the full retrieval trace"
      >
        <Activity className="h-3 w-3" />
        trace ({trace.length})
      </button>
      {open && (
        <TraceDrawer
          standaloneQuery={standaloneQuery}
          trace={trace}
          mode={mode}
          onClose={() => setOpen(false)}
        />
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// v0.6.0 trace drawer (debug / power-user surface). The Sources tab
// is gone; the rendered answer already carries inline [N] chips with
// hover popovers, so this drawer focuses on the retrieval pipeline
// itself: candidate count, rerank ranks, channel mix, what got cited.
// ---------------------------------------------------------------------------
function TraceDrawer({
  standaloneQuery,
  trace,
  mode,
  onClose,
}: {
  standaloneQuery?: string
  trace?: RetrievalTraceEntry[]
  mode?: string
  onClose: () => void
}) {
  useEffect(() => {
    const onEsc = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose()
    }
    window.addEventListener("keydown", onEsc)
    return () => window.removeEventListener("keydown", onEsc)
  }, [onClose])

  return (
    <div
      className="fixed inset-0 z-40 flex justify-end bg-black/30"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose()
      }}
    >
      <aside className="flex h-full w-full max-w-md flex-col border-l bg-background shadow-xl">
        <div className="flex items-center gap-2 border-b px-3 py-2">
          <Sparkles className="h-4 w-4 text-primary" />
          <div className="text-sm font-medium">Retrieval trace</div>
          {mode && (
            <span className="ml-2 font-mono text-[10px] text-muted-foreground/70">
              {mode}
            </span>
          )}
          <button
            type="button"
            onClick={onClose}
            className="ml-auto rounded p-1 text-muted-foreground hover:bg-accent"
            title="Close (Esc)"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto p-3">
          {standaloneQuery && (
            <div className="mb-3 rounded border border-border/50 bg-muted/40 px-2.5 py-1.5 text-[11px]">
              <div className="text-[10px] uppercase tracking-wide text-muted-foreground/70">
                Standalone query
              </div>
              <div className="font-mono">{standaloneQuery}</div>
            </div>
          )}
          <TraceTab trace={trace || []} />
        </div>
      </aside>
    </div>
  )
}

// ---------------------------------------------------------------------------

function TraceTab({ trace }: { trace: RetrievalTraceEntry[] }) {
  if (trace.length === 0) {
    return (
      <p className="text-xs text-muted-foreground">
        No retrieval trace for this turn (the assistant either short-
        circuited to chitchat or the index was empty).
      </p>
    )
  }
  const dropped = trace.filter((t) => t.rank_after === null).length
  const cited = trace.filter((t) => t.cited).length
  return (
    <div className="space-y-2">
      <div className="rounded border border-border/50 bg-muted/40 px-2.5 py-1.5 text-[11px] leading-snug">
        <div className="font-medium">{trace.length} candidates</div>
        <div className="text-muted-foreground">
          {trace.length - dropped} kept after rerank
          {dropped > 0 && <> · {dropped} dropped</>}
          {cited > 0 && <> · {cited} actually cited ✓</>}
        </div>
      </div>
      <div className="space-y-1.5">
        {trace.map((e, i) => (
          <TraceRow key={i} entry={e} />
        ))}
      </div>
    </div>
  )
}

function TraceRow({ entry }: { entry: RetrievalTraceEntry }) {
  const dropped = entry.rank_after === null
  const cited = !!entry.cited
  return (
    <div
      className={cn(
        "rounded-md border px-2.5 py-1.5 text-[11px]",
        cited
          ? "border-emerald-500/40 bg-emerald-500/5"
          : dropped
            ? "border-border/40 bg-muted/20 opacity-70"
            : "border-border bg-card/50",
      )}
    >
      <div className="flex items-baseline gap-2 font-mono">
        <span className="font-medium">{entry.filename || "?"}</span>
        <span className="text-muted-foreground">
          p.{entry.page ?? "?"}
        </span>
        {entry.source && (
          <SourceChannelChip channel={entry.source} />
        )}
        <span
          className={cn(
            "ml-auto text-[10px] uppercase tracking-wide",
            cited
              ? "text-emerald-600 dark:text-emerald-400"
              : dropped
                ? "text-muted-foreground/60"
                : "text-muted-foreground",
          )}
        >
          {cited ? "cited ✓" : dropped ? "dropped" : "kept"}
        </span>
      </div>
      <div className="mt-0.5 flex items-baseline gap-2 text-[10px] text-muted-foreground">
        <span>
          rank #{entry.rank_before}
          {entry.rank_after !== null ? ` -> #${entry.rank_after}` : " -> --"}
        </span>
        {entry.score_after !== null && (
          <span>score {entry.score_after.toFixed(3)}</span>
        )}
      </div>
      {entry.snippet && (
        <div className="mt-1 whitespace-pre-wrap text-muted-foreground line-clamp-3">
          {entry.snippet}
        </div>
      )}
    </div>
  )
}

/** v0.5.0: tag the retrieval channel that surfaced this candidate.
 *  Hybrid mode produces 'dense' / 'bm25' / 'both'; the colour cue helps
 *  the user spot at a glance which channel is pulling its weight on a
 *  given query (e.g. an exact-name query should be mostly 'bm25 / both'). */
function SourceChannelChip({ channel }: { channel: string }) {
  const lower = channel.toLowerCase()
  if (!lower || lower === "dense") {
    // Most common case for dense-only modes -- skip the chip to keep
    // the row uncluttered.
    if (lower === "dense") {
      return (
        <span className="rounded bg-sky-500/15 px-1 py-px text-[9px] font-medium uppercase tracking-wide text-sky-600 dark:text-sky-300">
          dense
        </span>
      )
    }
    return null
  }
  if (lower === "bm25") {
    return (
      <span className="rounded bg-amber-500/15 px-1 py-px text-[9px] font-medium uppercase tracking-wide text-amber-600 dark:text-amber-300">
        bm25
      </span>
    )
  }
  if (lower === "both") {
    return (
      <span className="rounded bg-emerald-500/15 px-1 py-px text-[9px] font-medium uppercase tracking-wide text-emerald-600 dark:text-emerald-300">
        both
      </span>
    )
  }
  if (lower === "memory") {
    return (
      <span
        className="rounded bg-violet-500/15 px-1 py-px text-[9px] font-medium uppercase tracking-wide text-violet-600 dark:text-violet-300"
        title="Carried over from a previous turn in this conversation"
      >
        memory
      </span>
    )
  }
  return (
    <span className="rounded bg-muted px-1 py-px text-[9px] font-medium uppercase tracking-wide text-muted-foreground">
      {channel}
    </span>
  )
}
