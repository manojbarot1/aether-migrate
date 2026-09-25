/**
 * ToolCallCard — shows the status of a tool invocation inline in the chat.
 */
import React from 'react'

interface ToolCallCardProps {
  tool: string
  status: 'running' | 'done' | 'error'
}

const TOOL_LABELS: Record<string, string> = {
  'inventory.search_vms': 'Searching VMs',
  'inventory.get_resource': 'Loading resource',
  'inventory.diff_snapshots': 'Comparing snapshots',
  'discovery.status': 'Checking discovery status',
  'discovery.refresh': 'Starting discovery',
  'topology.get': 'Loading topology',
  'sizing.recommend': 'Computing sizing',
  'cost.compare': 'Comparing costs',
  'assessment.run': 'Running assessment',
  'plan.create': 'Creating plan',
  'plan.get': 'Loading plan',
  'plan.explain': 'Explaining plan',
}

export const ToolCallCard: React.FC<ToolCallCardProps> = ({ tool, status }) => {
  const label = TOOL_LABELS[tool] ?? tool
  const icons = { running: '⏳', done: '✓', error: '✗' }
  const colors = { running: '#1d4ed8', done: '#15803d', error: '#dc2626' }

  return (
    <div
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: '6px',
        padding: '4px 10px',
        border: '1px solid #e5e7eb',
        borderRadius: '9999px',
        fontSize: '0.75rem',
        color: colors[status],
        background: '#f7f8fa',
        margin: '2px 0',
      }}
    >
      <span>{icons[status]}</span>
      <span>
        <code style={{ fontFamily: 'monospace', fontSize: '0.7rem' }}>{tool}</code>
        {' — '}
        {label}
      </span>
    </div>
  )
}

export default ToolCallCard
