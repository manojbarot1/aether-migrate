/**
 * ResourceDetail page — full detail view with tabbed sections.
 *
 * Tabs: Overview | Compute | Storage | Network | Identity | Raw
 * Navigated to via hash: #/resources/<id>
 */

import React, { useEffect, useState } from 'react'
import inventoryApi, { type ResourceDetailResponse } from '../api/inventory'

type Tab = 'overview' | 'compute' | 'storage' | 'network' | 'identity' | 'raw'

const TABS: { id: Tab; label: string }[] = [
  { id: 'overview', label: 'Overview' },
  { id: 'compute', label: 'Compute' },
  { id: 'storage', label: 'Storage' },
  { id: 'network', label: 'Network' },
  { id: 'identity', label: 'Identity' },
  { id: 'raw', label: 'Raw' },
]

const FIELD: React.CSSProperties = {
  display: 'grid',
  gridTemplateColumns: '160px 1fr',
  gap: '0.25rem 0.75rem',
  marginBottom: '0.4rem',
  fontSize: '0.875rem',
}

const KEY: React.CSSProperties = { color: '#57606a', fontWeight: 500 }
const VAL: React.CSSProperties = { color: '#1f2328' }

const BADGE: React.CSSProperties = {
  display: 'inline-block',
  padding: '0.15rem 0.45rem',
  borderRadius: '4px',
  fontSize: '0.75rem',
  fontWeight: 600,
}

const STATUS_STYLE: Record<string, React.CSSProperties> = {
  running: { background: '#dcfce7', color: '#166534' },
  stopped: { background: '#f3f4f6', color: '#374151' },
  terminated: { background: '#fee2e2', color: '#991b1b' },
  unknown: { background: '#f3f4f6', color: '#57606a' },
}

const Row: React.FC<{ label: string; value: React.ReactNode }> = ({ label, value }) => (
  <div style={FIELD}>
    <span style={KEY}>{label}</span>
    <span style={VAL}>{value ?? '—'}</span>
  </div>
)

const Section: React.FC<{ title: string; children: React.ReactNode }> = ({ title, children }) => (
  <div style={{ marginBottom: '1.5rem' }}>
    <h3 style={{ fontSize: '0.85rem', fontWeight: 700, textTransform: 'uppercase', color: '#57606a', marginBottom: '0.75rem', letterSpacing: '0.05em' }}>
      {title}
    </h3>
    {children}
  </div>
)

function getResourceId(): string | null {
  const hash = window.location.hash
  const m = hash.match(/^#\/resources\/(.+)$/)
  return m ? m[1] : null
}

const ResourceDetail: React.FC = () => {
  const resourceId = getResourceId()
  const [tab, setTab] = useState<Tab>('overview')
  const [detail, setDetail] = useState<ResourceDetailResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [rawExpanded, setRawExpanded] = useState(false)

  useEffect(() => {
    if (!resourceId) return
    setLoading(true)
    inventoryApi
      .getResource(resourceId, true)
      .then(setDetail)
      .catch(() => setError('Failed to load resource'))
      .finally(() => setLoading(false))
  }, [resourceId])

  if (!resourceId) return <p style={{ color: '#ef4444' }}>No resource ID in URL.</p>
  if (loading) return <p style={{ color: '#57606a' }}>Loading…</p>
  if (error || !detail) return <p style={{ color: '#ef4444' }}>{error ?? 'Resource not found'}</p>

  const r = detail.resource
  const spec = r.spec as Record<string, unknown>
  const nics = (spec.nics ?? []) as Array<Record<string, unknown>>
  const disks = (spec.disks ?? []) as Array<Record<string, unknown>>
  const status = r.status

  return (
    <div>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '1rem', marginBottom: '1.5rem' }}>
        <button
          style={{ padding: '0.25rem 0.6rem', borderRadius: '4px', border: '1px solid #e5e7eb', fontSize: '0.8rem', cursor: 'pointer', background: '#f7f8fa', fontFamily: 'inherit' }}
          onClick={() => { window.location.hash = '#/inventory' }}
        >
          ← Inventory
        </button>
        <h1 style={{ fontSize: '1.1rem', fontWeight: 700 }}>{r.name ?? r.native_id}</h1>
        <span style={{ ...BADGE, ...STATUS_STYLE[status] ?? STATUS_STYLE.unknown }}>{status}</span>
      </div>

      {/* Tabs */}
      <div style={{ display: 'flex', gap: '0', borderBottom: '2px solid #e5e7eb', marginBottom: '1.5rem' }}>
        {TABS.map((t) => (
          <button
            key={t.id}
            style={{
              padding: '0.4rem 0.85rem',
              fontSize: '0.875rem',
              cursor: 'pointer',
              border: 'none',
              borderBottom: tab === t.id ? '2px solid #3b82d4' : '2px solid transparent',
              background: 'transparent',
              color: tab === t.id ? '#3b82d4' : '#57606a',
              fontFamily: 'inherit',
              fontWeight: tab === t.id ? 600 : 400,
              marginBottom: '-2px',
            }}
            onClick={() => setTab(t.id)}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* Tab content */}
      {tab === 'overview' && (
        <Section title="Overview">
          <Row label="Native ID" value={r.native_id} />
          <Row label="Provider" value={r.provider.toUpperCase()} />
          <Row label="Account" value={r.account} />
          <Row label="Region" value={r.region} />
          <Row label="Zone" value={r.zone} />
          <Row label="Lifecycle" value={(spec.extra as Record<string, unknown>)?.lifecycle as string} />
          <Row label="Tenancy" value={spec.tenancy as string} />
          <Row label="Discovered" value={r.discovered_at ? new Date(r.discovered_at).toLocaleString() : null} />
          <Row label="Snapshot" value={r.snapshot_id?.slice(0, 8) ?? null} />
        </Section>
      )}

      {tab === 'compute' && (
        <Section title="Compute">
          <Row label="vCPU" value={spec.vcpu != null ? String(spec.vcpu) : null} />
          <Row label="Memory" value={spec.memory_gib != null ? `${spec.memory_gib} GiB` : null} />
          <Row label="Architecture" value={spec.architecture as string} />
          <Row label="Instance Type" value={spec.instance_type as string} />
          <Row label="OS" value={spec.os_name as string} />
          <Row label="Image ID" value={spec.image_id as string} />
          <Row label="Hypervisor" value={(spec.extra as Record<string, unknown>)?.hypervisor as string} />
          <Row label="EBS Optimized" value={String((spec.extra as Record<string, unknown>)?.ebs_optimized ?? '—')} />
        </Section>
      )}

      {tab === 'storage' && (
        <Section title="Disks">
          {disks.length === 0 ? (
            <p style={{ color: '#57606a', fontSize: '0.875rem' }}>No disks attached.</p>
          ) : (
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.875rem' }}>
              <thead>
                <tr style={{ borderBottom: '2px solid #e5e7eb', color: '#57606a', fontSize: '0.78rem', textTransform: 'uppercase', textAlign: 'left' }}>
                  <th style={{ padding: '0.4rem' }}>Type</th>
                  <th style={{ padding: '0.4rem' }}>Size</th>
                  <th style={{ padding: '0.4rem' }}>IOPS</th>
                  <th style={{ padding: '0.4rem' }}>Boot</th>
                  <th style={{ padding: '0.4rem' }}>Encrypted</th>
                  <th style={{ padding: '0.4rem' }}>Ephemeral</th>
                </tr>
              </thead>
              <tbody>
                {disks.map((d, i) => (
                  <tr key={i} style={{ borderBottom: '1px solid #e5e7eb' }}>
                    <td style={{ padding: '0.4rem' }}>{(d.type_class as string) ?? '—'}</td>
                    <td style={{ padding: '0.4rem' }}>{d.size_gib != null ? `${d.size_gib} GiB` : '—'}</td>
                    <td style={{ padding: '0.4rem' }}>{d.iops != null ? String(d.iops) : '—'}</td>
                    <td style={{ padding: '0.4rem' }}>{d.boot ? '✓' : '—'}</td>
                    <td style={{ padding: '0.4rem' }}>{d.encrypted ? '✓' : '—'}</td>
                    <td style={{ padding: '0.4rem' }}>
                      {d.ephemeral ? (
                        <span style={{ color: '#f59e0b', fontWeight: 600 }}>⚠ Ephemeral</span>
                      ) : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Section>
      )}

      {tab === 'network' && (
        <Section title="Network Interfaces">
          {nics.length === 0 ? (
            <p style={{ color: '#57606a', fontSize: '0.875rem' }}>No network interfaces.</p>
          ) : (
            nics.map((nic, i) => (
              <div key={i} style={{ border: '1px solid #e5e7eb', borderRadius: '4px', padding: '0.75rem', marginBottom: '0.75rem' }}>
                <Row label="Subnet" value={nic.subnet_id as string} />
                <Row label="Private IPs" value={(nic.private_ips as string[])?.join(', ')} />
                <Row label="Public IPs" value={(nic.public_ips as string[])?.join(', ')} />
                <Row label="Security Groups" value={(nic.security_group_ids as string[])?.join(', ')} />
                <Row label="Src/Dst Check" value={String(nic.source_dest_check ?? '—')} />
              </div>
            ))
          )}
        </Section>
      )}

      {tab === 'identity' && (
        <Section title="IAM Identity">
          <Row label="Instance Role" value={(spec.extra as Record<string, unknown>)?.identity_arn as string} />
          <Row label="Tags" value={
            Object.keys(r.tags).length > 0 ? (
              <pre style={{ margin: 0, fontSize: '0.78rem', color: '#57606a' }}>
                {JSON.stringify(r.tags, null, 2)}
              </pre>
            ) : null
          } />
        </Section>
      )}

      {tab === 'raw' && (
        <Section title="Raw Data">
          {detail.raw_ref ? (
            <div>
              <button
                style={{ padding: '0.3rem 0.65rem', borderRadius: '4px', border: '1px solid #e5e7eb', fontSize: '0.8rem', cursor: 'pointer', background: '#f7f8fa', fontFamily: 'inherit', marginBottom: '0.75rem' }}
                onClick={() => setRawExpanded(!rawExpanded)}
              >
                {rawExpanded ? 'Collapse' : 'Expand'} Raw JSON
              </button>
              {rawExpanded && (
                <pre style={{ background: '#f7f8fa', border: '1px solid #e5e7eb', borderRadius: '4px', padding: '0.75rem', fontSize: '0.78rem', overflowX: 'auto' }}>
                  {JSON.stringify(spec, null, 2)}
                </pre>
              )}
            </div>
          ) : (
            <p style={{ color: '#57606a', fontSize: '0.875rem' }}>
              Raw data not available (requires analyst role or raw_ref not stored).
            </p>
          )}
          {Object.keys(detail.provenance).length > 0 && (
            <div style={{ marginTop: '1rem' }}>
              <h4 style={{ fontSize: '0.8rem', color: '#57606a', marginBottom: '0.5rem' }}>Provenance</h4>
              <pre style={{ background: '#f7f8fa', border: '1px solid #e5e7eb', borderRadius: '4px', padding: '0.75rem', fontSize: '0.78rem', overflowX: 'auto' }}>
                {JSON.stringify(detail.provenance, null, 2)}
              </pre>
            </div>
          )}
          {(detail.edges_out.length > 0 || detail.edges_in.length > 0) && (
            <div style={{ marginTop: '1rem' }}>
              <h4 style={{ fontSize: '0.8rem', color: '#57606a', marginBottom: '0.5rem' }}>Edges</h4>
              {detail.edges_out.map((e, i) => (
                <p key={`out-${i}`} style={{ fontSize: '0.8rem' }}>→ {e.kind}: {e.to_id.slice(0, 12)}…</p>
              ))}
              {detail.edges_in.map((e, i) => (
                <p key={`in-${i}`} style={{ fontSize: '0.8rem' }}>← {e.kind}: {e.from_id.slice(0, 12)}…</p>
              ))}
            </div>
          )}
        </Section>
      )}
    </div>
  )
}

export default ResourceDetail
