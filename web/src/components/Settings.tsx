/**
 * Settings modal — v0.3.0.
 *
 * Two persisted options:
 *   1. Custom **model path** — changing it stops the running
 *      llama-server.exe, spawns a new one against the new GGUF, and
 *      rebuilds the chain. The PUT call doesn't return until the new
 *      model is loaded (or fails), so the user sees the actual
 *      "applied" state, not just "saved".
 *   2. **Auto-cleanup on exit** — read by hushdoc.ps1 (not this
 *      process) after the wait loop exits. When true, the launcher
 *      skips its per-category cleanup prompts and just nukes
 *      chat_history / data/uploads / chroma_db without asking.
 *
 * Shape is a full-screen overlay because:
 *   - the model-path field benefits from horizontal room
 *   - the reload step can take 10-30 s; we want to focus the user on
 *     the operation, not let them keep typing in the chat below
 */
import { useEffect, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  AlertTriangle,
  CheckCircle2,
  Loader2,
  Settings as SettingsIcon,
  X,
} from "lucide-react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { apiGetConfig, apiSaveConfig } from "@/lib/api"
import type { Lang } from "@/lib/i18n"
import { useLang, useT } from "@/lib/lang-context"
import { cn } from "@/lib/utils"

interface SettingsModalProps {
  onClose: () => void
}

export function SettingsModal({ onClose }: SettingsModalProps) {
  const t = useT()
  const { lang, setLang } = useLang()
  const qc = useQueryClient()
  const { data: config, isLoading } = useQuery({
    queryKey: ["config"],
    queryFn: apiGetConfig,
    // Settings are user-driven, not polled — no refetchInterval.
    staleTime: 60_000,
  })

  // Local form state, hydrated when the query resolves.
  const [modelPath, setModelPath] = useState("")
  const [autoCleanup, setAutoCleanup] = useState(false)
  useEffect(() => {
    if (config) {
      setModelPath(config.model_path)
      setAutoCleanup(config.auto_cleanup_on_exit)
    }
  }, [config])

  const modelPathDirty = !!config && modelPath.trim() !== config.model_path
  const autoCleanupDirty = !!config && autoCleanup !== config.auto_cleanup_on_exit
  const anythingDirty = modelPathDirty || autoCleanupDirty

  const save = useMutation({
    mutationFn: async () => {
      // Only send the keys the user actually changed -- the backend
      // accepts a partial update so an unrelated setting can't get
      // clobbered by stale form state.
      const patch: { model_path?: string; auto_cleanup_on_exit?: boolean } = {}
      if (modelPathDirty) patch.model_path = modelPath.trim()
      if (autoCleanupDirty) patch.auto_cleanup_on_exit = autoCleanup
      return apiSaveConfig(patch)
    },
    onSuccess: (fresh) => {
      qc.setQueryData(["config"], fresh)
      qc.invalidateQueries({ queryKey: ["health"] })
      if (modelPathDirty) {
        toast.success(
          `Model swapped to ${trimPath(fresh.model_path)}. Chain reloaded.`,
        )
      } else {
        toast.success("Settings saved.")
      }
    },
    onError: (err) => toast.error(err.message),
  })

  useEffect(() => {
    const onEsc = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !save.isPending) onClose()
    }
    window.addEventListener("keydown", onEsc)
    return () => window.removeEventListener("keydown", onEsc)
  }, [onClose, save.isPending])

  const ggufOk = config?.model_path_valid ?? false

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      onClick={(e) => {
        if (e.target === e.currentTarget && !save.isPending) onClose()
      }}
    >
      <div className="flex max-h-[min(90vh,720px)] w-full max-w-2xl flex-col overflow-hidden rounded-lg border bg-background shadow-xl">
        <div className="flex items-center gap-2 border-b px-4 py-2.5">
          <SettingsIcon className="h-4 w-4 text-muted-foreground" />
          <div className="text-sm font-medium">{t("settings.title")}</div>
          <button
            type="button"
            onClick={onClose}
            disabled={save.isPending}
            className="ml-auto rounded p-1 text-muted-foreground hover:bg-accent disabled:opacity-50"
            title={`${t("settings.close")} (Esc)`}
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto">
          {isLoading || !config ? (
            <div className="p-6 text-sm text-muted-foreground">
              <Loader2 className="mr-2 inline h-4 w-4 animate-spin" />
              {t("settings.loading")}
            </div>
          ) : (
            <div className="space-y-6 p-5">
              {/* Language (v0.7.0) -- bilingual UI toggle. Lives at the
                  top so users coming in to flip language find it first. */}
              <section className="space-y-2">
                <h3 className="text-sm font-semibold">
                  {t("settings.section.language")}
                </h3>
                <p className="text-[11px] text-muted-foreground leading-snug">
                  {t("settings.section.language.desc")}
                </p>
                <div className="flex items-center gap-2">
                  <div className="inline-flex rounded-md border bg-card p-0.5">
                    {(["en", "zh"] as const).map((opt) => (
                      <button
                        key={opt}
                        type="button"
                        onClick={() => setLang(opt as Lang)}
                        className={cn(
                          "rounded px-3 py-1 text-xs transition-colors",
                          lang === opt
                            ? "bg-primary text-primary-foreground"
                            : "text-muted-foreground hover:text-foreground",
                        )}
                      >
                        {opt === "en" ? "English" : "中文"}
                      </button>
                    ))}
                  </div>
                  <span className="inline-flex items-center gap-1 text-[10px] text-emerald-600 dark:text-emerald-400">
                    <CheckCircle2 className="h-3 w-3" />
                    {t("settings.section.language.instant")}
                  </span>
                </div>
              </section>

              <hr className="border-border/50" />

              {/* Model path */}
              <section className="space-y-2">
                <div className="flex items-baseline gap-2">
                  <h3 className="text-sm font-semibold">{t("settings.section.model")}</h3>
                  <span className="text-[11px] text-muted-foreground">
                    {t("settings.section.model.tag")}
                  </span>
                  <PathStatusPip ok={ggufOk} okLabel={t("settings.filePresent")} missLabel={t("settings.fileMissing")} />
                </div>
                <p className="text-[11px] text-muted-foreground leading-snug">
                  {t("settings.section.model.desc")}
                </p>
                <input
                  type="text"
                  value={modelPath}
                  onChange={(e) => setModelPath(e.target.value)}
                  disabled={save.isPending}
                  placeholder="./models/model.gguf"
                  spellCheck={false}
                  className="w-full rounded-md border bg-card px-2.5 py-1.5 font-mono text-xs outline-none focus:ring-1 focus:ring-ring disabled:opacity-50"
                />
                {modelPathDirty && (
                  <p className="text-[11px] text-amber-600 dark:text-amber-400">
                    <AlertTriangle className="mr-1 inline h-3 w-3" />
                    {t("settings.section.model.dirtyWarn")}
                  </p>
                )}
              </section>

              <hr className="border-border/50" />

              {/* Auto-cleanup */}
              <section className="space-y-2">
                <h3 className="text-sm font-semibold">{t("settings.section.cleanup")}</h3>
                <label className="flex cursor-pointer items-start gap-2.5 rounded-md border bg-card/50 p-3 hover:bg-card">
                  <input
                    type="checkbox"
                    checked={autoCleanup}
                    onChange={(e) => setAutoCleanup(e.target.checked)}
                    disabled={save.isPending}
                    className="mt-0.5 h-4 w-4 accent-primary"
                  />
                  <div className="min-w-0">
                    <div className="text-sm font-medium">
                      {t("settings.cleanup.toggle")}
                    </div>
                    <div className="mt-1 text-[11px] leading-snug text-muted-foreground">
                      {t("settings.cleanup.desc")}
                    </div>
                  </div>
                </label>
              </section>
            </div>
          )}
        </div>

        <div className="flex items-center justify-between border-t px-4 py-2.5">
          <div className="text-[11px] text-muted-foreground">
            {save.isPending
              ? t("settings.saving")
              : anythingDirty
                ? t("settings.unsaved")
                : t("settings.upToDate")}
          </div>
          <div className="flex items-center gap-2">
            <Button
              size="sm"
              variant="ghost"
              onClick={onClose}
              disabled={save.isPending}
            >
              {t("settings.close")}
            </Button>
            <Button
              size="sm"
              disabled={!anythingDirty || save.isPending}
              onClick={() => save.mutate()}
            >
              {save.isPending && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
              {t("settings.save")}
            </Button>
          </div>
        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------

function PathStatusPip({
  ok,
  okLabel,
  missLabel,
}: {
  ok: boolean
  okLabel: string
  missLabel: string
}) {
  return (
    <span
      className={cn(
        "ml-auto inline-flex items-center gap-1 rounded-full px-1.5 py-0.5 text-[10px] font-medium",
        ok
          ? "bg-emerald-500/10 text-emerald-700 dark:text-emerald-400"
          : "bg-destructive/10 text-destructive",
      )}
    >
      {ok ? (
        <>
          <CheckCircle2 className="h-2.5 w-2.5" />
          {okLabel}
        </>
      ) : (
        <>
          <AlertTriangle className="h-2.5 w-2.5" />
          {missLabel}
        </>
      )}
    </span>
  )
}

function trimPath(p: string): string {
  // Keep readable: leaf name + one parent.
  const parts = p.replace(/\\/g, "/").split("/").filter(Boolean)
  if (parts.length <= 2) return p
  return `…/${parts.slice(-2).join("/")}`
}
