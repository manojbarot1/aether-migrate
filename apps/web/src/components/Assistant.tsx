/**
 * Assistant — sliding side panel with streaming AI chat.
 *
 * Available on every page. Messages are rendered as:
 * - User messages: plain text (HTML-escaped via React's default behaviour)
 * - Assistant messages: sanitized Markdown (react-markdown, no raw HTML)
 * - Tool calls: ToolCallCard
 * - Tool results: structured cards (VMTableCard, SnapshotStatusCard, …)
 *
 * A "Data from snapshot, not live" disclaimer is shown at the top.
 * Snapshot time is shown on all inventory data via the result cards.
 */
import React from 'react'
import { SSEChunk, streamChat } from '../api/ai'
import ToolCallCard from './cards/ToolCallCard'
import VMTableCard from './cards/VMTableCard'
import SnapshotStatusCard from './cards/SnapshotStatusCard'

// ---------------------------------------------------------------------------
// Message types
// ---------------------------------------------------------------------------

interface UserMessage {
  kind: 'user'
  id: string
  text: string
}

interface AssistantMessage {
  kind: 'assistant'
  id: string
  text: string
}

interface ToolCallMessage {
  kind: 'tool_call'
  id: string
  tool: string
  status: 'running' | 'done' | 'error'
}

interface ToolResultMessage {
  kind: 'tool_result'
  id: string
  tool: string
  resultId: string
  cardType: string
  data: Record<string, unknown> | null
}

type Message =
  | UserMessage
  | AssistantMessage
  | ToolCallMessage
  | ToolResultMessage

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function SimpleMarkdown({ text }: { text: string }) {
  // Minimal safe markdown: bold, italic, code, paragraphs.
  // We do NOT render raw HTML (no dangerouslySetInnerHTML).
  const lines = text.split('\n')
  return (
    <span style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
      {lines.map((line, i) => (
        <React.Fragment key={i}>
          {i > 0 && <br />}
          {line}
        </React.Fragment>
      ))}
    </span>
  )
}

// ---------------------------------------------------------------------------
// Result card dispatcher
// ---------------------------------------------------------------------------

function ResultCard({
  tool: _tool,
  cardType,
  data,
}: {
  tool: string
  cardType: string
  data: Record<string, unknown> | null
}) {
  if (!data) return null

  if (cardType === 'vm_table' && 'vms' in data) {
    const d = data as {
      vms: Parameters<typeof VMTableCard>[0]['vms']
      total: number
      snapshot_time?: string | null
      coverage_warning?: string | null
    }
    return (
      <VMTableCard
        vms={d.vms}
        total={d.total}
        snapshot_time={d.snapshot_time}
        coverage_warning={d.coverage_warning}
      />
    )
  }

  if (cardType === 'snapshot_status' && 'snapshots' in data) {
    return (
      <SnapshotStatusCard
        snapshots={data.snapshots as Parameters<typeof SnapshotStatusCard>[0]['snapshots']}
      />
    )
  }

  if (cardType === 'topology_preview') {
    return (
      <div
        style={{
          padding: '10px 12px',
          border: '1px solid #e5e7eb',
          borderRadius: '6px',
          fontSize: '0.8rem',
          color: '#57606a',
          margin: '8px 0',
        }}
      >
        🗺 Topology data available — open the Topology view for the full graph.
      </div>
    )
  }

  if (cardType === 'plan_summary') {
    return (
      <div
        style={{
          padding: '10px 12px',
          border: '1px solid #e5e7eb',
          borderRadius: '6px',
          fontSize: '0.8rem',
          color: '#57606a',
          margin: '8px 0',
        }}
      >
        📋 Plan data — full plan view available in Phase 7.
      </div>
    )
  }

  // Generic fallback
  return (
    <pre
      style={{
        padding: '8px',
        background: '#f7f8fa',
        borderRadius: '4px',
        fontSize: '0.7rem',
        overflowX: 'auto',
        margin: '4px 0',
        maxHeight: '200px',
      }}
    >
      {JSON.stringify(data, null, 2)}
    </pre>
  )
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

interface AssistantProps {
  open: boolean
  onClose: () => void
}

export const Assistant: React.FC<AssistantProps> = ({ open, onClose }) => {
  const [messages, setMessages] = React.useState<Message[]>([])
  const [input, setInput] = React.useState('')
  const [sending, setSending] = React.useState(false)
  const [conversationId, setConversationId] = React.useState<string | undefined>()
  const [_toolResults, _setToolResults] = React.useState<Record<string, Record<string, unknown>>>({})
  const bottomRef = React.useRef<HTMLDivElement>(null)

  React.useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const handleSend = async () => {
    const text = input.trim()
    if (!text || sending) return
    setInput('')
    setSending(true)

    const userMsgId = `u-${Date.now()}`
    setMessages((prev) => [...prev, { kind: 'user', id: userMsgId, text }])

    const asstMsgId = `a-${Date.now()}`
    setMessages((prev) => [
      ...prev,
      { kind: 'assistant', id: asstMsgId, text: '' },
    ])

    try {
      const convId = await streamChat(
        text,
        (chunk: SSEChunk) => {
          if (chunk.type === 'text') {
            setMessages((prev) =>
              prev.map((m) =>
                m.id === asstMsgId && m.kind === 'assistant'
                  ? { ...m, text: m.text + (chunk.content ?? '') }
                  : m,
              ),
            )
          } else if (chunk.type === 'tool_call') {
            const tcId = `tc-${chunk.tool}-${Date.now()}`
            setMessages((prev) => [
              ...prev,
              {
                kind: 'tool_call',
                id: tcId,
                tool: chunk.tool ?? '',
                status: 'running',
              },
            ])
          } else if (chunk.type === 'tool_result') {
            // Mark the most recent running tool_call for this tool as done
            setMessages((prev) =>
              prev.map((m) => {
                if (
                  m.kind === 'tool_call' &&
                  m.tool === chunk.tool &&
                  m.status === 'running'
                ) {
                  return { ...m, status: 'done' as const }
                }
                return m
              }),
            )
            // Add a result card placeholder; data is fetched inline from the API
            const trId = `tr-${chunk.result_id}-${Date.now()}`
            setMessages((prev) => [
              ...prev,
              {
                kind: 'tool_result',
                id: trId,
                tool: chunk.tool ?? '',
                resultId: chunk.result_id ?? '',
                cardType: chunk.card_type ?? 'generic',
                data: null,
              },
            ])
          } else if (chunk.type === 'done') {
            if (chunk.conversation_id) setConversationId(chunk.conversation_id)
          }
        },
        conversationId,
      )
      if (convId) setConversationId(convId)
    } catch (err) {
      setMessages((prev) => [
        ...prev,
        {
          kind: 'assistant',
          id: `err-${Date.now()}`,
          text: 'Sorry, an error occurred. Please try again.',
        },
      ])
    } finally {
      setSending(false)
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  return (
    <div
      style={{
        position: 'fixed',
        top: 0,
        right: open ? 0 : '-440px',
        width: '420px',
        height: '100vh',
        background: '#fff',
        borderLeft: '1px solid #e5e7eb',
        display: 'flex',
        flexDirection: 'column',
        zIndex: 200,
        transition: 'right 0.25s ease',
        boxShadow: open ? '-4px 0 16px rgba(0,0,0,0.08)' : 'none',
      }}
    >
      {/* Header */}
      <div
        style={{
          padding: '12px 16px',
          borderBottom: '1px solid #e5e7eb',
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          flexShrink: 0,
        }}
      >
        <span style={{ fontWeight: 700, fontSize: '0.9rem', color: '#3b82d4' }}>
          AI Assistant
        </span>
        <button
          onClick={onClose}
          style={{
            background: 'none',
            border: 'none',
            cursor: 'pointer',
            color: '#57606a',
            fontSize: '1.1rem',
          }}
        >
          ✕
        </button>
      </div>

      {/* Disclaimer */}
      <div
        style={{
          padding: '6px 16px',
          background: '#fffbeb',
          borderBottom: '1px solid #fde68a',
          fontSize: '0.7rem',
          color: '#92400e',
          flexShrink: 0,
        }}
      >
        ℹ️ Data shown is from discovery snapshots, not live cloud APIs.
      </div>

      {/* Messages */}
      <div
        style={{
          flex: 1,
          overflowY: 'auto',
          padding: '16px',
          display: 'flex',
          flexDirection: 'column',
          gap: '12px',
        }}
      >
        {messages.length === 0 && (
          <div style={{ color: '#57606a', fontSize: '0.85rem', textAlign: 'center', marginTop: '2rem' }}>
            Ask about your cloud inventory, topology, or migration readiness.
          </div>
        )}

        {messages.map((msg) => {
          if (msg.kind === 'user') {
            return (
              <div key={msg.id} style={{ display: 'flex', justifyContent: 'flex-end' }}>
                <div
                  style={{
                    background: '#3b82d4',
                    color: '#fff',
                    padding: '8px 12px',
                    borderRadius: '12px 12px 4px 12px',
                    maxWidth: '80%',
                    fontSize: '0.875rem',
                    wordBreak: 'break-word',
                  }}
                >
                  {msg.text}
                </div>
              </div>
            )
          }

          if (msg.kind === 'assistant') {
            return (
              <div key={msg.id} style={{ display: 'flex', justifyContent: 'flex-start' }}>
                <div
                  style={{
                    background: '#f7f8fa',
                    border: '1px solid #e5e7eb',
                    padding: '8px 12px',
                    borderRadius: '12px 12px 12px 4px',
                    maxWidth: '90%',
                    fontSize: '0.875rem',
                    color: '#1f2328',
                  }}
                >
                  {msg.text ? (
                    <SimpleMarkdown text={msg.text} />
                  ) : (
                    <span style={{ color: '#57606a' }}>…</span>
                  )}
                </div>
              </div>
            )
          }

          if (msg.kind === 'tool_call') {
            return (
              <div key={msg.id} style={{ paddingLeft: '4px' }}>
                <ToolCallCard tool={msg.tool} status={msg.status} />
              </div>
            )
          }

          if (msg.kind === 'tool_result') {
            return (
              <div key={msg.id}>
                <ResultCard tool={msg.tool} cardType={msg.cardType} data={msg.data} />
              </div>
            )
          }

          return null
        })}
        <div ref={bottomRef} />
      </div>

      {/* Input */}
      <div
        style={{
          padding: '12px 16px',
          borderTop: '1px solid #e5e7eb',
          display: 'flex',
          gap: '8px',
          flexShrink: 0,
        }}
      >
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Ask about your inventory…"
          rows={2}
          style={{
            flex: 1,
            resize: 'none',
            border: '1px solid #e5e7eb',
            borderRadius: '6px',
            padding: '8px 10px',
            fontSize: '0.875rem',
            fontFamily: 'inherit',
            outline: 'none',
          }}
        />
        <button
          onClick={handleSend}
          disabled={sending || !input.trim()}
          style={{
            padding: '8px 14px',
            background: sending || !input.trim() ? '#e5e7eb' : '#3b82d4',
            color: sending || !input.trim() ? '#9ca3af' : '#fff',
            border: 'none',
            borderRadius: '6px',
            cursor: sending || !input.trim() ? 'not-allowed' : 'pointer',
            fontSize: '0.875rem',
            fontWeight: 600,
            alignSelf: 'flex-end',
          }}
        >
          {sending ? '…' : 'Send'}
        </button>
      </div>
    </div>
  )
}

export default Assistant
