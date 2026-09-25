/**
 * SnapshotBadge — displays "as of HH:mm UTC" with a status color indicator.
 */

import React from 'react'

interface SnapshotBadgeProps {
  snapshotTime: string | null
  status?: string
}

const STATUS_COLORS: Record<string, string> = {
  completed: '#22c55e',
  running: '#f59e0b',
  failed: '#ef4444',
  unknown: '#57606a',
}

export const SnapshotBadge: React.FC<SnapshotBadgeProps> = ({ snapshotTime, status = 'unknown' }) => {
  if (!snapshotTime) {
    return (
      <span style={{ fontSize: '0.75rem', color: '#57606a', fontStyle: 'italic' }}>
        no snapshot
      </span>
    )
  }

  const dt = new Date(snapshotTime)
  const hhmm = dt.toISOString().slice(11, 16)

  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: '0.3rem',
        fontSize: '0.75rem',
        color: '#57606a',
        background: '#f7f8fa',
        border: '1px solid #e5e7eb',
        borderRadius: '4px',
        padding: '0.1rem 0.4rem',
      }}
    >
      <span
        style={{
          width: '6px',
          height: '6px',
          borderRadius: '50%',
          background: STATUS_COLORS[status] ?? STATUS_COLORS.unknown,
          flexShrink: 0,
        }}
      />
      as of {hhmm} UTC
    </span>
  )
}

export default SnapshotBadge
