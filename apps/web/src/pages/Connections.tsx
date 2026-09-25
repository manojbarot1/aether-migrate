/**
 * Connections page — full CRUD for cloud provider connections.
 *
 * Features:
 * - Table: name, provider, mode, last tested, status badge
 * - "Add Connection" → modal with ConnectionForm
 * - "Test" button per row
 * - "Discover" button per row
 * - Delete with confirmation
 */

import React, { useCallback, useEffect, useState } from 'react'
import { apiClient } from '../api/client'
import discoveryApi from '../api/discovery'
import ConnectionForm, { type ConnectionFormData } from '../components/ConnectionForm'
import SnapshotBadge from '../components/SnapshotBadge'

interface ConnectionRow {
  id: string
  workspace_id: string
  name: string
  provider: string
  mode: string
  scope: string | null
  last_tested_at: string | null
  last_test_ok: boolean | null
  created_at: string
}

interface TestResult {
  ok: boolean
  identity: string | null
  warnings: string[]
  errors: string[]
}

const BADGE: React.CSSProperties = {
  display: 'inline-block',
  padding: '0.15rem 0.5rem',
  borderRadius: '4px',
  fontSize: '0.75rem',
  fontWeight: 600,
}

const STATUS_BADGE_STYLE = (ok: boolean | null): React.CSSProperties => ({
  ...BADGE,
  background: ok === null ? '#f0f0f0' : ok ? '#dcfce7' : '#fee2e2',
  color: ok === null ? '#57606a' : ok ? '#166534' : '#991b1b',
})

const BTN: React.CSSProperties = {
  padding: '0.25rem 0.6rem',
  borderRadius: '4px',
  fontSize: '0.8rem',
  cursor: 'pointer',
  border: '1px solid #e5e7eb',
  background: '#f7f8fa',
  fontFamily: 'inherit',
}

const MODAL_OVERLAY: React.CSSProperties = {
  position: 'fixed',
  inset: 0,
  background: 'rgba(0,0,0,0.3)',
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  zIndex: 100,
}

const MODAL: React.CSSProperties = {
  background: '#fff',
  borderRadius: '8px',
  padding: '1.5rem',
  width: '460px',
  maxWidth: '95vw',
  maxHeight: '90vh',
  overflowY: 'auto',
  border: '1px solid #e5e7eb',
}

const Connections: React.FC = () => {
  const [connections, setConnections] = useState<ConnectionRow[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // Add modal
  const [showAdd, setShowAdd] = useState(false)
  const [addLoading, setAddLoading] = useState(false)
  const [addError, setAddError] = useState<string | null>(null)

  // Test result modal
  const [testResult, setTestResult] = useState<{ conn: string; result: TestResult } | null>(null)

  // Discover result
  const [discoverMsg, setDiscoverMsg] = useState<string | null>(null)

  // Delete confirmation
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null)

  // Snapshot status per connection
  const [snapTimes, setSnapTimes] = useState<Record<string, { time: string | null; status: string }>>({})

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const rows = await apiClient.get<ConnectionRow[]>('/api/v1/connections')
      setConnections(rows)
      // Load snapshot status for each connection
      const statuses: Record<string, { time: string | null; status: string }> = {}
      await Promise.allSettled(
        rows.map(async (c) => {
          try {
            const s = await discoveryApi.getConnectionStatus(c.id)
            statuses[c.id] = {
              time: s.last_discovery_time,
              status: s.latest_snapshot?.status ?? 'unknown',
            }
          } catch {
            statuses[c.id] = { time: null, status: 'unknown' }
          }
        })
      )
      setSnapTimes(statuses)
    } catch {
      setError('Failed to load connections')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const handleAdd = async (data: ConnectionFormData) => {
    setAddLoading(true)
    setAddError(null)
    try {
      await apiClient.post('/api/v1/connections', data)
      setShowAdd(false)
      load()
    } catch (e: unknown) {
      const err = e as { error?: string }
      setAddError(err?.error ?? 'Failed to create connection')
    } finally {
      setAddLoading(false)
    }
  }

  const handleTest = async (id: string, name: string) => {
    try {
      const result = await apiClient.post<TestResult>(`/api/v1/connections/${id}/test`, {})
      setTestResult({ conn: name, result })
      load()
    } catch {
      alert('Test request failed')
    }
  }

  const handleDiscover = async (id: string) => {
    try {
      const resp = await discoveryApi.refresh(id)
      setDiscoverMsg(`Discovery started — job: ${resp.job_id}`)
      setTimeout(() => setDiscoverMsg(null), 5000)
    } catch {
      alert('Failed to start discovery')
    }
  }

  const handleDelete = async (id: string) => {
    try {
      await apiClient.delete(`/api/v1/connections/${id}`)
      setDeleteTarget(null)
      load()
    } catch {
      alert('Failed to delete connection')
    }
  }

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '1.5rem' }}>
        <h1 style={{ fontSize: '1.25rem', fontWeight: 700 }}>Connections</h1>
        <button
          style={{ ...BTN, background: '#3b82d4', color: '#fff', border: 'none', padding: '0.4rem 0.9rem' }}
          onClick={() => { setShowAdd(true); setAddError(null) }}
        >
          + Add Connection
        </button>
      </div>

      {discoverMsg && (
        <div style={{ background: '#f0fdf4', border: '1px solid #bbf7d0', borderRadius: '4px', padding: '0.5rem 0.75rem', marginBottom: '1rem', fontSize: '0.85rem', color: '#166534' }}>
          {discoverMsg}
        </div>
      )}

      {loading ? (
        <p style={{ color: '#57606a' }}>Loading…</p>
      ) : error ? (
        <p style={{ color: '#ef4444' }}>{error}</p>
      ) : connections.length === 0 ? (
        <p style={{ color: '#57606a', fontSize: '0.875rem' }}>
          No connections yet. Add one to start discovering resources.
        </p>
      ) : (
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.875rem' }}>
            <thead>
              <tr style={{ borderBottom: '2px solid #e5e7eb', textAlign: 'left', color: '#57606a', fontSize: '0.78rem', textTransform: 'uppercase' }}>
                <th style={{ padding: '0.5rem 0.75rem' }}>Name</th>
                <th style={{ padding: '0.5rem 0.75rem' }}>Provider</th>
                <th style={{ padding: '0.5rem 0.75rem' }}>Mode</th>
                <th style={{ padding: '0.5rem 0.75rem' }}>Last Tested</th>
                <th style={{ padding: '0.5rem 0.75rem' }}>Status</th>
                <th style={{ padding: '0.5rem 0.75rem' }}>Last Discovery</th>
                <th style={{ padding: '0.5rem 0.75rem' }}>Actions</th>
              </tr>
            </thead>
            <tbody>
              {connections.map((c) => (
                <tr key={c.id} style={{ borderBottom: '1px solid #e5e7eb' }}>
                  <td style={{ padding: '0.6rem 0.75rem', fontWeight: 500 }}>{c.name}</td>
                  <td style={{ padding: '0.6rem 0.75rem' }}>
                    <span style={{ ...BADGE, background: '#eff6ff', color: '#1e40af' }}>
                      {c.provider.toUpperCase()}
                    </span>
                  </td>
                  <td style={{ padding: '0.6rem 0.75rem', color: '#57606a' }}>{c.mode}</td>
                  <td style={{ padding: '0.6rem 0.75rem', color: '#57606a', fontSize: '0.8rem' }}>
                    {c.last_tested_at ? new Date(c.last_tested_at).toLocaleString() : '—'}
                  </td>
                  <td style={{ padding: '0.6rem 0.75rem' }}>
                    <span style={STATUS_BADGE_STYLE(c.last_test_ok)}>
                      {c.last_test_ok === null ? 'Not tested' : c.last_test_ok ? 'OK' : 'Failed'}
                    </span>
                  </td>
                  <td style={{ padding: '0.6rem 0.75rem' }}>
                    <SnapshotBadge
                      snapshotTime={snapTimes[c.id]?.time ?? null}
                      status={snapTimes[c.id]?.status}
                    />
                  </td>
                  <td style={{ padding: '0.6rem 0.75rem', display: 'flex', gap: '0.4rem' }}>
                    <button style={BTN} onClick={() => handleTest(c.id, c.name)}>Test</button>
                    <button style={{ ...BTN, background: '#eff6ff', color: '#1e40af', border: '1px solid #bfdbfe' }} onClick={() => handleDiscover(c.id)}>Discover</button>
                    <button style={{ ...BTN, background: '#fff', color: '#ef4444', border: '1px solid #fecaca' }} onClick={() => setDeleteTarget(c.id)}>Delete</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Add Connection Modal */}
      {showAdd && (
        <div style={MODAL_OVERLAY} onClick={() => setShowAdd(false)}>
          <div style={MODAL} onClick={(e) => e.stopPropagation()}>
            <h2 style={{ fontSize: '1rem', fontWeight: 700, marginBottom: '1rem' }}>Add Connection</h2>
            <ConnectionForm
              onSubmit={handleAdd}
              onCancel={() => setShowAdd(false)}
              loading={addLoading}
              error={addError}
            />
          </div>
        </div>
      )}

      {/* Test Result Modal */}
      {testResult && (
        <div style={MODAL_OVERLAY} onClick={() => setTestResult(null)}>
          <div style={{ ...MODAL, width: '400px' }} onClick={(e) => e.stopPropagation()}>
            <h2 style={{ fontSize: '1rem', fontWeight: 700, marginBottom: '0.75rem' }}>
              Test Result — {testResult.conn}
            </h2>
            <div style={{ marginBottom: '0.5rem' }}>
              <span style={STATUS_BADGE_STYLE(testResult.result.ok)}>
                {testResult.result.ok ? 'Connected' : 'Failed'}
              </span>
            </div>
            {testResult.result.identity && (
              <p style={{ fontSize: '0.8rem', color: '#57606a', marginBottom: '0.5rem', wordBreak: 'break-all' }}>
                Identity: {testResult.result.identity}
              </p>
            )}
            {testResult.result.warnings.length > 0 && (
              <div style={{ background: '#fffbeb', border: '1px solid #fef08a', borderRadius: '4px', padding: '0.5rem', marginBottom: '0.5rem' }}>
                <p style={{ fontSize: '0.78rem', fontWeight: 600, color: '#92400e', marginBottom: '0.25rem' }}>Warnings</p>
                {testResult.result.warnings.map((w, i) => (
                  <p key={i} style={{ fontSize: '0.78rem', color: '#92400e' }}>{w}</p>
                ))}
              </div>
            )}
            {testResult.result.errors.length > 0 && (
              <div style={{ background: '#fef2f2', border: '1px solid #fecaca', borderRadius: '4px', padding: '0.5rem' }}>
                {testResult.result.errors.map((e, i) => (
                  <p key={i} style={{ fontSize: '0.78rem', color: '#991b1b' }}>{e}</p>
                ))}
              </div>
            )}
            <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: '1rem' }}>
              <button style={BTN} onClick={() => setTestResult(null)}>Close</button>
            </div>
          </div>
        </div>
      )}

      {/* Delete Confirmation */}
      {deleteTarget && (
        <div style={MODAL_OVERLAY} onClick={() => setDeleteTarget(null)}>
          <div style={{ ...MODAL, width: '360px' }} onClick={(e) => e.stopPropagation()}>
            <h2 style={{ fontSize: '1rem', fontWeight: 700, marginBottom: '0.75rem' }}>Delete Connection?</h2>
            <p style={{ fontSize: '0.875rem', color: '#57606a', marginBottom: '1rem' }}>
              This will remove credentials from OpenBao and delete all snapshots. This action cannot be undone.
            </p>
            <div style={{ display: 'flex', gap: '0.5rem', justifyContent: 'flex-end' }}>
              <button style={BTN} onClick={() => setDeleteTarget(null)}>Cancel</button>
              <button
                style={{ ...BTN, background: '#ef4444', color: '#fff', border: 'none' }}
                onClick={() => handleDelete(deleteTarget)}
              >
                Delete
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

export default Connections
