/**
 * v0.6.2 — corner resource panel.
 *
 * Tiny instrument readout in the header. Displays:
 *   ⚡ <ch/s>  ·  GPU <vram>  ·  RAM <total rss>
 *
 * Hover lifts a popover with detailed per-process / per-device stats.
 * char/s is computed client-side from a rolling 3-second window of
 * SSE token chunks (the `hushdoc:tok` custom event); GPU + RAM numbers
 * come from a /api/resource poll every 2 seconds. Polling runs at 1s
 * while a chunk has been seen in the last 1.5s (active mode) and 4s
 * otherwise (idle) -- the panel doesn't pretend to be realtime when
 * nothing is happening.
 *
 * Styling intentionally uses stock Tailwind utilities only so the
 * panel drops cleanly into the existing v0.6.x design system without
 * needing new CSS variables.
 */
import { useEffect, useRef, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { Cpu, MemoryStick, Zap } from "lucide-react"

import { useT } from "@/lib/lang-context"
import { cn } from "@/lib/utils"
import { onTokens } from "@/lib/tokRate"

interface ResourceSnapshot {
  backend_pid: number
  backend_rss: number
  llama_pid: number | null
  llama_rss: number
  total_rss: number
  gpu: {
    name: string
    vram_used: number
    vram_total: number
    util: number
  } | null
}

async function fetchResource(): Promise<ResourceSnapshot> {
  const r = await fetch("/api/resource")
  if (!r.ok) throw new Error(`/api/resource -> ${r.status}`)
  return r.json()
}

function fmtBytes(b: number): string {
  if (!b || b < 0) return "—"
  if (b < 1024) return `${b}B`
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(0)}K`
  if (b < 1024 * 1024 * 1024) return `${(b / 1024 / 1024).toFixed(0)}M`
  return `${(b / 1024 / 1024 / 1024).toFixed(1)}G`
}

/** Rolling 3-second char/s tracker. Subscribes to the tokRate event
 *  channel; the rate value is React state so it triggers re-renders. */
function useCharRate() {
  const samples = useRef<Array<{ t: number; n: number }>>([])
  const [rate, setRate] = useState(0)
  const [active, setActive] = useState(false)
  const idleTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    const off = onTokens((chars) => {
      const now = performance.now()
      samples.current.push({ t: now, n: chars })
      const cutoff = now - 3000
      while (samples.current.length && samples.current[0].t < cutoff) {
        samples.current.shift()
      }
      const span = samples.current.length > 1
        ? (now - samples.current[0].t) / 1000
        : 0.3 // first chunk: assume 300ms so the displayed rate isn't infinity
      const total = samples.current.reduce((a, s) => a + s.n, 0)
      setRate(span > 0 ? total / span : 0)
      setActive(true)
      if (idleTimer.current) clearTimeout(idleTimer.current)
      idleTimer.current = setTimeout(() => setActive(false), 1500)
    })
    return () => {
      off()
      if (idleTimer.current) clearTimeout(idleTimer.current)
    }
  }, [])
  return { rate, active }
}

export function ResourcePanel() {
  const t = useT()
  const { rate, active } = useCharRate()
  const refetchInterval = active ? 1000 : 4000
  const { data } = useQuery({
    queryKey: ["resource"],
    queryFn: fetchResource,
    refetchInterval,
    staleTime: 1500,
  })

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
  useEffect(() => () => cancelClose(), [])

  // Heuristic: most consumer machines today are 8-32 GB. Use 16 GB as
  // the "full" reference for the inline progress bar; if you run on
  // 64 GB the bar just stays cool, no harm done.
  const ramPct = data?.total_rss
    ? Math.min(99, Math.round((data.total_rss / (16 * 1024 ** 3)) * 100))
    : 0
  const vramPct = data?.gpu
    ? Math.round((data.gpu.vram_used / data.gpu.vram_total) * 100)
    : 0

  return (
    <div
      className="relative inline-flex"
      onMouseEnter={() => {
        cancelClose()
        setOpen(true)
      }}
      onMouseLeave={scheduleClose}
    >
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className={cn(
          "group inline-flex items-center gap-1.5 rounded-md px-2 py-1",
          "font-mono text-[10.5px] tabular-nums transition-colors",
          "text-muted-foreground/70 hover:text-foreground hover:bg-accent/40",
        )}
        title={t("resource.click")}
      >
        <Zap
          className={cn(
            "h-3 w-3",
            active ? "text-emerald-500" : "text-muted-foreground/50",
          )}
        />
        <span className={cn(!active && "opacity-60")}>
          {active ? Math.round(rate) : "—"}
          <span className="ml-0.5 text-muted-foreground/50">ch/s</span>
        </span>
        {data?.gpu && (
          <>
            <span className="text-muted-foreground/30">·</span>
            <span
              className={cn(vramPct > 90 && "text-rose-500")}
              title={t("resource.gpuVram")}
            >
              {fmtBytes(data.gpu.vram_used)}
              <span className="ml-0.5 text-muted-foreground/50">vram</span>
            </span>
          </>
        )}
        <span className="text-muted-foreground/30">·</span>
        <span
          className={cn(ramPct > 90 && "text-rose-500")}
          title={t("resource.totalRss")}
        >
          {fmtBytes(data?.total_rss ?? 0)}
          <span className="ml-0.5 text-muted-foreground/50">ram</span>
        </span>
      </button>

      {open && data && (
        <div
          role="tooltip"
          onMouseEnter={cancelClose}
          onMouseLeave={scheduleClose}
          className={cn(
            "absolute right-0 top-[calc(100%+6px)] z-40 w-[280px]",
            "rounded-md border bg-popover px-3 py-2.5",
            "text-popover-foreground shadow-lg",
          )}
        >
          <Section title={t("resource.generation")}>
            <Row
              icon={<Zap className="h-3 w-3" />}
              label="Throughput"
              value={
                <span className={cn(active && "text-emerald-600 dark:text-emerald-400")}>
                  {active ? `${Math.round(rate)} ch/s` : "idle"}
                </span>
              }
            />
          </Section>

          {data.gpu && (
            <Section title={t("resource.gpu")}>
              <Row label="Device" value={data.gpu.name} />
              <Row
                icon={<Cpu className="h-3 w-3" />}
                label="Util"
                value={`${data.gpu.util}%`}
              />
              <Row
                label="VRAM"
                value={`${fmtBytes(data.gpu.vram_used)} / ${fmtBytes(data.gpu.vram_total)}`}
                bar={vramPct}
                tone={vramPct > 90 ? "danger" : vramPct > 75 ? "warn" : undefined}
              />
            </Section>
          )}

          <Section title={t("resource.memoryRss")}>
            <Row
              icon={<MemoryStick className="h-3 w-3" />}
              label="Backend"
              value={fmtBytes(data.backend_rss)}
            />
            {data.llama_pid && (
              <Row
                label="llama-server"
                value={fmtBytes(data.llama_rss)}
              />
            )}
            <Row
              label="Total"
              value={fmtBytes(data.total_rss)}
              emphasize
            />
          </Section>
        </div>
      )}
    </div>
  )
}

function Section({
  title,
  children,
}: {
  title: string
  children: React.ReactNode
}) {
  return (
    <div className="mb-2 last:mb-0">
      <div className="mb-1 font-mono text-[9px] uppercase tracking-[0.06em] text-muted-foreground/60">
        {title}
      </div>
      <div className="space-y-0.5">{children}</div>
    </div>
  )
}

function Row({
  icon,
  label,
  value,
  bar,
  tone,
  emphasize,
}: {
  icon?: React.ReactNode
  label: string
  value: React.ReactNode
  bar?: number
  tone?: "warn" | "danger"
  emphasize?: boolean
}) {
  return (
    <div className="flex items-center gap-2 text-[11px]">
      {icon && (
        <span className="text-muted-foreground/60">{icon}</span>
      )}
      <span className="text-muted-foreground/80">{label}</span>
      <span
        className={cn(
          "ml-auto truncate font-mono tabular-nums",
          emphasize && "font-semibold text-foreground",
          !emphasize && "text-foreground/85",
        )}
      >
        {value}
      </span>
      {typeof bar === "number" && (
        <div className="ml-2 h-1 w-12 overflow-hidden rounded-full bg-muted">
          <div
            className={cn(
              "h-full transition-[width] duration-300",
              tone === "danger" && "bg-rose-500",
              tone === "warn" && "bg-amber-500",
              !tone && "bg-primary/70",
            )}
            style={{ width: `${Math.min(100, Math.max(0, bar))}%` }}
          />
        </div>
      )}
    </div>
  )
}
