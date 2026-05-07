import { useCallback } from "react"
import {
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query"
import { toast } from "sonner"

const BASE = "/api"

export interface ConversationMeta {
  id: string
  title: string
  created_at: number
  updated_at: number
  message_count: number
}

export interface ConversationMessage {
  role: "user" | "assistant"
  content: string
  ts?: number
}

export interface ConversationDetail {
  id: string
  title: string
  created_at: number
  updated_at: number
  messages: ConversationMessage[]
}

async function apiListConversations(): Promise<ConversationMeta[]> {
  const r = await fetch(`${BASE}/conversations`)
  if (!r.ok) throw new Error(`list -> ${r.status}`)
  const j = (await r.json()) as { conversations: ConversationMeta[] }
  return j.conversations
}

async function apiCreateConversation(
  title?: string,
): Promise<ConversationDetail> {
  const r = await fetch(`${BASE}/conversations`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title: title ?? null }),
  })
  if (!r.ok) throw new Error(`create -> ${r.status}`)
  return r.json()
}

async function apiGetConversation(
  id: string,
): Promise<ConversationDetail> {
  const r = await fetch(`${BASE}/conversations/${id}`)
  if (!r.ok) throw new Error(`get -> ${r.status}`)
  return r.json()
}

async function apiDeleteConversation(id: string): Promise<void> {
  const r = await fetch(`${BASE}/conversations/${id}`, { method: "DELETE" })
  if (!r.ok) throw new Error(`delete -> ${r.status}`)
}

async function apiRenameConversation(
  id: string,
  title: string,
): Promise<ConversationMeta> {
  const r = await fetch(`${BASE}/conversations/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
  })
  if (!r.ok) throw new Error(`rename -> ${r.status}`)
  return r.json()
}

/** Sidebar conversation-list state. The SSE `title` event the backend
 *  emits after the first turn updates the title in place; everywhere
 *  else, the list refetches when needed. */
export function useConversations() {
  const qc = useQueryClient()

  const list = useQuery({
    queryKey: ["conversations"],
    queryFn: apiListConversations,
    staleTime: 5_000,
  })

  const create = useMutation({
    mutationFn: (title?: string) => apiCreateConversation(title),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["conversations"] }),
    onError: (e) => toast.error(`Couldn't create chat: ${e.message}`),
  })

  const remove = useMutation({
    mutationFn: (id: string) => apiDeleteConversation(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["conversations"] }),
    onError: (e) => toast.error(`Delete failed: ${e.message}`),
  })

  const rename = useMutation({
    mutationFn: ({ id, title }: { id: string; title: string }) =>
      apiRenameConversation(id, title),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["conversations"] }),
    onError: (e) => toast.error(`Rename failed: ${e.message}`),
  })

  /** Apply a server-emitted `title` SSE event to the cached list without
   *  refetching. */
  const applyTitleEvent = useCallback(
    (id: string, title: string) => {
      qc.setQueryData<ConversationMeta[]>(["conversations"], (old) =>
        (old ?? []).map((c) => (c.id === id ? { ...c, title } : c)),
      )
      qc.invalidateQueries({ queryKey: ["conversations"] })
    },
    [qc],
  )

  return { list, create, remove, rename, applyTitleEvent }
}

export const conversationApi = {
  get: apiGetConversation,
  list: apiListConversations,
  create: apiCreateConversation,
  delete: apiDeleteConversation,
  rename: apiRenameConversation,
}
