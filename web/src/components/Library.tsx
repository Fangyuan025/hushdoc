/**
 * Library panel — v0.2.0 replacement for the old Documents + Search-scope
 * pair. One row per indexed file:
 *
 *     [☑] filename.pdf                                       🗑 (hover)
 *         📄 91 chunks · 683 KB · uploaded May 13
 *
 * The checkbox IS the search-scope toggle (same effective scope as
 * before; no separate scope panel). The hover-trash removes the file
 * from the index. The upload zone collapses to a single "+ Add" button
 * with a popover menu (files / folder / paste text) so the panel stays
 * compact when the user just wants to read their existing library.
 */
import { useEffect, useMemo, useRef, useState } from "react"
import {
  CheckCircle2,
  ClipboardPaste,
  FileText,
  FolderUp,
  Image as ImageIcon,
  Loader2,
  Plus,
  Square,
  Trash2,
  Upload,
  XCircle,
} from "lucide-react"

import { Button } from "@/components/ui/button"
import { useDocuments } from "@/hooks/useDocuments"
import { useScope } from "@/hooks/useScope"
import { useT } from "@/lib/lang-context"
import { cn } from "@/lib/utils"
import type { FileMeta } from "@/types"

const ACCEPT = [
  ".pdf",
  ".docx",
  ".epub",
  ".jpg",
  ".jpeg",
  ".png",
  ".tif",
  ".tiff",
  ".bmp",
  ".md",
  ".markdown",
  ".txt",
]
const ACCEPT_ATTR = ACCEPT.join(",")

export interface LibraryProps {
  /** Lifted up to ChatPane via Sidebar's onScopeChange. */
  onScopeChange?: (effective: string[] | null) => void
}

export function Library({ onScopeChange }: LibraryProps) {
  const t = useT()
  const {
    list,
    del,
    delOne,
    pasteText,
    upload,
    cancelUpload,
    uploading,
    progress,
    dismissProgress,
  } = useDocuments()

  // The aggregated file list (rich metadata). Falls back to the bare
  // `filenames` array for very old backends that don't return `files`.
  const files: FileMeta[] = useMemo(() => {
    const data = list.data
    if (!data) return []
    if (data.files && data.files.length > 0) return data.files
    return (data.filenames ?? []).map((fn) => ({
      filename: fn,
      chunk_count: 0,
      file_size: 0,
      added_at: 0,
      source_kind: "unknown" as const,
    }))
  }, [list.data])

  const indexed = files.map((f) => f.filename)
  const scope = useScope(indexed)

  useEffect(() => {
    onScopeChange?.(scope.effectiveScope)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [JSON.stringify(scope.effectiveScope)])

  // ---- Upload triggers ---------------------------------------------------
  const fileInputRef = useRef<HTMLInputElement>(null)
  const folderInputRef = useRef<HTMLInputElement>(null)
  const [pasteOpen, setPasteOpen] = useState(false)
  const [menuOpen, setMenuOpen] = useState(false)

  // Close the popover when uploading starts so the progress list takes over.
  useEffect(() => {
    if (uploading) setMenuOpen(false)
  }, [uploading])

  // Auto-collapse the progress list after a brief success delay.
  useEffect(() => {
    if (progress.done && !uploading) {
      const t = setTimeout(() => dismissProgress(), 1500)
      return () => clearTimeout(t)
    }
  }, [progress.done, uploading, dismissProgress])

  const onPickFiles = (e: React.ChangeEvent<HTMLInputElement>) => {
    const picked = Array.from(e.target.files ?? []).filter((f) =>
      ACCEPT.some((ext) => f.name.toLowerCase().endsWith(ext)),
    )
    e.currentTarget.value = ""
    if (picked.length) void upload(picked, false, "uploaded")
    setMenuOpen(false)
  }

  // Hard upper bound on a single folder pick. Each ingest pulls Docling's
  // ~770 MB layout model + does a per-file inference pass; an accidental
  // "pick your whole Desktop" easily becomes a runaway that lights the
  // CPU on fire and writes Gb to chroma_db before the user can stop it.
  // 50 is plenty for real research / contract / paper batches; anything
  // bigger should be a deliberate decision.
  const FOLDER_FILE_LIMIT = 50

  const onPickFolder = (e: React.ChangeEvent<HTMLInputElement>) => {
    const all = Array.from(e.target.files ?? [])
    const totalSeen = all.length
    const picked = all.filter((f) =>
      ACCEPT.some((ext) => f.name.toLowerCase().endsWith(ext)),
    )
    e.currentTarget.value = ""
    setMenuOpen(false)
    if (picked.length === 0) {
      return
    }
    // If the folder is dangerously big, confirm before kicking off Docling.
    // Native confirm() is fine here — this is the safety net, not the
    // main flow.
    if (picked.length > FOLDER_FILE_LIMIT) {
      const ok = window.confirm(
        `This folder contains ${picked.length} ingestible files ` +
          `(saw ${totalSeen} total). Indexing them all may take a while ` +
          `and use significant disk + CPU. Continue?`,
      )
      if (!ok) return
    }
    void upload(picked, false, "folder")
  }

  // ---- Render -----------------------------------------------------------
  return (
    <div className="space-y-2">
      {/* Add menu / Cancel split: during ingest the button morphs into a
          Cancel control so a runaway folder pick can be stopped without
          killing the backend. */}
      <div className="relative flex gap-1">
        <Button
          size="sm"
          variant="outline"
          className="flex-1 justify-start text-xs"
          onClick={() => setMenuOpen((v) => !v)}
          disabled={uploading}
        >
          {uploading ? (
            <>
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
              {t("library.indexing")}
            </>
          ) : (
            <>
              <Plus className="h-3.5 w-3.5" />
              {t("library.addToLibrary")}
            </>
          )}
        </Button>
        {uploading && (
          <Button
            size="sm"
            variant="destructive"
            className="text-xs"
            onClick={cancelUpload}
            title={t("library.cancelTooltip")}
          >
            <Square className="h-3 w-3 fill-current" />
            {t("common.cancel")}
          </Button>
        )}
        {menuOpen && !uploading && (
          <AddMenu
            onPickFiles={() => fileInputRef.current?.click()}
            onPickFolder={() => folderInputRef.current?.click()}
            onPasteText={() => {
              setMenuOpen(false)
              setPasteOpen(true)
            }}
            onClose={() => setMenuOpen(false)}
          />
        )}
        {/* Hidden inputs that the menu triggers click on */}
        <input
          ref={fileInputRef}
          type="file"
          multiple
          accept={ACCEPT_ATTR}
          className="hidden"
          onChange={onPickFiles}
        />
        <input
          ref={folderInputRef}
          type="file"
          multiple
          {...(
            // `webkitdirectory` / `directory` aren't first-class React
            // input props but every modern browser supports them; spread
            // them as untyped HTML attributes so TS stays happy without
            // a brittle @ts-expect-error.
            {
              webkitdirectory: "",
              directory: "",
            } as React.HTMLAttributes<HTMLInputElement>
          )}
          className="hidden"
          onChange={onPickFolder}
        />
      </div>

      {/* Live ingest progress */}
      {progress.files.length > 0 && (
        <ul className="space-y-1">
          {progress.files.map((f) => (
            <li
              key={f.filename}
              className="flex items-start gap-2 rounded-md border bg-card/60 px-2 py-1.5 text-xs"
            >
              <ProgressIcon status={f.status} />
              <div className="min-w-0 flex-1">
                <div className="truncate font-medium">{f.filename}</div>
                {f.status === "indexed" && (
                  <div className="text-[10px] text-muted-foreground">
                    {f.chunks} chunks indexed
                  </div>
                )}
                {f.status === "error" && (
                  <div className="text-[10px] text-destructive">{f.error}</div>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}

      {/* The file list itself */}
      {list.isLoading ? (
        <Skeleton />
      ) : files.length === 0 ? (
        <EmptyHint />
      ) : (
        <>
          <ScopeToolbar
            total={files.length}
            selected={scope.selected.length}
            allSelected={scope.allSelected}
            onAll={scope.selectAll}
            onNone={scope.selectNone}
          />
          <ul className="space-y-1">
            {files.map((file) => (
              <FileRow
                key={file.filename}
                file={file}
                checked={scope.selected.includes(file.filename)}
                onToggle={() => scope.toggle(file.filename)}
                onDelete={() => delOne.mutate(file.filename)}
                deleting={delOne.isPending && delOne.variables === file.filename}
              />
            ))}
          </ul>
          {/* Danger-zone wipe-all stays available but is de-emphasised
              now that single-file delete is the common case. */}
          <DeleteAllButton onConfirm={() => del.mutate()} pending={del.isPending} />
        </>
      )}

      {pasteOpen && (
        <PasteTextModal
          onClose={() => setPasteOpen(false)}
          onSubmit={({ text, filename }) =>
            pasteText.mutate({ text, filename })
          }
          pending={pasteText.isPending}
        />
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------

function AddMenu({
  onPickFiles,
  onPickFolder,
  onPasteText,
  onClose,
}: {
  onPickFiles: () => void
  onPickFolder: () => void
  onPasteText: () => void
  onClose: () => void
}) {
  const t = useT()
  // Click-outside dismissal.
  useEffect(() => {
    const onDown = (e: MouseEvent) => {
      const el = e.target as HTMLElement
      if (!el.closest('[data-add-menu="root"]')) onClose()
    }
    window.addEventListener("mousedown", onDown)
    return () => window.removeEventListener("mousedown", onDown)
  }, [onClose])

  return (
    <div
      data-add-menu="root"
      className="absolute left-0 right-0 top-full z-20 mt-1 overflow-hidden rounded-md border bg-popover shadow-md"
    >
      <MenuItem icon={<Upload className="h-3.5 w-3.5" />} onClick={onPickFiles}>
        {t("library.addFiles")}
        <span className="ml-auto text-[10px] text-muted-foreground">
          {t("library.addFilesSub")}
        </span>
      </MenuItem>
      <MenuItem icon={<FolderUp className="h-3.5 w-3.5" />} onClick={onPickFolder}>
        {t("library.addFolder")}
        <span className="ml-auto text-[10px] text-muted-foreground">
          {t("library.addFolderSub")}
        </span>
      </MenuItem>
      <MenuItem
        icon={<ClipboardPaste className="h-3.5 w-3.5" />}
        onClick={onPasteText}
      >
        {t("library.pasteText")}
        <span className="ml-auto text-[10px] text-muted-foreground">
          {t("library.pasteTextSub")}
        </span>
      </MenuItem>
    </div>
  )
}

function MenuItem({
  icon,
  children,
  onClick,
}: {
  icon: React.ReactNode
  children: React.ReactNode
  onClick: () => void
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="flex w-full items-center gap-2 px-2.5 py-2 text-xs text-left hover:bg-accent"
    >
      {icon}
      {children}
    </button>
  )
}

// ---------------------------------------------------------------------------

function ScopeToolbar({
  total,
  selected,
  allSelected,
  onAll,
  onNone,
}: {
  total: number
  selected: number
  allSelected: boolean
  onAll: () => void
  onNone: () => void
}) {
  const t = useT()
  const label = allSelected
    ? t("library.allNInScope", { n: total })
    : selected === 0
      ? t("library.allInScope")
      : t("library.nInScope", { n: selected, total })
  return (
    <div className="flex items-center justify-between px-1 text-[10px] text-muted-foreground">
      <span>{label}</span>
      <button
        type="button"
        onClick={allSelected ? onNone : onAll}
        className="text-foreground/70 underline-offset-2 hover:underline"
      >
        {t(allSelected ? "library.selectNone" : "library.selectAll")}
      </button>
    </div>
  )
}

// ---------------------------------------------------------------------------

function FileRow({
  file,
  checked,
  onToggle,
  onDelete,
  deleting,
}: {
  file: FileMeta
  checked: boolean
  onToggle: () => void
  onDelete: () => void
  deleting: boolean
}) {
  const t = useT()
  const [confirmDelete, setConfirmDelete] = useState(false)
  const Icon = iconForFilename(file.filename)
  return (
    <li
      className={cn(
        "group flex items-start gap-2 rounded-md border px-2 py-1.5 text-xs transition-colors",
        checked ? "border-border bg-card/60" : "border-border/50 bg-card/30",
      )}
    >
      <input
        type="checkbox"
        checked={checked}
        onChange={onToggle}
        className="mt-1 h-3.5 w-3.5 shrink-0 accent-primary"
        title={t(checked ? "library.fileInScope" : "library.fileOutOfScope")}
      />
      <Icon className="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted-foreground" />
      <div className="min-w-0 flex-1">
        <div className="truncate font-medium" title={file.filename}>
          {file.filename}
        </div>
        <div className="text-[10px] text-muted-foreground">
          {file.chunk_count} chunk{file.chunk_count === 1 ? "" : "s"}
          {file.file_size > 0 && (
            <> · {formatSize(file.file_size)}</>
          )}
          {file.added_at > 0 && (
            <> · {formatAdded(file.added_at)}</>
          )}
          {file.source_kind !== "unknown" && (
            <> · {file.source_kind}</>
          )}
        </div>
      </div>
      {confirmDelete ? (
        <div className="flex shrink-0 items-center gap-0.5">
          <button
            type="button"
            onClick={() => {
              setConfirmDelete(false)
              onDelete()
            }}
            disabled={deleting}
            className="rounded p-0.5 text-destructive hover:bg-destructive/10"
            title={t("library.deleteRowConfirm")}
          >
            {deleting ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <Trash2 className="h-3.5 w-3.5" />
            )}
          </button>
          <button
            type="button"
            onClick={() => setConfirmDelete(false)}
            className="rounded p-0.5 text-muted-foreground hover:bg-accent"
            title={t("library.deleteRowCancel")}
          >
            <XCircle className="h-3.5 w-3.5" />
          </button>
        </div>
      ) : (
        <button
          type="button"
          onClick={() => setConfirmDelete(true)}
          className="shrink-0 rounded p-0.5 text-muted-foreground opacity-0 transition-opacity hover:text-destructive group-hover:opacity-100"
          title={t("library.removeDocument")}
        >
          <Trash2 className="h-3.5 w-3.5" />
        </button>
      )}
    </li>
  )
}

// ---------------------------------------------------------------------------

function DeleteAllButton({
  onConfirm,
  pending,
}: {
  onConfirm: () => void
  pending: boolean
}) {
  const t = useT()
  const [armed, setArmed] = useState(false)
  if (!armed) {
    return (
      <button
        type="button"
        onClick={() => setArmed(true)}
        className="mt-2 w-full text-center text-[10px] text-muted-foreground/70 hover:text-destructive"
      >
        {t("library.clearAll")}
      </button>
    )
  }
  return (
    <div className="mt-2 flex gap-1">
      <Button
        size="sm"
        variant="destructive"
        className="flex-1 text-xs"
        onClick={() => {
          setArmed(false)
          onConfirm()
        }}
        disabled={pending}
      >
        {pending ? (
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
        ) : (
          <Trash2 className="h-3.5 w-3.5" />
        )}
        {t("common.confirmDelete")}
      </Button>
      <Button
        size="sm"
        variant="outline"
        className="text-xs"
        onClick={() => setArmed(false)}
      >
        {t("common.cancel")}
      </Button>
    </div>
  )
}

// ---------------------------------------------------------------------------

function PasteTextModal({
  onClose,
  onSubmit,
  pending,
}: {
  onClose: () => void
  onSubmit: (v: { text: string; filename: string }) => void
  pending: boolean
}) {
  const t = useT()
  const [text, setText] = useState("")
  const [filename, setFilename] = useState("")
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  useEffect(() => {
    textareaRef.current?.focus()
    const onEsc = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose()
    }
    window.addEventListener("keydown", onEsc)
    return () => window.removeEventListener("keydown", onEsc)
  }, [onClose])

  const canSubmit = text.trim().length > 0 && !pending

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose()
      }}
    >
      <div className="flex h-[min(80vh,640px)] w-full max-w-2xl flex-col overflow-hidden rounded-lg border bg-background shadow-xl">
        <div className="flex items-center justify-between border-b px-4 py-2.5">
          <div className="text-sm font-medium">{t("library.pasteModalTitle")}</div>
          <button
            type="button"
            onClick={onClose}
            className="rounded p-1 text-muted-foreground hover:bg-accent"
            title={t("pdf.close")}
          >
            <XCircle className="h-4 w-4" />
          </button>
        </div>
        <div className="space-y-2 border-b p-3">
          <input
            type="text"
            value={filename}
            onChange={(e) => setFilename(e.target.value)}
            placeholder={t("library.pasteFilenamePlaceholder")}
            className="w-full rounded-md border bg-card px-2.5 py-1.5 text-xs outline-none focus:ring-1 focus:ring-ring"
          />
        </div>
        <textarea
          ref={textareaRef}
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder={t("library.pasteContentPlaceholder")}
          className="min-h-0 flex-1 resize-none border-0 bg-transparent p-3 text-sm outline-none placeholder:text-muted-foreground"
        />
        <div className="flex items-center justify-between border-t px-3 py-2.5">
          <div className="text-[10px] text-muted-foreground">
            {text.length.toLocaleString()}
          </div>
          <div className="flex items-center gap-2">
            <Button size="sm" variant="ghost" onClick={onClose}>
              {t("common.cancel")}
            </Button>
            <Button
              size="sm"
              disabled={!canSubmit}
              onClick={() => {
                onSubmit({ text, filename: filename.trim() })
                onClose()
              }}
            >
              {pending && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
              {t("library.addToLibrary")}
            </Button>
          </div>
        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------

function Skeleton() {
  return (
    <ul className="space-y-1">
      {[0, 1, 2].map((i) => (
        <li
          key={i}
          className="h-10 animate-pulse rounded-md bg-muted/40"
          style={{ animationDelay: `${i * 80}ms` }}
        />
      ))}
    </ul>
  )
}

function EmptyHint() {
  const t = useT()
  return (
    <p className="px-1 text-[11px] leading-snug text-muted-foreground">
      {t("library.empty")}
    </p>
  )
}

function ProgressIcon({ status }: { status: string }) {
  if (status === "indexed")
    return <CheckCircle2 className="mt-0.5 h-3 w-3 shrink-0 text-emerald-500" />
  if (status === "error")
    return <XCircle className="mt-0.5 h-3 w-3 shrink-0 text-destructive" />
  return (
    <Loader2 className="mt-0.5 h-3 w-3 shrink-0 animate-spin text-muted-foreground" />
  )
}

// ---------------------------------------------------------------------------
// Formatting helpers
// ---------------------------------------------------------------------------

function iconForFilename(name: string) {
  const lower = name.toLowerCase()
  if (/\.(jpe?g|png|tiff?|bmp)$/i.test(lower)) return ImageIcon
  return FileText
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

function formatAdded(epoch: number): string {
  if (!epoch) return ""
  const now = Date.now() / 1000
  const diff = now - epoch
  if (diff < 60) return "just now"
  if (diff < 3_600) return `${Math.floor(diff / 60)}m ago`
  if (diff < 86_400) return `${Math.floor(diff / 3_600)}h ago`
  if (diff < 7 * 86_400) return `${Math.floor(diff / 86_400)}d ago`
  const d = new Date(epoch * 1_000)
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" })
}
