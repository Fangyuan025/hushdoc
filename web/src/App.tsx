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
        // v0.2.0: slimmer padding so the header reads as a status bar
        // not a hero strip. px-3 py-1 -> px-2 py-0.5; text-xs unchanged.
        "inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[11px] font-medium",
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

  // Heartbeat: tells the launcher's watchdog whether the browser is
  // still around. Two channels:
  //
  //   - Regular ping (every 10 s, plus an eager one on visibility
  //     change to *visible*). Pings unconditionally even when the tab
  //     is hidden — browsers throttle background `setInterval` to
  //     ~1/min and the backend's idle window (60 s) is sized for that.
  //
  //   - Goodbye beacon on `pagehide`. `navigator.sendBeacon` survives
  //     the page being unloaded; regular `fetch` is killed mid-flight.
  //     This switches the backend to a 5 s goodbye window so the
  //     launcher's cleanup prompt appears almost instantly when the
  //     user actually closes the tab — without sacrificing the long
  //     idle window that protects backgrounded tabs from false exit.
  //     A page reload also fires pagehide, but the freshly-mounted
  //     page sends a regular ping within a second which cancels the
  //     goodbye state on the backend before it expires.
  useEffect(() => {
    const ping = () => {
      void fetch("/api/heartbeat", { method: "POST" }).catch(() => {})
    }
    ping()
    const id = window.setInterval(ping, 10_000)

    const onShow = () => {
      if (document.visibilityState === "visible") ping()
    }
    const onHide = () => {
      // sendBeacon: queued by the browser, survives page unload.
      // Falls through to keepalive fetch if sendBeacon is missing.
      try {
        if (!navigator.sendBeacon?.("/api/heartbeat?closing=1")) {
          void fetch("/api/heartbeat?closing=1", {
            method: "POST",
            keepalive: true,
          }).catch(() => {})
        }
      } catch {
        /* best-effort; the 60 s idle window is the safety net */
      }
    }

    document.addEventListener("visibilitychange", onShow)
    window.addEventListener("pagehide", onHide)
    return () => {
      window.clearInterval(id)
      document.removeEventListener("visibilitychange", onShow)
      window.removeEventListener("pagehide", onHide)
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
      {/* Slimmer header for v0.2.0: drop the redundant 'local-only
          document assistant' subtitle (it's already the badge in the
          Library row + footer), reduce vertical padding, shrink the
          HealthPill via its variant. Saves ~14 px of fixed chrome. */}
      <header className="flex shrink-0 items-center justify-between gap-2 border-b px-3 py-1.5 sm:px-4">
        <div className="flex min-w-0 items-center gap-2">
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

          <span className="text-lg leading-none">🤫</span>
          <h1 className="text-[15px] font-semibold tracking-tight">Hushdoc</h1>
        </div>
        <div className="flex shrink-0 items-center gap-1.5">
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
