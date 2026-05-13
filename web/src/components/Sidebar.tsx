import { useCallback, useEffect, useState } from "react"
import { BookOpen, ChevronDown, ChevronRight, MessageSquare, Mic } from "lucide-react"

import { ConversationList } from "@/components/ConversationList"
import { Library } from "@/components/Library"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Switch } from "@/components/ui/switch"
import { useDocuments } from "@/hooks/useDocuments"
import type { useVoice } from "@/hooks/useVoice"
import { cn } from "@/lib/utils"

export interface SidebarProps {
  activeConversationId: string | null
  onSelectConversation: (id: string) => void
  onCreateConversation: () => void
  onScopeChange: (scope: string[] | null) => void
  voice: ReturnType<typeof useVoice>
}

/** Inner content — shared between desktop sidebar and mobile drawer. */
export function SidebarContent({
  activeConversationId,
  onSelectConversation,
  onCreateConversation,
  onScopeChange,
  voice,
}: SidebarProps) {
  const { list } = useDocuments()
  const chunkCount = list.data?.chunk_count ?? 0
  const fileCount = list.data?.files?.length ?? list.data?.filenames?.length ?? 0

  return (
    <div className="flex h-full min-h-0 w-full flex-col">
      <ScrollArea className="flex-1">
        <div className="space-y-4 p-3">
          {/* Chats — open by default. */}
          <CollapsibleSection
            id="chats"
            icon={<MessageSquare className="h-3.5 w-3.5" />}
            title="Chats"
            defaultOpen
          >
            <ConversationList
              activeId={activeConversationId}
              onSelect={onSelectConversation}
              onCreate={onCreateConversation}
            />
          </CollapsibleSection>

          {/* Library — Documents + Search-scope merged. */}
          <CollapsibleSection
            id="library"
            icon={<BookOpen className="h-3.5 w-3.5" />}
            title="Library"
            badge={
              chunkCount > 0
                ? `${fileCount} file${fileCount === 1 ? "" : "s"} · ${chunkCount} chunks`
                : list.isLoading
                  ? "…"
                  : ""
            }
            defaultOpen
          >
            <Library onScopeChange={onScopeChange} />
          </CollapsibleSection>

          {/* Voice — collapsed by default (most users don't enable it). */}
          <CollapsibleSection
            id="voice"
            icon={<Mic className="h-3.5 w-3.5" />}
            title="Voice"
            defaultOpen={false}
          >
            <label className="flex cursor-pointer items-center justify-between gap-2 rounded-md border bg-card px-2.5 py-2 text-xs">
              <span>Voice mode</span>
              <Switch
                checked={voice.enabled}
                onCheckedChange={(v) => voice.setEnabled(v)}
              />
            </label>
            {voice.enabled && (
              <p className="mt-2 px-1 text-[11px] leading-snug text-muted-foreground">
                🌐 English only — Whisper-base.en in, Kokoro-82M out. Mic
                appears beside the chat input; auto-stops after 1.5 s of
                silence.
              </p>
            )}
          </CollapsibleSection>
        </div>
      </ScrollArea>
    </div>
  )
}

/** Desktop variant — fixed-width left rail, hidden below md. */
export function Sidebar(props: SidebarProps) {
  return (
    <aside className="hidden w-64 shrink-0 border-r md:block">
      <SidebarContent {...props} />
    </aside>
  )
}

// ---------------------------------------------------------------------------
// Collapsible section primitive (state persisted to localStorage so the
// user's layout sticks across reloads).
// ---------------------------------------------------------------------------
const SECTION_STATE_KEY = "hushdoc-sidebar-sections"

function readSectionState(): Record<string, boolean> {
  try {
    const raw = localStorage.getItem(SECTION_STATE_KEY)
    if (!raw) return {}
    const parsed = JSON.parse(raw)
    return typeof parsed === "object" && parsed ? parsed : {}
  } catch {
    return {}
  }
}

function writeSectionState(state: Record<string, boolean>): void {
  try {
    localStorage.setItem(SECTION_STATE_KEY, JSON.stringify(state))
  } catch {
    /* ignore quota / disabled storage */
  }
}

function CollapsibleSection({
  id,
  icon,
  title,
  badge,
  defaultOpen,
  children,
}: {
  id: string
  icon: React.ReactNode
  title: string
  badge?: string
  defaultOpen: boolean
  children: React.ReactNode
}) {
  const [open, setOpen] = useState<boolean>(() => {
    const persisted = readSectionState()
    return id in persisted ? !!persisted[id] : defaultOpen
  })

  const toggle = useCallback(() => {
    setOpen((prev) => {
      const next = !prev
      const all = readSectionState()
      writeSectionState({ ...all, [id]: next })
      return next
    })
  }, [id])

  // Keep state in sync if another tab / instance toggles it.
  useEffect(() => {
    const onStorage = (e: StorageEvent) => {
      if (e.key !== SECTION_STATE_KEY) return
      const next = readSectionState()
      if (id in next) setOpen(!!next[id])
    }
    window.addEventListener("storage", onStorage)
    return () => window.removeEventListener("storage", onStorage)
  }, [id])

  return (
    <div className={cn("space-y-2", open && "pb-1")}>
      <button
        type="button"
        onClick={toggle}
        className="group flex w-full items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground transition-colors hover:text-foreground"
      >
        {open ? (
          <ChevronDown className="h-3 w-3 transition-transform" />
        ) : (
          <ChevronRight className="h-3 w-3 transition-transform" />
        )}
        {icon}
        {title}
        {badge && (
          <span className="ml-auto rounded bg-muted px-1.5 py-0.5 text-[10px] font-normal lowercase tracking-normal text-muted-foreground/80 group-hover:text-muted-foreground">
            {badge}
          </span>
        )}
      </button>
      {open && children}
    </div>
  )
}
