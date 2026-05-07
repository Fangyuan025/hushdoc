import { useCallback, useEffect, useRef, useState } from "react"
import {
  QueryClient,
  QueryClientProvider,
  useQuery,
} from "@tanstack/react-query"
import {
  AlertTriangle,
  Loader2,
  Menu,
  Moon,
  ShieldCheck,
  Sun,
} from "lucide-react"
import { Toaster } from "sonner"

import { ChatPane, type ChatPaneHandle } from "@/components/ChatPane"
import { Sidebar, SidebarContent } from "@/components/Sidebar"
import { Button } from "@/components/ui/button"
import { Sheet, SheetContent, SheetTrigger } from "@/components/ui/sheet"
import { useConversations } from "@/hooks/useConversations"
import { useKeyboardShortcuts } from "@/hooks/useKeyboardShortcuts"
import { useVoice } from "@/hooks/useVoice"
import { apiHealth } from "@/lib/api"
import { cn } from "@/lib/utils"

const ACTIVE_CONV_KEY = "hushdoc-active-conv"

const qc = new QueryClient({
  defaultOptions: {
    queries: { retry: 1, staleTime: 5_000 },
  },
})

function HealthPill() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["health"],
    queryFn: apiHealth,
    refetchInterval: 5_000,
  })

  if (isLoading)
    return (
      <Pill icon={<Loader2 className="h-3.5 w-3.5 animate-spin" />}>
        Connecting…
      </Pill>
    )
  if (error)
    return (
      <Pill
        icon={<AlertTriangle className="h-3.5 w-3.5" />}
        variant="destructive"
      >
        Backend offline
      </Pill>
    )
  return (
    <Pill
      icon={<ShieldCheck className="h-3.5 w-3.5" />}
      variant={data?.chain_loaded ? "ready" : "loading"}
    >
      {data?.vector_count ?? 0} chunks ·{" "}
      {data?.chain_loaded ? "ready" : "warming up"}
    </Pill>
  )
}

function Pill({
  children,
  icon,
  variant = "loading",
}: {
  children: React.ReactNode
  icon?: React.ReactNode
  variant?: "loading" | "ready" | "destructive"
}) {
  return (
    <div
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-medium",
        variant === "ready" &&
          "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300",
        variant === "loading" &&
          "border-border bg-muted text-muted-foreground",
        variant === "destructive" &&
          "border-destructive/30 bg-destructive/10 text-destructive",
      )}
    >
      {icon}
      {children}
    </div>
  )
}

function Shell() {
  const [dark, setDark] = useState(
    () =>
      typeof window !== "undefined" &&
      window.matchMedia("(prefers-color-scheme: dark)").matches,
  )
  useEffect(() => {
    document.documentElement.classList.toggle("dark", dark)
  }, [dark])

  // Pre-warm the backend on app boot so the user's first message
  // doesn't sit on "..." for 30-60s while the embedding model and
  // llama-server.exe spin up. /api/chat/clear is the cheapest call
  // that exercises both via deps.get_chain().
  useEffect(() => {
    fetch("/api/chat/clear", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: "__warmup__" }),
    }).catch(() => {
      /* warmup is best-effort; first send will retry the load */
    })
  }, [])

  // Heartbeat: pings the backend every 5 s while the tab is open. The
  // launcher's auto-shutdown watchdog uses this to detect that the user
  // has closed the browser — when heartbeats stop for ~15 s the backend
  // self-exits, the launcher's wait loop unblocks, and its cleanup
  // prompt fires. A `sendBeacon` on `pagehide` covers the close-tab
  // case so the watchdog notices within the first 5 s of silence.
  useEffect(() => {
    const ping = () => {
      // Only ping when the document is visible — backgrounded tabs
      // throttle setInterval anyway, and the watchdog grace window of
      // 15 s tolerates short visibility gaps.
      if (document.visibilityState === "hidden") return
      void fetch("/api/heartbeat", { method: "POST" }).catch(() => {})
    }
    ping()
    const id = window.setInterval(ping, 5_000)
    const onShow = () => ping()
    document.addEventListener("visibilitychange", onShow)
    return () => {
      window.clearInterval(id)
      document.removeEventListener("visibilitychange", onShow)
    }
  }, [])

  // Lifted state.
  const [scope, setScope] = useState<string[] | null>(null)
  const chatRef = useRef<ChatPaneHandle>(null)
  const voice = useVoice()
  const [drawerOpen, setDrawerOpen] = useState(false)

  // Active conversation id, persisted per-tab. null = empty / new-chat
  // state where typing the first message will auto-create a conv.
  const [activeConversationId, setActiveConversationId] = useState<string | null>(
    () => {
      try {
        return sessionStorage.getItem(ACTIVE_CONV_KEY) || null
      } catch {
        return null
      }
    },
  )
  useEffect(() => {
    try {
      if (activeConversationId)
        sessionStorage.setItem(ACTIVE_CONV_KEY, activeConversationId)
      else sessionStorage.removeItem(ACTIVE_CONV_KEY)
    } catch {
      /* ignore */
    }
  }, [activeConversationId])

  const { create: createConv, applyTitleEvent, list: convList } =
    useConversations()

  // If the persisted active id refers to a conv that's been deleted
  // since the last load, drop it on first list-fetch.
  useEffect(() => {
    if (!activeConversationId || !convList.data) return
    if (!convList.data.some((c) => c.id === activeConversationId)) {
      setActiveConversationId(null)
    }
  }, [activeConversationId, convList.data])

  // Suppress hydration for a freshly-created conv ONCE — otherwise the
  // hydration fetch races with the in-flight optimistic send and wipes
  // the user/assistant messages we just appended locally.
  const [skipHydrationFor, setSkipHydrationFor] = useState<string | null>(null)

  const handleEnsureConversation = useCallback(async () => {
    const conv = await createConv.mutateAsync(undefined)
    setSkipHydrationFor(conv.id)
    setActiveConversationId(conv.id)
    return conv.id
  }, [createConv])

  const handleCreateNewChat = useCallback(() => {
    // ChatGPT-style "+ New chat": just clear the active id; an empty
    // pane is shown, and the first send will create the conv lazily.
    voice.stopPlayback()
    setActiveConversationId(null)
    setDrawerOpen(false)
  }, [voice])

  const handleSelectConversation = useCallback((id: string) => {
    voice.stopPlayback()
    setActiveConversationId(id)
    setDrawerOpen(false)
  }, [voice])

  // Global keyboard shortcuts.
  useKeyboardShortcuts({
    onFocusInput: () => chatRef.current?.focusInput(),
    onClearChat: handleCreateNewChat,
    onEscape: () => chatRef.current?.cancel(),
  })

  return (
    <div className="flex h-full flex-col">
      <header className="flex shrink-0 items-center justify-between gap-2 border-b px-4 py-2.5 sm:px-5">
        <div className="flex min-w-0 items-center gap-2.5">
          {/* Hamburger — mobile only */}
          <Sheet open={drawerOpen} onOpenChange={setDrawerOpen}>
            <SheetTrigger asChild>
              <Button
                type="button"
                variant="ghost"
                size="icon-sm"
                className="md:hidden"
              >
                <Menu className="h-4 w-4" />
              </Button>
            </SheetTrigger>
            <SheetContent side="left" className="p-0">
              <SidebarContent
                activeConversationId={activeConversationId}
                onSelectConversation={handleSelectConversation}
                onCreateConversation={handleCreateNewChat}
                onScopeChange={setScope}
                voice={voice}
              />
            </SheetContent>
          </Sheet>

          <span className="text-xl">🤫</span>
          <h1 className="text-base font-semibold tracking-tight">Hushdoc</h1>
          <span className="hidden truncate text-xs text-muted-foreground sm:inline">
            local-only document assistant
          </span>
        </div>
        <div className="flex shrink-0 items-center gap-2 sm:gap-3">
          <HealthPill />
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            onClick={() => setDark((d) => !d)}
            title={dark ? "Switch to light" : "Switch to dark"}
          >
            {dark ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
          </Button>
        </div>
      </header>

      <main className="flex min-h-0 flex-1">
        <Sidebar
          activeConversationId={activeConversationId}
          onSelectConversation={handleSelectConversation}
          onCreateConversation={handleCreateNewChat}
          onScopeChange={setScope}
          voice={voice}
        />
        <div className="flex min-w-0 flex-1">
          <ChatPane
            ref={chatRef}
            conversationId={activeConversationId}
            onEnsureConversation={handleEnsureConversation}
            onTitleEvent={applyTitleEvent}
            skipHydrationFor={skipHydrationFor}
            onHydrationConsumed={() => setSkipHydrationFor(null)}
            scope={scope}
            voice={voice}
          />
        </div>
      </main>
    </div>
  )
}

export default function App() {
  return (
    <QueryClientProvider client={qc}>
      <Shell />
      <Toaster
        position="bottom-right"
        toastOptions={{
          classNames: {
            toast:
              "border bg-background text-foreground shadow-md rounded-md text-sm",
          },
        }}
      />
    </QueryClientProvider>
  )
}
