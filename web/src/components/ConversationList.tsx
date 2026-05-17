import { useState } from "react"
import { MessageSquare, Plus, Trash2 } from "lucide-react"

import { Button } from "@/components/ui/button"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { useConversations } from "@/hooks/useConversations"
import { useT } from "@/lib/lang-context"
import { cn } from "@/lib/utils"

interface ConversationListProps {
  activeId: string | null
  onSelect: (id: string) => void
  onCreate: () => void
}

/** ChatGPT-style sidebar list. New chats land at the top; deleting the
 *  active conversation falls back to the next one. */
export function ConversationList({
  activeId,
  onSelect,
  onCreate,
}: ConversationListProps) {
  const t = useT()
  const { list, remove } = useConversations()
  const [pendingDelete, setPendingDelete] = useState<string | null>(null)

  const items = list.data ?? []

  return (
    <div className="space-y-1.5">
      <Button
        size="sm"
        variant="outline"
        className="w-full justify-start gap-2"
        onClick={onCreate}
      >
        <Plus className="h-3.5 w-3.5" />
        {t("sidebar.newChat")}
      </Button>

      {items.length === 0 ? (
        <p className="px-1 py-1 text-[11px] text-muted-foreground">
          {list.isLoading ? "…" : t("sidebar.noChatsYet")}
        </p>
      ) : (
        <TooltipProvider delayDuration={500}>
          <ul className="space-y-0.5">
            {items.map((c) => {
              const isActive = c.id === activeId
              const isPending = pendingDelete === c.id
              return (
                <li key={c.id}>
                  <div
                    className={cn(
                      "group flex items-center gap-1.5 rounded-md transition-colors",
                      isActive ? "bg-accent" : "hover:bg-accent/60",
                    )}
                  >
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <button
                          type="button"
                          onClick={() => onSelect(c.id)}
                          className={cn(
                            "flex min-w-0 flex-1 items-center gap-2 rounded-md px-2 py-1.5 text-left text-xs",
                            !isActive && "text-muted-foreground",
                          )}
                        >
                          <MessageSquare className="h-3 w-3 shrink-0 opacity-70" />
                          <span className="truncate">{c.title || t("sidebar.newChat")}</span>
                        </button>
                      </TooltipTrigger>
                      <TooltipContent side="right" className="max-w-xs">
                        <div className="font-medium">{c.title || t("sidebar.newChat")}</div>
                        <div className="mt-0.5 text-[10px] opacity-80">
                          {c.message_count} messages ·{" "}
                          {new Date(c.updated_at * 1000).toLocaleString()}
                        </div>
                      </TooltipContent>
                    </Tooltip>

                    {/* Delete: two-click confirmation inside the row. */}
                    {isPending ? (
                      <div className="flex shrink-0 items-center gap-0.5 pr-1">
                        <button
                          type="button"
                          className="rounded px-1.5 py-0.5 text-[10px] font-semibold text-destructive hover:bg-destructive/10"
                          onClick={() => {
                            remove.mutate(c.id, {
                              onSuccess: () => setPendingDelete(null),
                            })
                          }}
                        >
                          Confirm
                        </button>
                        <button
                          type="button"
                          className="rounded px-1.5 py-0.5 text-[10px] text-muted-foreground hover:bg-muted"
                          onClick={() => setPendingDelete(null)}
                        >
                          Cancel
                        </button>
                      </div>
                    ) : (
                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation()
                          setPendingDelete(c.id)
                        }}
                        title={t("common.delete")}
                        className="mr-1 hidden h-6 w-6 shrink-0 items-center justify-center rounded text-muted-foreground hover:bg-destructive/10 hover:text-destructive group-hover:flex"
                      >
                        <Trash2 className="h-3 w-3" />
                      </button>
                    )}
                  </div>
                </li>
              )
            })}
          </ul>
        </TooltipProvider>
      )}
    </div>
  )
}
