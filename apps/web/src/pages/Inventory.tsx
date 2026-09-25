/**
 * Inventory page — VM list with filters, snapshot selector, and discover button.
 */

import React, { useCallback, useEffect, useState } from 'react'
import discoveryApi from '../api/discovery'
import inventoryApi, { type ResourceSummary, type VMFilter } from '../api/inventory'
import SnapshotBadge from '../components/SnapshotBadge'

const STATUS_BADGE: Record<string, React.CSSProperties> = {
  running: { background: '#dcfce7', color: '#166534' },
  stopped: { background: '#f3f4f6', color: '#374151' },
  terminated: { background: '#fee2e2', color: '#991b1b' },
  unknown: { background: '#f3f4f6', color: '#57606a' },
}

const BADGE_BASE: React.CSSProperties = {
  display: 'inline-block',
  padding: '0.15rem 0.45rem',
  borderRadius: '4px',
  fontSize: '0.75rem',
  fontWeight: 600,
}

const BTN: React.CSSProperties = {
  padding: '0.3rem 0.65rem',
  borderRadius: '4px',
  fontSize: '0.8rem',
  cursor: 'pointer',
  border: '1px solid #e5e7eb',
  background: '#f7f8fa',
  fontFamily: 'inherit',
}

const INPUT: React.CSSProperties = {
  padding: '0.3rem 0.5rem',
  borderRadius: '4px',
  border: '1px solid #e5e7eb',
  fontSize: '0.85rem',
  fontFamily: 'inherit',
}

const Inventory: React.FC = () => {
  const [vms, setVms] = useState<ResourceSummary[]>([])
  const [total, setTotal] = useState(0)
  const [snapshotId, setSnapshotId] = useState<string | null>(null)
  const [snapshotTime, setSnapshotTime] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  const [filter, setFilter] = useState<VMFilter>({
    provider: '',
    region: '',
    status: '',
    min_vcpu: undefined,
    min_memory_gib: undefined,
    limit: 50,
    offset: 0,
  })

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const resp = await inventoryApi.listVms(filter)
      setVms(resp.items)
      setTotal(resp.total)
      setSnapshotId(resp.snapshot_id)
      setSnapshotTime(resp.snapshot_time)
    } catch {
      setVms([])
    } finally {
      setLoading(false)
    }
  }, [filter])

  useEffect(() => {
    load()
  }, [load])

  const handleDiscover = async (connectionId: string) => {
    try {
      const resp = await discoveryApi.refresh(connectionId)
      alert(`Discovery started — job: ${resp.job_id}`)
    } catch {
      alert('Failed to start discovery')
    }
  }

  const navigateTo = (id: string) => {
    window.location.hash = `#/resources/${id}`
  }

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '1rem' }}>
        <h1 style={{ fontSize: '1.25rem', fontWeight: 700 }}>Inventory</h1>
        <SnapshotBadge snapshotTime={snapshotTime} status="completed" />
      </div>

      {/* Filter bar */}
      <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap', marginBottom: '1rem' }}>
        <input
          style={INPUT}
          placeholder="Provider"
          value={filter.provider ?? ''}
          onChange={(e) => setFilter((f) => ({ ...f, provider: e.target.value, offset: 0 }))}
        />
        <input
          style={INPUT}
          placeholder="Region"
          value={filter.region ?? ''}
          onChange={(e) => setFilter((f) => ({ ...f, region: e.target.value, offset: 0 }))}
        />
        <select
          style={INPUT}
          value={filter.status ?? ''}
          onChange={(e) => setFilter((f) => ({ ...f, status: e.target.value, offset: 0 }))}
        >
          <option value="">All statuses</option>
          <option value="running">Running</option>
          <option value="stopped">Stopped</option>
          <option value="terminated">Terminated</option>
        </select>
        <input
          style={{ ...INPUT, width: '90px' }}
          type="number"
          placeholder="Min vCPU"
          value={filter.min_vcpu ?? ''}
          onChange={(e) => setFilter((f) => ({ ...f, min_vcpu: e.target.value ? parseInt(e.target.value) : undefined, offset: 0 }))}
        />
        <input
          style={{ ...INPUT, width: '110px' }}
          type="number"
          placeholder="Min RAM GiB"
          value={filter.min_memory_gib ?? ''}
          onChange={(e) => setFilter((f) => ({ ...f, min_memory_gib: e.target.value ? parseFloat(e.target.value) : undefined, offset: 0 }))}
        />
        <button style={{ ...BTN, background: '#3b82d4', color: '#fff', border: 'none' }} onClick={load}>
          Refresh
        </button>
      </div>

      {loading ? (
        <p style={{ color: '#57606a' }}>Loading…</p>
      ) : vms.length === 0 ? (
        <p style={{ color: '#57606a', fontSize: '0.875rem' }}>
          No VMs found. Run discovery on a connection to populate inventory.
        </p>
      ) : (
        <>
          <p style={{ fontSize: '0.8rem', color: '#57606a', marginBottom: '0.75rem' }}>
            Showing {vms.length} of {total} VMs
            {snapshotId ? ` · snapshot ${snapshotId.slice(0, 8)}…` : ''}
          </p>
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.875rem' }}>
              <thead>
                <tr style={{ borderBottom: '2px solid #e5e7eb', textAlign: 'left', color: '#57606a', fontSize: '0.78rem', textTransform: 'uppercase' }}>
                  <th style={{ padding: '0.5rem 0.75rem' }}>Name</th>
                  <th style={{ padding: '0.5rem 0.75rem' }}>Provider</th>
                  <th style={{ padding: '0.5rem 0.75rem' }}>Region</th>
                  <th style={{ padding: '0.5rem 0.75rem' }}>vCPU</th>
                  <th style={{ padding: '0.5rem 0.75rem' }}>Memory</th>
                  <th style={{ padding: '0.5rem 0.75rem' }}>OS</th>
                  <th style={{ padding: '0.5rem 0.75rem' }}>Status</th>
                  <th style={{ padding: '0.5rem 0.75rem' }}>Actions</th>
                </tr>
              </thead>
              <tbody>
                {vms.map((vm) => {
                  const spec = vm.spec as Record<string, unknown>
                  return (
                    <tr
                      key={vm.id}
                      style={{ borderBottom: '1px solid #e5e7eb', cursor: 'pointer' }}
                      onClick={() => navigateTo(vm.id)}
                    >
                      <td style={{ padding: '0.6rem 0.75rem', fontWeight: 500 }}>
                        {vm.name ?? vm.native_id}
                      </td>
                      <td style={{ padding: '0.6rem 0.75rem', color: '#57606a' }}>
                        {vm.provider.toUpperCase()}
                      </td>
                      <td style={{ padding: '0.6rem 0.75rem', color: '#57606a' }}>{vm.region}</td>
                      <td style={{ padding: '0.6rem 0.75rem' }}>
                        {spec.vcpu != null ? String(spec.vcpu) : '—'}
                      </td>
                      <td style={{ padding: '0.6rem 0.75rem' }}>
                        {spec.memory_gib != null ? `${spec.memory_gib} GiB` : '—'}
                      </td>
                      <td style={{ padding: '0.6rem 0.75rem', color: '#57606a' }}>
                        {(spec.os_name as string) ?? '—'}
                      </td>
                      <td style={{ padding: '0.6rem 0.75rem' }}>
                        <span style={{ ...BADGE_BASE, ...STATUS_BADGE[vm.status] ?? STATUS_BADGE.unknown }}>
                          {vm.status}
                        </span>
                      </td>
                      <td style={{ padding: '0.6rem 0.75rem' }}>
                        <button
                          style={{ ...BTN, background: '#eff6ff', color: '#1e40af', border: '1px solid #bfdbfe' }}
                          onClick={(e) => { e.stopPropagation(); handleDiscover(vm.connection_id) }}
                        >
                          Refresh
                        </button>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>

          {/* Pagination */}
          <div style={{ display: 'flex', gap: '0.5rem', marginTop: '1rem', alignItems: 'center' }}>
            <button
              style={BTN}
              disabled={(filter.offset ?? 0) === 0}
              onClick={() => setFilter((f) => ({ ...f, offset: Math.max(0, (f.offset ?? 0) - (f.limit ?? 50)) }))}
            >
              ← Prev
            </button>
            <span style={{ fontSize: '0.8rem', color: '#57606a' }}>
              Page {Math.floor((filter.offset ?? 0) / (filter.limit ?? 50)) + 1}
            </span>
            <button
              style={BTN}
              disabled={vms.length < (filter.limit ?? 50)}
              onClick={() => setFilter((f) => ({ ...f, offset: (f.offset ?? 0) + (f.limit ?? 50) }))}
            >
              Next →
            </button>
          </div>
        </>
      )}
    </div>
  )
}

export default Inventory
