/**
 * SnapshotStatusCard — renders a discovery snapshot status badge.
 */
import React from 'react'

interface SnapshotSummary {
  connection_id: string
  snapshot_id: string
  status: string
  completed_at: string | null
  coverage_summary: Record<string, string>
  vm_count: number
}

interface SnapshotStatusCardProps {
  snapshots: SnapshotSummary[]
}

const STATUS_BADGE: Record<string, { bg: string; fg: string; label: string }> = {
  completed: { bg: '#dcfce7', fg: '#15803d', label: 'Completed' },
  running: { bg: '#dbeafe', fg: '#1d4ed8', label: 'Running' },
  failed: { bg: '#fee2e2', fg: '#dc2626', label: 'Failed' },
}

const Badge: React.FC<{ status: string }> = ({ status }) => {
  const style = STATUS_BADGE[status] ?? { bg: '#f3f4f6', fg: '#374151', label: status }
  return (
    <span
      style={{
        display: 'inline-block',
        padding: '2px 8px',
        borderRadius: '9999px',
        background: style.bg,
        color: style.fg,
        fontSize: '0.7rem',
        fontWeight: 600,
      }}
    >
      {style.label}
    </span>
  )
}

export const SnapshotStatusCard: React.FC<SnapshotStatusCardProps> = ({ snapshots }) => {
  if (snapshots.length === 0) {
    return (
      <div
        style={{
          padding: '12px',
          border: '1px solid #e5e7eb',
          borderRadius: '6px',
          color: '#57606a',
          fontSize: '0.8rem',
        }}
      >
        No discovery snapshots found.
      </div>
    )
  }

  return (
    <div
      style={{
        border: '1px solid #e5e7eb',
        borderRadius: '6px',
        overflow: 'hidden',
        fontSize: '0.8rem',
        margin: '8px 0',
      }}
    >
      <div
        style={{
          padding: '8px 12px',
          background: '#f7f8fa',
          borderBottom: '1px solid #e5e7eb',
          fontWeight: 600,
          color: '#1f2328',
        }}
      >
        Discovery Snapshots
      </div>
      {snapshots.map((snap) => (
        <div
          key={snap.snapshot_id}
          style={{
            padding: '10px 12px',
            borderBottom: '1px solid #f0f0f0',
            display: 'grid',
            gridTemplateColumns: '1fr auto',
            gap: '8px',
            alignItems: 'start',
          }}
        >
          <div>
            <div style={{ color: '#57606a', fontFamily: 'monospace', fontSize: '0.7rem' }}>
              {snap.connection_id}
            </div>
            <div style={{ marginTop: '4px', color: '#1f2328' }}>
              <strong>{snap.vm_count}</strong> VMs discovered
              {snap.completed_at && (
                <span style={{ color: '#57606a', marginLeft: '8px' }}>
                  · as of {new Date(snap.completed_at).toLocaleString()}
                </span>
              )}
            </div>
            {Object.keys(snap.coverage_summary).length > 0 && (
              <div style={{ marginTop: '4px', color: '#57606a', fontSize: '0.7rem' }}>
                Coverage:{' '}
                {Object.entries(snap.coverage_summary)
                  .map(([region, status]) => `${region}: ${status}`)
                  .join(', ')}
              </div>
            )}
          </div>
          <Badge status={snap.status} />
        </div>
      ))}
      <div
        style={{
          padding: '6px 12px',
          background: '#f7f8fa',
          fontSize: '0.7rem',
          color: '#57606a',
        }}
      >
        Data from discovery snapshot, not live cloud API.
      </div>
    </div>
  )
}

export default SnapshotStatusCard
