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
import { useEffect, useMemo, useRef, useState } from "react"
import {
  Activity,
  BookOpen,
  FileText,
  Sparkles,
  X,
} from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { useDocuments } from "@/hooks/useDocuments"
import { cn } from "@/lib/utils"
import type { RetrievalTraceEntry, SourceDoc } from "@/types"

import { PdfChunkViewer } from "./PdfChunkViewer"

interface SourcesProps {
  docs: SourceDoc[]
  standaloneQuery?: string
  trace?: RetrievalTraceEntry[]
  /** topk / topk+rerank / balanced / balanced+rerank — shown as a small
   *  badge in the trace tab so the user knows what pipeline ran. */
  mode?: string
}

export function Sources({ docs, standaloneQuery, trace, mode }: SourcesProps) {
  if ((!docs || docs.length === 0) && (!trace || trace.length === 0)) {
    return null
  }

  // Group docs by (filename, page) for the inline chip row.
  const chips = useMemo(() => {
    const seen = new Set<string>()
    return (docs || [])
      .map((d) => ({ key: `${d.filename}#${d.page ?? "?"}`, doc: d }))
      .filter(({ key }) =>
        seen.has(key) ? false : (seen.add(key), true),
      )
  }, [docs])

  // v0.5.0: which filenames have an on-disk raw copy the viewer can
  // open. Pulled from the same /api/documents cache the Library panel
  // uses, so no extra roundtrip per message. Files ingested before
  // raw retention (or typed/pasted items) are absent and the citation
  // chip stays a snippet-only quick-peek.
  const docList = useDocuments().list
  const filenamesWithRaw = useMemo(() => {
    const set = new Set<string>()
    for (const f of docList.data?.files ?? []) {
      if (f.has_raw) set.add(f.filename)
    }
    return set
  }, [docList.data])
  const hasRaw = (filename: string) => filenamesWithRaw.has(filename)

  // Drawer state — open / which tab / which chunk to focus.
  const [open, setOpen] = useState(false)
  const [tab, setTab] = useState<"sources" | "trace">("sources")
  const [focusKey, setFocusKey] = useState<string | null>(null)

  // v0.5.0 viewer modal state. Independent of the drawer so the user
  // can have both open at once -- drawer on the right with the chunk
  // list, modal in the center with the rendered PDF page. Closing the
  // viewer leaves the drawer right where it was.
  const [viewing, setViewing] = useState<SourceDoc | null>(null)
  const openViewer = (doc: SourceDoc) => setViewing(doc)
  const closeViewer = () => setViewing(null)

  return (
    <div className="mt-2 space-y-1.5">
      {/* Chip row */}
      <div className="flex flex-wrap items-center gap-1">
        {chips.map(({ key, doc }) => (
          <button
            key={key}
            type="button"
            onClick={() => {
              setTab("sources")
              setFocusKey(key)
              setOpen(true)
            }}
            className="group"
          >
            <Badge
              variant="muted"
              className="font-mono text-[10px] transition-colors group-hover:bg-primary/10 group-hover:text-foreground"
            >
              <FileText className="mr-1 h-2.5 w-2.5" />
              {doc.filename} · p.{doc.page ?? "?"}
            </Badge>
          </button>
        ))}
        {trace && trace.length > 0 && (
          <button
            type="button"
            onClick={() => {
              setTab("trace")
              setOpen(true)
            }}
            className="ml-auto flex items-center gap-1 text-[10px] uppercase tracking-wide text-muted-foreground/70 hover:text-foreground"
            title="See the full retrieval trace"
          >
            <Activity className="h-3 w-3" />
            trace ({trace.length})
          </button>
        )}
      </div>

      {open && (
        <SourcesDrawer
          docs={docs}
          standaloneQuery={standaloneQuery}
          trace={trace}
          mode={mode}
          initialTab={tab}
          focusKey={focusKey}
          hasRaw={hasRaw}
          onOpenViewer={openViewer}
          onClose={() => {
            setOpen(false)
            setFocusKey(null)
          }}
        />
      )}
      {viewing && (
        <PdfChunkViewer
          filename={viewing.filename}
          initialPage={viewing.page ?? 1}
          chunkText={viewing.snippet}
          onClose={closeViewer}
        />
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Drawer
// ---------------------------------------------------------------------------

function SourcesDrawer({
  docs,
  standaloneQuery,
  trace,
  mode,
  initialTab,
  focusKey,
  hasRaw,
  onOpenViewer,
  onClose,
}: {
  docs: SourceDoc[]
  standaloneQuery?: string
  trace?: RetrievalTraceEntry[]
  mode?: string
  initialTab: "sources" | "trace"
  focusKey: string | null
  hasRaw: (filename: string) => boolean
  onOpenViewer: (doc: SourceDoc) => void
  onClose: () => void
}) {
  const [tab, setTab] = useState(initialTab)
  useEffect(() => setTab(initialTab), [initialTab])

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
          <div className="text-sm font-medium">Sources &amp; retrieval</div>
          <button
            type="button"
            onClick={onClose}
            className="ml-auto rounded p-1 text-muted-foreground hover:bg-accent"
            title="Close (Esc)"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="flex border-b px-1 text-xs">
          <TabBtn
            active={tab === "sources"}
            onClick={() => setTab("sources")}
            badge={docs.length}
          >
            Sources
          </TabBtn>
          <TabBtn
            active={tab === "trace"}
            onClick={() => setTab("trace")}
            badge={trace?.length}
            disabled={!trace || trace.length === 0}
          >
            Retrieval
          </TabBtn>
          {mode && (
            <span className="ml-auto self-center pr-2 font-mono text-[10px] text-muted-foreground/70">
              {mode}
            </span>
          )}
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto p-3">
          {tab === "sources" ? (
            <SourcesTab
              docs={docs}
              standaloneQuery={standaloneQuery}
              focusKey={focusKey}
              hasRaw={hasRaw}
              onOpenViewer={onOpenViewer}
            />
          ) : (
            <TraceTab trace={trace || []} />
          )}
        </div>
      </aside>
    </div>
  )
}

function TabBtn({
  active,
  badge,
  disabled,
  onClick,
  children,
}: {
  active: boolean
  badge?: number
  disabled?: boolean
  onClick: () => void
  children: React.ReactNode
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={cn(
        "border-b-2 px-3 py-2 text-xs transition-colors",
        active
          ? "border-primary text-foreground"
          : "border-transparent text-muted-foreground hover:text-foreground",
        disabled && "cursor-not-allowed opacity-40 hover:text-muted-foreground",
      )}
    >
      {children}
      {typeof badge === "number" && badge > 0 && (
        <span className="ml-1 rounded bg-muted px-1 py-0.5 font-mono text-[10px] text-muted-foreground">
          {badge}
        </span>
      )}
    </button>
  )
}

// ---------------------------------------------------------------------------

function SourcesTab({
  docs,
  standaloneQuery,
  focusKey,
  hasRaw,
  onOpenViewer,
}: {
  docs: SourceDoc[]
  standaloneQuery?: string
  focusKey: string | null
  hasRaw: (filename: string) => boolean
  onOpenViewer: (doc: SourceDoc) => void
}) {
  const focusRef = useRef<HTMLDivElement | null>(null)
  useEffect(() => {
    if (focusRef.current) {
      focusRef.current.scrollIntoView({
        behavior: "smooth",
        block: "center",
      })
    }
  }, [focusKey])

  if (!docs || docs.length === 0) {
    return (
      <p className="text-xs text-muted-foreground">
        No chunks were cited inline in the answer. See the Retrieval tab
        for the full candidate set.
      </p>
    )
  }

  return (
    <div className="space-y-2">
      {standaloneQuery && (
        <div className="rounded border border-border/50 bg-muted/40 px-2.5 py-1.5 text-[11px]">
          <div className="text-[10px] uppercase tracking-wide text-muted-foreground/70">
            Standalone query
          </div>
          <div className="font-mono">{standaloneQuery}</div>
        </div>
      )}
      {docs.map((d, i) => {
        const key = `${d.filename}#${d.page ?? "?"}`
        const isFocus = key === focusKey
        const viewerOk = hasRaw(d.filename)
        return (
          <div
            key={i}
            ref={isFocus ? focusRef : undefined}
            className={cn(
              "rounded-md border bg-card/50 p-2.5 text-xs",
              isFocus && "border-primary/60 bg-primary/5",
            )}
          >
            <div className="mb-1 flex items-baseline gap-2 font-mono text-[11px]">
              <span className="font-medium">{d.filename}</span>
              <span className="text-muted-foreground">
                p.{d.page ?? "?"}
              </span>
              {d.headings && (
                <span className="truncate text-muted-foreground/70">
                  · {d.headings}
                </span>
              )}
              {viewerOk && (
                <button
                  type="button"
                  onClick={() => onOpenViewer(d)}
                  className="ml-auto inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-primary hover:bg-primary/10"
                  title="Open this page in the PDF viewer"
                >
                  <BookOpen className="h-3 w-3" />
                  open
                </button>
              )}
            </div>
            <div className="whitespace-pre-wrap text-muted-foreground">
              {d.snippet}
            </div>
          </div>
        )
      })}
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
