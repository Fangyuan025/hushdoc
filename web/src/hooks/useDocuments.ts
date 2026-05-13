import { useCallback, useState } from "react"
import {
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query"
import { toast } from "sonner"

import {
  apiDeleteDocuments,
  apiDeleteOneDocument,
  apiListDocuments,
  apiPasteText,
} from "@/lib/api"

interface UploadFileStatus {
  filename: string
  status: "queued" | "uploading" | "indexed" | "error"
  chunks?: number
  summary?: string
  error?: string
}

interface UploadProgress {
  files: UploadFileStatus[]
  totalChunks: number
  done: boolean
}

const EMPTY_PROGRESS: UploadProgress = {
  files: [],
  totalChunks: 0,
  done: false,
}

/** Manages the documents list, delete-all, and the upload pipeline. */
export function useDocuments() {
  const qc = useQueryClient()

  const list = useQuery({
    queryKey: ["documents"],
    queryFn: apiListDocuments,
    refetchInterval: 30_000,
  })

  const del = useMutation({
    mutationFn: apiDeleteDocuments,
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ["documents"] })
      toast.success(
        data.was_count > 0
          ? `Cleared ${data.was_count} chunks from the vector store.`
          : "Vector store was already empty.",
      )
    },
    onError: (err) => toast.error(`Wipe failed: ${err.message}`),
  })

  /** Delete a single indexed file by name. v0.2.0. */
  const delOne = useMutation({
    mutationFn: (filename: string) => apiDeleteOneDocument(filename),
    onSuccess: (data, filename) => {
      qc.invalidateQueries({ queryKey: ["documents"] })
      if (data.removed_chunks > 0) {
        toast.success(`Removed ${filename} (${data.removed_chunks} chunks).`)
      } else {
        toast.info(`${filename} was not indexed.`)
      }
    },
    onError: (err, filename) =>
      toast.error(`Couldn't delete ${filename}: ${err.message}`),
  })

  /** Ingest raw pasted text. Returns the server-assigned filename. v0.2.0. */
  const pasteText = useMutation({
    mutationFn: ({ text, filename }: { text: string; filename?: string }) =>
      apiPasteText(text, filename),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ["documents"] })
      toast.success(`Added "${data.filename}" (${data.chunks} chunks).`)
    },
    onError: (err) => toast.error(`Paste failed: ${err.message}`),
  })

  const [progress, setProgress] = useState<UploadProgress>(EMPTY_PROGRESS)
  const [uploading, setUploading] = useState(false)
  const [uploadError, setUploadError] = useState<string | null>(null)

  const upload = useCallback(
    async (
      files: File[],
      replace: boolean,
      sourceKind: "uploaded" | "folder" = "uploaded",
    ) => {
      if (files.length === 0 || uploading) return
      setUploading(true)
      setUploadError(null)
      setProgress({
        files: files.map((f) => ({ filename: f.name, status: "queued" })),
        totalChunks: 0,
        done: false,
      })

      try {
        const fd = new FormData()
        for (const f of files) fd.append("files", f, f.name)
        fd.append("replace", String(replace))
        // sourceKind currently isn't read by the backend's upload route
        // (it stamps 'uploaded' for everything multipart) but it's still
        // worth threading through here so a future server-side handler
        // can distinguish folder picks from drag-drops without a new API.
        fd.append("source_kind", sourceKind)

        const resp = await fetch("/api/documents/upload", {
          method: "POST",
          body: fd,
        })
        if (!resp.ok) throw new Error(`upload -> ${resp.status}`)
        if (!resp.body) throw new Error("upload response has no body")

        const reader = resp.body.getReader()
        const decoder = new TextDecoder("utf-8")
        let buf = ""
        let curEvent = "message"
        let curData: string[] = []

        const flush = () => {
          if (!curData.length) {
            curEvent = "message"
            return
          }
          let payload: unknown = curData.join("\n")
          try {
            payload = JSON.parse(payload as string)
          } catch {
            /* keep as string */
          }
          handleSseEvent(curEvent, payload)
          curEvent = "message"
          curData = []
        }

        const handleSseEvent = (ev: string, payload: unknown) => {
          if (ev === "file_done") {
            const p = payload as {
              filename: string
              chunks: number
              summary: string
            }
            setProgress((old) => ({
              ...old,
              files: old.files.map((f) =>
                f.filename === p.filename
                  ? {
                      ...f,
                      status: "indexed",
                      chunks: p.chunks,
                      summary: p.summary,
                    }
                  : f,
              ),
              totalChunks: old.totalChunks + p.chunks,
            }))
          } else if (ev === "file_error") {
            const p = payload as { filename: string; error: string }
            setProgress((old) => ({
              ...old,
              files: old.files.map((f) =>
                f.filename === p.filename
                  ? { ...f, status: "error", error: p.error }
                  : f,
              ),
            }))
          } else if (ev === "all_done") {
            const p = payload as {
              succeeded: number
              total: number
              total_chunks: number
            }
            setProgress((old) => ({ ...old, done: true }))
            if (p.succeeded === p.total) {
              toast.success(
                `Indexed ${p.total} file(s) (${p.total_chunks} chunks).`,
              )
            } else {
              toast.warning(
                `Indexed ${p.succeeded}/${p.total} files. Some failed.`,
              )
            }
          }
        }

        while (true) {
          const { value, done } = await reader.read()
          if (done) break
          buf += decoder.decode(value, { stream: true })
          let nl: number
          while ((nl = buf.indexOf("\n")) !== -1) {
            const raw = buf.slice(0, nl).replace(/\r$/, "")
            buf = buf.slice(nl + 1)
            if (raw === "") flush()
            else if (raw.startsWith(":")) continue
            else if (raw.startsWith("event:")) curEvent = raw.slice(6).trim()
            else if (raw.startsWith("data:")) curData.push(raw.slice(5).replace(/^ /, ""))
          }
        }
        flush()
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err)
        setUploadError(msg)
        toast.error(`Upload failed: ${msg}`)
      } finally {
        setUploading(false)
        // refresh the list so newly indexed files show up immediately
        qc.invalidateQueries({ queryKey: ["documents"] })
      }
    },
    [uploading, qc],
  )

  const dismissProgress = useCallback(() => setProgress(EMPTY_PROGRESS), [])

  return {
    list,
    del,
    delOne,
    pasteText,
    upload,
    uploading,
    uploadError,
    progress,
    dismissProgress,
  }
}
