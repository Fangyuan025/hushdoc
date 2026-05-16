import React, { useMemo, useState } from "react"
import {
  Check,
  ChevronLeft,
  ChevronRight,
  Copy,
  Loader2,
  RefreshCw,
  Sparkles,
  User,
  Volume2,
} from "lucide-react"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import remarkMath from "remark-math"
import rehypeKatex from "rehype-katex"

import { cn } from "@/lib/utils"
import type { ChatMessage as Msg, ParagraphBinding } from "@/types"

import { CitationChip } from "./CitationChip"
import { PdfChunkViewer } from "./PdfChunkViewer"
import { Sources } from "./Sources"

// Inserting the cursor character into the streaming content directly
// (rather than via a CSS ::after on the prose container) puts it inline
// at the actual last text position — even mid-paragraph, mid-list, or
// mid-code-block — which is what the user expects from a typewriter.
const STREAMING_CURSOR = "▍"

/** Loading indicator shown in the assistant bubble while it's streaming
 *  but no tokens have arrived yet. Cold-start latency is masked by the
 *  warmup ping in App.tsx, so a single neutral status line is all the
 *  user needs here. */
function StreamingPlaceholder() {
  return (
    <div className="flex items-center gap-2 text-muted-foreground">
      <Loader2 className="h-3.5 w-3.5 animate-spin" />
      <span className="text-sm">Generating…</span>
    </div>
  )
}

interface ChatMessageProps {
  msg: Msg
  /** Optional handler for the 🔊 replay icon next to the assistant
   *  bubble. Receives the cached blob URL to re-play. */
  onReplay?: (audioUrl: string) => void
  /** v0.2.0: regenerate this answer. v0.5.0 attaches the new answer
   *  as an additional variant on the same bubble; the user navigates
   *  between variants via the < N/M > pager. Falsy = button hidden. */
  onRegenerate?: () => void
  /** v0.5.0: switch the active variant when the user clicks the pager
   *  arrows. Receives the new (clamped) variant index. */
  onSwitchVariant?: (variantIndex: number) => void
  /** v0.6.0: open the full source document in a side panel, scrolled
   *  to the paragraph that backs this citation. Wired by ChatPane to
   *  the View-source side-panel state. */
  onOpenSource?: (binding: ParagraphBinding) => void
}

// v0.6.0: walk markdown-rendered children, replace [N] text with
// hover citation chips AND wrap ungrounded-sentence ranges in a
// .ungrounded span (wavy amber underline). Recursive so it works
// inside <strong>, <em>, list items, table cells, etc. Code blocks
// are left alone (we avoid injecting into `code` / `pre`).
const CITATION_PATTERN = /\[(\d{1,3})\]/g

type Marker =
  | { type: "chip"; start: number; end: number; id: number }
  | { type: "ungrounded"; start: number; end: number; text: string }

function renderWithCitations(
  children: React.ReactNode,
  bindings: Map<number, ParagraphBinding>,
  ungroundedSentences: string[],
  onOpenSource?: (b: ParagraphBinding) => void,
): React.ReactNode {
  return React.Children.map(children, (child, childIdx) => {
    if (typeof child === "string") {
      const markers: Marker[] = []
      // 1) chip markers
      let m: RegExpExecArray | null
      CITATION_PATTERN.lastIndex = 0
      while ((m = CITATION_PATTERN.exec(child)) !== null) {
        markers.push({
          type: "chip",
          start: m.index,
          end: m.index + m[0].length,
          id: parseInt(m[1], 10),
        })
      }
      // 2) ungrounded-sentence markers. Each sentence's text is a
      // substring of the answer; we look for it in this text node. A
      // sentence may not span this text node (markdown chunking) — if
      // so the indexOf returns -1 and we skip. Wrap each match.
      for (const sentText of ungroundedSentences) {
        if (!sentText) continue
        let from = 0
        while (true) {
          const idx = child.indexOf(sentText, from)
          if (idx === -1) break
          markers.push({
            type: "ungrounded",
            start: idx,
            end: idx + sentText.length,
            text: sentText,
          })
          from = idx + sentText.length
        }
      }
      if (markers.length === 0) return child
      // Sort by start; resolve same-start ties favouring ungrounded
      // wrappers (they're outer) over chips.
      markers.sort((a, b) =>
        a.start - b.start || (a.type === "ungrounded" ? -1 : 1),
      )
      // Drop overlapping markers preferring the earlier one.
      const kept: Marker[] = []
      let furthest = 0
      for (const mk of markers) {
        if (mk.start < furthest) continue
        kept.push(mk)
        furthest = mk.end
      }
      const parts: React.ReactNode[] = []
      let cursor = 0
      for (const mk of kept) {
        if (mk.start > cursor) parts.push(child.slice(cursor, mk.start))
        if (mk.type === "chip") {
          parts.push(
            <CitationChip
              key={`cit-${childIdx}-${mk.start}`}
              id={mk.id}
              binding={bindings.get(mk.id)}
              onOpenSource={onOpenSource}
            />,
          )
        } else {
          parts.push(
            <span
              key={`ung-${childIdx}-${mk.start}`}
              className="ungrounded"
              title="This sentence couldn't be matched to a specific source — double-check it"
            >
              {mk.text}
            </span>,
          )
        }
        cursor = mk.end
      }
      if (cursor < child.length) parts.push(child.slice(cursor))
      return parts
    }
    if (React.isValidElement(child)) {
      const tag = (child.type as string) || ""
      if (tag === "code" || tag === "pre" || tag === "a") return child
      const props = child.props as { children?: React.ReactNode }
      if (props.children !== undefined) {
        return React.cloneElement(child, {
          ...props,
          children: renderWithCitations(
            props.children, bindings, ungroundedSentences, onOpenSource,
          ),
        } as React.HTMLAttributes<HTMLElement>)
      }
    }
    return child
  })
}

/** One chat bubble. Assistant supports markdown + code + GFM tables. */
export function ChatMessage({
  msg,
  onReplay,
  onRegenerate,
  onSwitchVariant,
  onOpenSource: onOpenSourceExternal,
}: ChatMessageProps) {
  const isUser = msg.role === "user"
  const [copied, setCopied] = useState(false)
  // v0.6.0: clicking "View source →" in a citation popover mounts the
  // PDF viewer modal. State is local to the message bubble so multiple
  // messages can have their own viewer state without colliding.
  const [viewingSource, setViewingSource] =
    useState<ParagraphBinding | null>(null)
  const openSource = (b: ParagraphBinding) => {
    // Parent can override (e.g. ChatPane might want a side panel
    // instead of modal); fall back to our local modal otherwise.
    if (onOpenSourceExternal) onOpenSourceExternal(b)
    else setViewingSource(b)
  }

  // v0.6.0: flatten sentence_bindings into a prompt_id -> paragraph
  // map so the citation chip renderer can look up bindings in O(1).
  // The same paragraph may appear in multiple sentences (e.g. a chunk
  // cited from both sentence 2 and sentence 5); we keep the first
  // occurrence — they should be identical anyway.
  // v0.6.1: chitchat replies skip the binding map entirely so any [N]
  // tokens that leak through from the model (echoing prompt examples)
  // don't render as broken chips on a greeting / thank-you reply.
  const bindingsById = useMemo(() => {
    const map = new Map<number, ParagraphBinding>()
    if (msg.chitchat) return map
    for (const sb of msg.sentenceBindings || []) {
      for (const p of sb.paragraphs) {
        if (!map.has(p.prompt_id)) map.set(p.prompt_id, p)
      }
    }
    return map
  }, [msg.sentenceBindings, msg.chitchat])

  // v0.6.1: when several adjacent sentences cite the SAME [N] (same
  // set, in the same order), the chips get repetitive -- a paragraph
  // of "... finding A [2]. finding B [2]. finding C [2]." reads
  // cleaner as "... finding A. finding B. finding C [2]." We only
  // keep the chip on the LAST sentence in each run. The underlying
  // sentence_bindings list is unchanged so the popover for the run-
  // closing chip still surfaces all the bound paragraphs; we just
  // de-duplicate the visual marker.
  const displayContent = useMemo(() => {
    if (msg.chitchat) return msg.content
    const bindings = msg.sentenceBindings || []
    if (bindings.length < 2) return msg.content
    const sameCitations = (a: number[], b: number[]) =>
      a.length === b.length && a.every((v, i) => v === b[i])
    // A sentence's chip(s) should be stripped iff the next sentence
    // cites exactly the same set of ids. Walking forward.
    const stripRanges: Array<{ start: number; end: number }> = []
    for (let i = 0; i < bindings.length - 1; i++) {
      const cur = bindings[i]
      const next = bindings[i + 1]
      if (cur.citations.length === 0) continue
      if (sameCitations(cur.citations, next.citations)) {
        stripRanges.push({ start: cur.start, end: cur.end })
      }
    }
    if (stripRanges.length === 0) return msg.content
    // Apply in reverse so offsets stay valid.
    stripRanges.sort((a, b) => b.start - a.start)
    let text = msg.content
    for (const r of stripRanges) {
      const head = text.slice(0, r.start)
      const range = text.slice(r.start, r.end)
      const tail = text.slice(r.end)
      // Strip the [N] tags AND the single space that typically
      // precedes them ("...claim [2]." -> "...claim.").
      const cleaned = range.replace(/ ?\[\d{1,3}\]/g, "")
      text = head + cleaned + tail
    }
    return text
  }, [msg.content, msg.sentenceBindings, msg.chitchat])

  // v0.6.0: sentences whose binding pass returned neither a model [N]
  // nor an auto-injected one. These are pure synthesis / connector
  // sentences OR potentially fabricated claims -- we underline them
  // with a wavy amber line so the user knows to verify.
  // v0.6.1: chitchat skips ungrounded marking too; greetings don't
  // need a "double-check this" hint.
  const ungroundedSentences = useMemo(() => {
    const out: string[] = []
    if (msg.chitchat) return out
    for (const sb of msg.sentenceBindings || []) {
      // Skip short discourse markers ("In summary,") -- the binding
      // pass already excluded them from auto-citation so they'd be
      // flagged ungrounded by definition, which is too noisy.
      if (
        sb.citations.length === 0
        && sb.paragraphs.length === 0
        && sb.text.length >= 30
      ) {
        out.push(sb.text)
      }
    }
    return out
  }, [msg.sentenceBindings, msg.chitchat])

  const onCopy = async () => {
    try {
      await navigator.clipboard.writeText(msg.content)
      setCopied(true)
      setTimeout(() => setCopied(false), 1200)
    } catch {
      /* clipboard might be blocked in some embeds; silently no-op */
    }
  }

  const showActionsRow =
    !isUser && !msg.streaming && msg.content && msg.content.length > 0

  return (
    <div className={cn("flex gap-3", isUser && "justify-end")}>
      {!isUser && (
        <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-primary/10 text-primary">
          <Sparkles className="h-4 w-4" />
        </div>
      )}
      <div className={cn("max-w-[78ch] min-w-0", isUser && "order-1")}>
        <div
          className={cn(
            "text-[15px] leading-relaxed",
            isUser
              ? "rounded-2xl bg-primary px-4 py-2.5 text-primary-foreground"
              : "py-0.5",
          )}
        >
          {isUser ? (
            <div className="whitespace-pre-wrap break-words">{msg.content}</div>
          ) : (
            <div className="prose prose-sm dark:prose-invert max-w-none [&_p]:my-1.5 [&_pre]:my-2 [&_.katex-display]:my-2">
              {msg.content ? (
                <ReactMarkdown
                  remarkPlugins={[remarkGfm, remarkMath]}
                  rehypePlugins={[rehypeKatex]}
                  components={{
                    // v0.6.0: rewrite [N] runs inside any block-level
                    // text container into hover citation chips. We
                    // override the common element types; nested
                    // children are walked recursively by
                    // renderWithCitations.
                    p: ({ node, children, ...rest }) => (
                      <p {...rest}>
                        {renderWithCitations(children, bindingsById, ungroundedSentences, openSource)}
                      </p>
                    ),
                    li: ({ node, children, ...rest }) => (
                      <li {...rest}>
                        {renderWithCitations(children, bindingsById, ungroundedSentences, openSource)}
                      </li>
                    ),
                    td: ({ node, children, ...rest }) => (
                      <td {...rest}>
                        {renderWithCitations(children, bindingsById, ungroundedSentences, openSource)}
                      </td>
                    ),
                    th: ({ node, children, ...rest }) => (
                      <th {...rest}>
                        {renderWithCitations(children, bindingsById, ungroundedSentences, openSource)}
                      </th>
                    ),
                    blockquote: ({ node, children, ...rest }) => (
                      <blockquote {...rest}>
                        {renderWithCitations(children, bindingsById, ungroundedSentences, openSource)}
                      </blockquote>
                    ),
                  }}
                >
                  {msg.streaming
                    ? msg.content + STREAMING_CURSOR
                    : displayContent}
                </ReactMarkdown>
              ) : msg.streaming ? (
                <StreamingPlaceholder />
              ) : null}
            </div>
          )}
        </div>

        {/* Sources / trace row (skip chitchat — no docs anyway). */}
        {!isUser && !msg.chitchat && (
          (msg.sources && msg.sources.length > 0) ||
          (msg.retrievalTrace && msg.retrievalTrace.length > 0)
        ) && (
          <Sources
            docs={msg.sources || []}
            standaloneQuery={msg.standaloneQuery}
            trace={msg.retrievalTrace}
            mode={msg.retrievalMode}
          />
        )}

        {/* v0.2.0 action row: regenerate / copy / replay-audio. Only
            renders on completed assistant messages so the streaming
            bubble doesn't flicker.

            v0.5.0: when the message has multiple regenerated variants,
            also render the < N/M > pager inline so the user can flip
            between answers. The pager is hidden while a regenerate is
            in flight (the streaming bubble already shows the in-progress
            variant as the active one). */}
        {showActionsRow && (
          <div className="mt-1 flex items-center gap-0.5 text-muted-foreground">
            {msg.variants && msg.variants.length > 1 && onSwitchVariant && (
              <VariantPager
                count={msg.variants.length}
                active={msg.activeVariant ?? 0}
                onSwitch={onSwitchVariant}
              />
            )}
            {onRegenerate && (
              <ActionBtn
                title="Regenerate answer"
                onClick={onRegenerate}
              >
                <RefreshCw className="h-3.5 w-3.5" />
              </ActionBtn>
            )}
            <ActionBtn
              title={copied ? "Copied!" : "Copy answer"}
              onClick={onCopy}
            >
              {copied ? (
                <Check className="h-3.5 w-3.5 text-emerald-500" />
              ) : (
                <Copy className="h-3.5 w-3.5" />
              )}
            </ActionBtn>
            {msg.audioUrl && onReplay && (
              <ActionBtn
                title="Replay audio"
                onClick={() => onReplay(msg.audioUrl!)}
              >
                <Volume2 className="h-3.5 w-3.5" />
              </ActionBtn>
            )}
          </div>
        )}
      </div>
      {isUser && (
        <div className="order-2 flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-secondary text-secondary-foreground">
          <User className="h-4 w-4" />
        </div>
      )}
      {/* v0.6.0 View-source modal. Lives at the message scope so two
          messages can each manage their own without state collision.
          v0.6.1: the inline popover already showed the user the
          paragraph excerpt. Once they open the full PDF page, an
          extra highlight overlay reads as redundant + cluttered, so
          we drop it — the viewer just navigates to the right page. */}
      {viewingSource && (
        <PdfChunkViewer
          filename={viewingSource.filename}
          initialPage={viewingSource.page ?? 1}
          onClose={() => setViewingSource(null)}
        />
      )}
    </div>
  )
}

function ActionBtn({
  title,
  onClick,
  disabled,
  children,
}: {
  title: string
  onClick: () => void
  disabled?: boolean
  children: React.ReactNode
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={title}
      disabled={disabled}
      className="rounded-md p-1 transition-colors hover:bg-accent hover:text-foreground disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-transparent"
    >
      {children}
    </button>
  )
}

/** v0.5.0 pager for stepping through regenerated answer variants.
 *  Modelled on ChatGPT's "< N/M >" control: the centre label shows the
 *  1-indexed position; arrows clamp at either end. */
function VariantPager({
  count,
  active,
  onSwitch,
}: {
  count: number
  active: number
  onSwitch: (idx: number) => void
}) {
  const atStart = active <= 0
  const atEnd = active >= count - 1
  return (
    <div className="mr-0.5 flex items-center gap-0 rounded-md text-xs">
      <ActionBtn
        title="Previous answer"
        disabled={atStart}
        onClick={() => !atStart && onSwitch(active - 1)}
      >
        <ChevronLeft className="h-3.5 w-3.5" />
      </ActionBtn>
      <span className="px-0.5 tabular-nums text-[11px] text-muted-foreground">
        {active + 1}/{count}
      </span>
      <ActionBtn
        title="Next answer"
        disabled={atEnd}
        onClick={() => !atEnd && onSwitch(active + 1)}
      >
        <ChevronRight className="h-3.5 w-3.5" />
      </ActionBtn>
    </div>
  )
}
