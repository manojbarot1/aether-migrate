/**
 * VMTableCard — renders a table of VM summaries from an inventory.search_vms result.
 */
import React from 'react'

interface VMSummary {
  id: string
  name: string | null
  provider: string
  region: string
  vcpu: number | null
  memory_gib: number | null
  os_family: string | null
  status: string
  source_sku: string | null
  snapshot_time: string | null
}

interface VMTableCardProps {
  vms: VMSummary[]
  total: number
  snapshot_time?: string | null
  coverage_warning?: string | null
}

const STATUS_COLORS: Record<string, string> = {
  running: '#16a34a',
  stopped: '#d97706',
  terminated: '#ef4444',
  unknown: '#6b7280',
}

export const VMTableCard: React.FC<VMTableCardProps> = ({
  vms,
  total,
  snapshot_time,
  coverage_warning,
}) => {
  const [sortKey, setSortKey] = React.useState<keyof VMSummary>('name')
  const [sortAsc, setSortAsc] = React.useState(true)

  const sorted = [...vms].sort((a, b) => {
    const av = a[sortKey] ?? ''
    const bv = b[sortKey] ?? ''
    if (av < bv) return sortAsc ? -1 : 1
    if (av > bv) return sortAsc ? 1 : -1
    return 0
  })

  const handleSort = (key: keyof VMSummary) => {
    if (sortKey === key) {
      setSortAsc(!sortAsc)
    } else {
      setSortKey(key)
      setSortAsc(true)
    }
  }

  const th = (label: string, key: keyof VMSummary) => (
    <th
      key={key}
      onClick={() => handleSort(key)}
      style={{
        padding: '6px 10px',
        textAlign: 'left',
        fontSize: '0.75rem',
        fontWeight: 600,
        color: '#57606a',
        cursor: 'pointer',
        userSelect: 'none',
        borderBottom: '1px solid #e5e7eb',
        whiteSpace: 'nowrap',
      }}
    >
      {label}
      {sortKey === key ? (sortAsc ? ' ↑' : ' ↓') : ''}
    </th>
  )

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
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
        }}
      >
        <span style={{ fontWeight: 600, color: '#1f2328' }}>
          Virtual Machines ({total} total)
        </span>
        {snapshot_time && (
          <span style={{ fontSize: '0.7rem', color: '#57606a' }}>
            📅 As of {new Date(snapshot_time).toLocaleString()} · snapshot data
          </span>
        )}
      </div>

      {coverage_warning && (
        <div
          style={{
            padding: '6px 12px',
            background: '#fffbeb',
            borderBottom: '1px solid #fde68a',
            fontSize: '0.75rem',
            color: '#92400e',
          }}
        >
          ⚠️ {coverage_warning}
        </div>
      )}

      <div style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead>
            <tr>
              {th('Name', 'name')}
              {th('Provider', 'provider')}
              {th('Region', 'region')}
              {th('vCPU', 'vcpu')}
              {th('Memory (GiB)', 'memory_gib')}
              {th('OS', 'os_family')}
              {th('Status', 'status')}
            </tr>
          </thead>
          <tbody>
            {sorted.map((vm) => (
              <tr
                key={vm.id}
                style={{ borderBottom: '1px solid #f0f0f0' }}
              >
                <td style={{ padding: '5px 10px', color: '#1f2328' }}>
                  {vm.name ?? <em style={{ color: '#57606a' }}>unnamed</em>}
                </td>
                <td style={{ padding: '5px 10px', color: '#57606a' }}>
                  {vm.provider.toUpperCase()}
                </td>
                <td style={{ padding: '5px 10px', color: '#57606a' }}>
                  {vm.region}
                </td>
                <td style={{ padding: '5px 10px', textAlign: 'right', color: '#1f2328' }}>
                  {vm.vcpu ?? '—'}
                </td>
                <td style={{ padding: '5px 10px', textAlign: 'right', color: '#1f2328' }}>
                  {vm.memory_gib != null ? vm.memory_gib.toFixed(0) : '—'}
                </td>
                <td style={{ padding: '5px 10px', color: '#57606a' }}>
                  {vm.os_family ?? '—'}
                </td>
                <td style={{ padding: '5px 10px' }}>
                  <span
                    style={{
                      display: 'inline-block',
                      width: '8px',
                      height: '8px',
                      borderRadius: '50%',
                      background: STATUS_COLORS[vm.status] ?? '#6b7280',
                      marginRight: '5px',
                    }}
                  />
                  {vm.status}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div
        style={{
          padding: '6px 12px',
          background: '#f7f8fa',
          borderTop: '1px solid #e5e7eb',
          fontSize: '0.7rem',
          color: '#57606a',
        }}
      >
        Data from discovery snapshot, not live cloud API.
        {vms.length < total && ` Showing ${vms.length} of ${total}.`}
      </div>
    </div>
  )
}

export default VMTableCard
