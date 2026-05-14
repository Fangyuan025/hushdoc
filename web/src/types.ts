/** API + chat types shared across hooks and components. */

export interface SourceDoc {
  filename: string
  page: number | null
  headings: string
  snippet: string
}

export interface HealthResponse {
  ok: boolean
  /** App version (read from /VERSION). 'dev' when no VERSION file is present. */
  version: string
  chain_loaded: boolean
  store_loaded: boolean
  vector_count: number
  indexed_files: string[]
}

/** v0.3.0 Settings page. Mirrors server/schemas.AppConfigResponse. */
export interface AppConfig {
  model_path: string
  auto_cleanup_on_exit: boolean
  /** Server-side check: does model_path point at an actual .gguf? Saves the
   *  frontend from doing FS work; if false, we render a red status pip. */
  model_path_valid: boolean
}

export interface FileMeta {
  filename: string
  chunk_count: number
  /** Bytes; 0 for files indexed before v0.2.0 or for typed-text items. */
  file_size: number
  /** Epoch seconds; 0 for files indexed before v0.2.0. */
  added_at: number
  /** uploaded · folder · typed · unknown */
  source_kind: "uploaded" | "folder" | "typed" | "unknown"
  /** v0.5.0: true iff the citation viewer can open this file. Server
   *  sets this when the on-disk raw copy under ./data/uploads/ is
   *  present AND the extension is one the viewer can render
   *  (currently just .pdf). */
  has_raw?: boolean
}

export interface DocumentsResponse {
  filenames: string[]
  chunk_count: number
  summaries: Record<string, string>
  /** v0.2.0+: rich per-file metadata for the Library panel. May be
   *  empty when talking to an older backend. */
  files: FileMeta[]
}

/** v0.2.0: one bi-encoder candidate's journey through retrieval. */
export interface RetrievalTraceEntry {
  filename: string
  page: number | null
  chunk_index: number | null
  /** First ~200 chars of the chunk text. */
  snippet: string
  /** 0-indexed position from the bi-encoder. */
  rank_before: number
  /** 0-indexed final rank, or null if this candidate was dropped after rerank. */
  rank_after: number | null
  /** Cross-encoder score; null when no rerank ran (small candidate set / no reranker). */
  score_after: number | null
  /** Set after the answer streams: true iff this candidate's (filename, page)
   *  was inline-cited by the model. */
  cited?: boolean
  /** v0.5.0: which retrieval channel surfaced this candidate.
   *  'dense' / 'bm25' / 'both' for hybrid runs; '' or 'dense' for the
   *  legacy dense-only modes. Rendered as a small chip in the trace tab. */
  source?: string
}

export interface DoneEvent {
  question: string
  standalone_question: string
  answer: string
  source_documents: SourceDoc[]
  chitchat: boolean
  scope: string[] | null
  /** v0.2.0+: per-candidate retrieval trace for the drawer's trace tab. */
  retrieval_trace?: RetrievalTraceEntry[]
  /** v0.2.0+: 'topk' | 'topk+rerank' | 'balanced' | 'balanced+rerank' | ''. */
  retrieval_mode?: string
  /** v0.5.0 regenerate: index into conv["messages"] of the assistant
   *  bubble the new variant attaches to. Absent on non-regenerate turns. */
  regenerated_message_index?: number
}

/** v0.5.0: one regenerated version of an assistant turn. Lives in the
 *  ChatMessage.variants array; the user flips between them in the
 *  < N/M > pager and the chain history uses whichever index matches
 *  ChatMessage.activeVariant. */
export interface AssistantVariant {
  content: string
  chitchat?: boolean
  sources?: SourceDoc[]
  standaloneQuery?: string
  retrievalTrace?: RetrievalTraceEntry[]
  retrievalMode?: string
}

export interface ChatMessage {
  id: string
  role: "user" | "assistant"
  content: string
  /** True while the assistant message is still being streamed. */
  streaming?: boolean
  /** Set to true when the chain short-circuited the chitchat path. */
  chitchat?: boolean
  /** Final cited sources (filtered to those mentioned in the answer). */
  sources?: SourceDoc[]
  /** Standalone search query that drove retrieval (debug). */
  standaloneQuery?: string
  /** Cached TTS audio for the replay icon, if voice mode synthesised one. */
  audioUrl?: string
  /** v0.2.0+: per-candidate retrieval trace (populated on done). */
  retrievalTrace?: RetrievalTraceEntry[]
  /** v0.2.0+: 'topk', 'topk+rerank', etc. — surfaced as a small badge. */
  retrievalMode?: string
  /** v0.5.0: index of this message inside its conversation's messages
   *  array. Used by regenerate / switch-variant to address the right
   *  assistant bubble on the server. Only present for messages loaded
   *  from the persistence layer, not for in-flight optimistic ones. */
  serverIndex?: number
  /** v0.5.0: regenerated answers stored alongside the original. Empty
   *  or single-element arrays render no pager. */
  variants?: AssistantVariant[]
  /** v0.5.0: index into variants[] currently rendered. The other
   *  display fields (content / sources / etc.) mirror this variant. */
  activeVariant?: number
}
