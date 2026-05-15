/**
 * v0.5.0 — PDF citation viewer.
 *
 * Renders one PDF page at a time on a canvas via pdf.js. The text
 * layer is overlaid as transparent absolutely-positioned divs so a
 * future chunk-highlight pass (commit 8) can paint over the right
 * spans. The viewer is mounted from the Sources drawer when the user
 * clicks an "open in viewer" button on a citation card -- not from
 * the chip row, which stays a lightweight quick-peek.
 *
 * pdf.js is browser-only, async, and pulls in a Web Worker. We init
 * the worker once per module load, then keep the loaded document in
 * a ref so flipping pages doesn't refetch / reparse the PDF.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import {
  ChevronLeft,
  ChevronRight,
  Loader2,
  Minus,
  Plus,
  X,
} from "lucide-react"
import * as pdfjsLib from "pdfjs-dist"
// Vite resolves `?url` imports to a static asset URL the worker
// can fetch at runtime. Bundling the worker as ESM keeps it on the
// same origin so we don't have to relax COEP / cross-origin checks.
import pdfWorkerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url"
import type {
  PDFDocumentProxy,
  PDFPageProxy,
} from "pdfjs-dist/types/src/display/api"

import { Button } from "@/components/ui/button"
import { highlightChunkInTextLayer } from "@/lib/pdf-highlight"
import { cn } from "@/lib/utils"

// Configure the worker once. The check guards against re-assignment
// during React strict-mode double-invocation in dev.
if (!pdfjsLib.GlobalWorkerOptions.workerSrc) {
  pdfjsLib.GlobalWorkerOptions.workerSrc = pdfWorkerUrl
}

interface PdfChunkViewerProps {
  filename: string
  /** Page to land on when the viewer mounts. 1-indexed; clamps to the
   *  document bounds. PDFs without explicit page metadata may have
   *  this set to null, in which case we open at page 1. */
  initialPage?: number | null
  /** The chunk's snippet text, kept for the highlight pass (commit 8).
   *  Stored on a ref-stub today so the prop already exists when the
   *  highlight overlay arrives. */
  chunkText?: string
  onClose: () => void
}

// Zoom presets the user can step through. The renderer multiplies the
// PDF's natural CSS size by this factor; we don't try to match the
// page's exact pixel density (devicePixelRatio handling is separate
// below) because the user perceives zoom as a relative size knob.
const ZOOM_STOPS = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0]

export function PdfChunkViewer({
  filename,
  initialPage,
  chunkText,
  onClose,
}: PdfChunkViewerProps) {
  const [doc, setDoc] = useState<PDFDocumentProxy | null>(null)
  const [pageNum, setPageNum] = useState<number>(initialPage || 1)
  const [zoomIdx, setZoomIdx] = useState<number>(2) // 1.0
  const [error, setError] = useState<string | null>(null)
  const [rendering, setRendering] = useState(false)

  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const textLayerRef = useRef<HTMLDivElement | null>(null)
  // Cancel token for the current render pass — when the user changes
  // page or zoom mid-render we abort the pdf.js render task so the new
  // pass doesn't paint a half-stale canvas.
  const renderTaskRef = useRef<ReturnType<PDFPageProxy["render"]> | null>(null)

  // Esc closes the viewer. Wire onto window so it works even when the
  // focus is inside the canvas / text layer.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation()
        onClose()
      } else if (e.key === "ArrowLeft") {
        setPageNum((p) => Math.max(1, p - 1))
      } else if (e.key === "ArrowRight") {
        setPageNum((p) => (doc ? Math.min(doc.numPages, p + 1) : p))
      }
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [doc, onClose])

  // Load the PDF when filename changes. pdf.js returns a "loadingTask"
  // whose .destroy() cancels an in-flight parse — important when the
  // user closes the modal before the bytes finish streaming.
  useEffect(() => {
    let cancelled = false
    setDoc(null)
    setError(null)
    const url = `/api/documents/${encodeURIComponent(filename)}/raw`
    const task = pdfjsLib.getDocument({ url, withCredentials: false })
    task.promise.then(
      (loaded) => {
        if (cancelled) {
          loaded.destroy()
          return
        }
        setDoc(loaded)
        // Clamp initial page to the document's actual bounds. PDFs
        // with chapter-style "page" metadata (we use 1-indexed chapter
        // for .epub) sometimes claim pages beyond the real count.
        setPageNum((p) => Math.min(Math.max(1, p), loaded.numPages))
      },
      (err) => {
        if (cancelled) return
        const msg = err instanceof Error ? err.message : String(err)
        setError(msg)
      },
    )
    return () => {
      cancelled = true
      task.destroy()
    }
  }, [filename])

  // (Re-)render the current page when the doc, pageNum, or zoom changes.
  useEffect(() => {
    if (!doc) return
    let cancelled = false
    setRendering(true)
    setError(null)

    const renderPage = async () => {
      try {
        const page = await doc.getPage(pageNum)
        if (cancelled) return

        // Cancel the prior render task (if any) before starting a new
        // one. pdf.js will throw a "Rendering cancelled" promise
        // rejection on the aborted task which we swallow below.
        if (renderTaskRef.current) {
          try {
            renderTaskRef.current.cancel()
          } catch {
            /* ignore */
          }
        }

        const canvas = canvasRef.current
        const textLayer = textLayerRef.current
        if (!canvas || !textLayer) return

        const scale = ZOOM_STOPS[zoomIdx]
        const viewport = page.getViewport({ scale })

        // High-DPI screens: render at device pixels for sharpness, then
        // CSS-size down to the logical viewport. Without this, retina /
        // 4K monitors render fuzzy.
        const outputScale = window.devicePixelRatio || 1
        const ctx = canvas.getContext("2d")
        if (!ctx) return
        canvas.width = Math.floor(viewport.width * outputScale)
        canvas.height = Math.floor(viewport.height * outputScale)
        canvas.style.width = `${Math.floor(viewport.width)}px`
        canvas.style.height = `${Math.floor(viewport.height)}px`
        const transform: number[] | undefined =
          outputScale !== 1
            ? [outputScale, 0, 0, outputScale, 0, 0]
            : undefined

        const task = page.render({
          canvasContext: ctx,
          viewport,
          transform,
          canvas,
        } as Parameters<PDFPageProxy["render"]>[0])
        renderTaskRef.current = task
        try {
          await task.promise
        } catch (err) {
          // pdf.js raises this when we call cancel() above; not an
          // actual error from the user's perspective.
          const msg = err instanceof Error ? err.message : String(err)
          if (!msg.includes("cancelled")) throw err
          return
        }
        if (cancelled) return

        // Build the text layer. Each pdf.js "text item" becomes one
        // absolutely-positioned span sized to match what was painted on
        // the canvas. Commit 8 will use these spans as the target for
        // the chunk-text highlight overlay; for now they're invisible
        // (transparent fill) but selectable, so the user can copy text
        // out of the rendered page if they want.
        textLayer.innerHTML = ""
        textLayer.style.width = `${Math.floor(viewport.width)}px`
        textLayer.style.height = `${Math.floor(viewport.height)}px`
        const textContent = await page.getTextContent()
        if (cancelled) return
        // pdf.js v5 exposes a TextLayer class (older builds had a
        // free renderTextLayer function). Positioning + line breaks
        // come straight from the engine so the overlay aligns with
        // whatever the canvas painted, even with rotated / italic
        // glyphs. Falls back to a no-op if the build is missing it.
        const TextLayerCls = (
          pdfjsLib as unknown as {
            TextLayer?: new (opts: {
              textContentSource: unknown
              container: HTMLElement
              viewport: unknown
            }) => { render(): Promise<void> }
          }
        ).TextLayer
        if (TextLayerCls) {
          const tl = new TextLayerCls({
            textContentSource: textContent,
            container: textLayer,
            viewport,
          })
          await tl.render()
        }

        // Highlight the chunk inside the rendered text layer. Done
        // AFTER the text layer renders so the spans are in the DOM;
        // if no match is found (different page, aggressive Docling
        // reflow), the helper silently returns 0 -- the page still
        // displays normally, just without the highlight overlay.
        if (chunkText && textLayer) {
          try {
            // v0.6.0 uses the paragraph-anchor class (subtle vertical
            // bar + faint tint) instead of v0.5.0's full yellow wash
            // -- citations are now sentence-level so the smaller
            // visual cue is more appropriate.
            const tagged = highlightChunkInTextLayer(
              textLayer, chunkText, "paragraphRef",
            )
            if (tagged === 0) {
              // No-op for the user -- but log it so a future
              // quality-pass can spot which docs hit this path
              // consistently (likely candidates for a different
              // chunking strategy).
              // eslint-disable-next-line no-console
              console.debug(
                "[PdfChunkViewer] no fuzzy match for chunk on page",
                pageNum,
              )
            } else {
              // Scroll the first highlighted span into view so the
              // user doesn't have to hunt for it on a long page.
              const first = textLayer.querySelector<HTMLElement>(
                ".paragraphRef",
              )
              if (first) {
                first.scrollIntoView({
                  behavior: "smooth",
                  block: "center",
                  inline: "nearest",
                })
              }
            }
          } catch (err) {
            // Highlight failures are non-fatal -- swallow to keep
            // the viewer usable.
            // eslint-disable-next-line no-console
            console.debug("[PdfChunkViewer] highlight failed:", err)
          }
        }
      } catch (err) {
        if (!cancelled) {
          const msg = err instanceof Error ? err.message : String(err)
          if (!msg.toLowerCase().includes("cancelled")) {
            setError(msg)
          }
        }
      } finally {
        if (!cancelled) setRendering(false)
      }
    }

    void renderPage()

    return () => {
      cancelled = true
      if (renderTaskRef.current) {
        try {
          renderTaskRef.current.cancel()
        } catch {
          /* ignore */
        }
      }
    }
  }, [doc, pageNum, zoomIdx])

  // Cleanup the loaded document when the viewer unmounts. pdf.js holds
  // onto the worker-side transport otherwise.
  useEffect(() => {
    return () => {
      if (doc) {
        void doc.destroy()
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const numPages = doc?.numPages ?? 0
  const onPrev = useCallback(
    () => setPageNum((p) => Math.max(1, p - 1)),
    [],
  )
  const onNext = useCallback(
    () => setPageNum((p) => (doc ? Math.min(doc.numPages, p + 1) : p)),
    [doc],
  )

  const pageInputValue = useMemo(() => String(pageNum), [pageNum])

  return (
    <div
      className="fixed inset-0 z-50 flex flex-col bg-black/70 backdrop-blur-sm"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose()
      }}
    >
      {/* Top bar */}
      <div className="flex items-center gap-2 border-b border-white/10 bg-background/95 px-3 py-2 text-sm">
        <span className="truncate font-mono text-xs text-muted-foreground">
          {filename}
        </span>
        <div className="ml-auto flex items-center gap-1">
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            onClick={onPrev}
            disabled={!doc || pageNum <= 1}
            title="Previous page (←)"
          >
            <ChevronLeft className="h-4 w-4" />
          </Button>
          <div className="flex items-center gap-1 text-xs">
            <input
              type="number"
              className="h-7 w-12 rounded border bg-background px-1 text-center font-mono text-xs"
              value={pageInputValue}
              onChange={(e) => {
                const n = parseInt(e.target.value, 10)
                if (!Number.isFinite(n)) return
                if (!doc) return
                setPageNum(Math.max(1, Math.min(doc.numPages, n)))
              }}
              disabled={!doc}
              min={1}
              max={numPages || 1}
            />
            <span className="text-muted-foreground">/ {numPages || "?"}</span>
          </div>
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            onClick={onNext}
            disabled={!doc || pageNum >= numPages}
            title="Next page (→)"
          >
            <ChevronRight className="h-4 w-4" />
          </Button>
          <div className="mx-1 h-5 w-px bg-border" />
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            onClick={() => setZoomIdx((i) => Math.max(0, i - 1))}
            disabled={zoomIdx <= 0}
            title="Zoom out"
          >
            <Minus className="h-4 w-4" />
          </Button>
          <span className="w-10 text-center font-mono text-xs text-muted-foreground">
            {Math.round(ZOOM_STOPS[zoomIdx] * 100)}%
          </span>
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            onClick={() =>
              setZoomIdx((i) => Math.min(ZOOM_STOPS.length - 1, i + 1))
            }
            disabled={zoomIdx >= ZOOM_STOPS.length - 1}
            title="Zoom in"
          >
            <Plus className="h-4 w-4" />
          </Button>
          <div className="mx-1 h-5 w-px bg-border" />
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            onClick={onClose}
            title="Close (Esc)"
          >
            <X className="h-4 w-4" />
          </Button>
        </div>
      </div>

      {/* Page surface. ``items-start`` + ``min-w-max`` on the inner
          wrapper lets the canvas scroll horizontally when zoomed past
          the viewport width, while still horizontally-centring it
          when it fits. ``py-6`` adds breathing room at top/bottom so
          the top of the page isn't pinned right under the toolbar. */}
      <div className="relative min-h-0 flex-1 overflow-auto bg-neutral-900/40">
        {error ? (
          <div className="mx-auto mt-12 max-w-md rounded-md border border-destructive/40 bg-destructive/10 px-4 py-3 text-sm text-destructive">
            Couldn't open PDF: {error}
          </div>
        ) : (
          <div className="flex min-h-full min-w-full items-start justify-center px-4 py-6">
            <div className="bg-white shadow-2xl dark:bg-neutral-200">
              <div className="relative">
                <canvas
                  ref={canvasRef}
                  className={cn(
                    "block transition-opacity",
                    rendering && "opacity-90",
                  )}
                />
                <div
                  ref={textLayerRef}
                  className="textLayer absolute inset-0 select-text"
                />
              </div>
            </div>
          </div>
        )}

        {!doc && !error && (
          <div className="absolute inset-0 flex items-center justify-center">
            <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
          </div>
        )}
      </div>
    </div>
  )
}
