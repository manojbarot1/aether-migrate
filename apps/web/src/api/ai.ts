/**
 * AI chat client — SSE-based streaming chat for the AETHER MIGRATE assistant.
 */

const AI_BASE_URL = import.meta.env.VITE_AI_URL ?? ''

import { getToken } from './client'

export interface SSEChunk {
  type: 'text' | 'tool_call' | 'tool_result' | 'done' | 'error'
  content?: string
  tool?: string
  input?: Record<string, unknown>
  result_id?: string
  card_type?: string
  conversation_id?: string
  message_id?: string
  code?: string
  message?: string
}

export interface Conversation {
  id: string
  title: string | null
  created_at: string
  updated_at: string
}

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant' | 'tool'
  content: string | null
  tool_name: string | null
  tool_result_id: string | null
  created_at: string
}

export interface ConversationDetail extends Conversation {
  messages: ChatMessage[]
}

/**
 * Start or continue a chat, streaming SSE events via a callback.
 * Returns the conversation_id when done.
 */
export async function streamChat(
  message: string,
  onChunk: (chunk: SSEChunk) => void,
  conversationId?: string,
): Promise<string | undefined> {
  const token = getToken()
  const resp = await fetch(`${AI_BASE_URL}/chat`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify({
      message,
      conversation_id: conversationId ?? null,
    }),
  })

  if (!resp.ok || !resp.body) {
    throw new Error(`Chat request failed: ${resp.status}`)
  }

  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let conversationOut: string | undefined
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split('\n')
    buffer = lines.pop() ?? ''
    for (const line of lines) {
      if (!line.startsWith('data: ')) continue
      const data = line.slice(6).trim()
      if (!data) continue
      try {
        const chunk: SSEChunk = JSON.parse(data)
        onChunk(chunk)
        if (chunk.type === 'done' && chunk.conversation_id) {
          conversationOut = chunk.conversation_id
        }
      } catch {
        // ignore malformed lines
      }
    }
  }

  return conversationOut
}

export async function listConversations(): Promise<Conversation[]> {
  const token = getToken()
  const resp = await fetch(`${AI_BASE_URL}/conversations`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  })
  if (!resp.ok) throw new Error(`Failed to list conversations: ${resp.status}`)
  return resp.json()
}

export async function getConversation(id: string): Promise<ConversationDetail> {
  const token = getToken()
  const resp = await fetch(`${AI_BASE_URL}/conversations/${id}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  })
  if (!resp.ok) throw new Error(`Failed to get conversation: ${resp.status}`)
  return resp.json()
}

export async function deleteConversation(id: string): Promise<void> {
  const token = getToken()
  await fetch(`${AI_BASE_URL}/conversations/${id}`, {
    method: 'DELETE',
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  })
}
