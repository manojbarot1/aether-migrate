/**
 * Assessment page — run migration readiness assessments against VMs.
 */

import React, { useCallback, useEffect, useState } from 'react'
import assessmentApi, {
  type AssessmentResult,
  type Finding,
  type AssessmentHistoryItem,
} from '../api/assessment'
import inventoryApi, { type ResourceSummary } from '../api/inventory'

// ---------------------------------------------------------------------------
// Style constants
// ---------------------------------------------------------------------------

const INPUT: React.CSSProperties = {
  padding: '0.3rem 0.5rem',
  borderRadius: '4px',
  border: '1px solid #e5e7eb',
  fontSize: '0.85rem',
  fontFamily: 'inherit',
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

const BTN_PRIMARY: React.CSSProperties = {
  ...BTN,
  background: '#3b82d4',
  color: '#fff',
  border: 'none',
}

const CARD: React.CSSProperties = {
  background: '#f7f8fa',
  border: '1px solid #e5e7eb',
  borderRadius: '6px',
  padding: '1rem',
  marginBottom: '1rem',
}

// ---------------------------------------------------------------------------
// Readiness badge
// ---------------------------------------------------------------------------

const READINESS_STYLE: Record<string, React.CSSProperties> = {
  blocked: { background: '#fee2e2', color: '#991b1b', borderColor: '#fca5a5' },
  ready_with_warnings: { background: '#fef9c3', color: '#854d0e', borderColor: '#fde047' },
  ready: { background: '#dcfce7', color: '#166534', borderColor: '#86efac' },
}

const READINESS_LABEL: Record<string, string> = {
  blocked: 'Blocked',
  ready_with_warnings: 'Ready with Warnings',
  ready: 'Ready',
}

const SEVERITY_STYLE: Record<string, React.CSSProperties> = {
  blocker: { background: '#fee2e2', color: '#991b1b' },
  warning: { background: '#fef9c3', color: '#854d0e' },
  info: { background: '#dbeafe', color: '#1e40af' },
}

function ReadinessBadge({ status }: { status: string }) {
  const style = READINESS_STYLE[status] ?? READINESS_STYLE.blocked
  return (
    <span
      style={{
        display: 'inline-block',
        padding: '0.25rem 0.75rem',
        borderRadius: '4px',
        fontWeight: 700,
        fontSize: '0.875rem',
        border: `1px solid ${(style as { borderColor?: string }).borderColor ?? '#e5e7eb'}`,
        ...style,
      }}
    >
      {READINESS_LABEL[status] ?? status}
    </span>
  )
}

// ---------------------------------------------------------------------------
// Score bar
// ---------------------------------------------------------------------------

function ScoreBar({ score }: { score: number }) {
  const color = score === 0 ? '#ef4444' : score < 60 ? '#f59e0b' : '#22c55e'
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', margin: '0.5rem 0' }}>
      <span style={{ fontWeight: 700, fontSize: '1.5rem', minWidth: '3rem' }}>{score}</span>
      <div
        style={{
          flex: 1,
          height: '8px',
          background: '#e5e7eb',
          borderRadius: '4px',
          overflow: 'hidden',
        }}
      >
        <div
          style={{
            height: '100%',
            width: `${score}%`,
            background: color,
            borderRadius: '4px',
            transition: 'width 0.4s ease',
          }}
        />
      </div>
      <span style={{ fontSize: '0.75rem', color: '#57606a' }}>/ 100</span>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Severity badge
// ---------------------------------------------------------------------------

function SeverityBadge({ severity }: { severity: string }) {
  const style = SEVERITY_STYLE[severity] ?? SEVERITY_STYLE.info
  return (
    <span
      style={{
        display: 'inline-block',
        padding: '0.1rem 0.4rem',
        borderRadius: '3px',
        fontSize: '0.7rem',
        fontWeight: 700,
        textTransform: 'uppercase',
        ...style,
      }}
    >
      {severity}
    </span>
  )
}

// ---------------------------------------------------------------------------
// Acknowledge modal
// ---------------------------------------------------------------------------

interface AcknowledgeModalProps {
  finding: Finding
  onConfirm: (reason: string, expiresAt: string | null) => void
  onCancel: () => void
}

function AcknowledgeModal({ finding, onConfirm, onCancel }: AcknowledgeModalProps) {
  const [reason, setReason] = useState('')
  const [expiry, setExpiry] = useState('')

  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        background: 'rgba(0,0,0,0.35)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        zIndex: 1000,
      }}
    >
      <div
        style={{
          background: '#fff',
          border: '1px solid #e5e7eb',
          borderRadius: '8px',
          padding: '1.5rem',
          width: '420px',
          maxWidth: '90vw',
        }}
      >
        <h3 style={{ margin: '0 0 0.5rem', fontSize: '1rem', fontWeight: 700 }}>
          Acknowledge finding
        </h3>
        <p style={{ margin: '0 0 1rem', fontSize: '0.85rem', color: '#57606a' }}>
          <strong>{finding.rule_id}</strong> — {finding.title}
        </p>

        <label style={{ display: 'block', marginBottom: '0.25rem', fontSize: '0.8rem', fontWeight: 600 }}>
          Reason <span style={{ color: '#ef4444' }}>*</span>
        </label>
        <textarea
          style={{ ...INPUT, width: '100%', minHeight: '80px', resize: 'vertical', boxSizing: 'border-box' }}
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          placeholder="Why is this finding acceptable?"
        />

        <label style={{ display: 'block', margin: '0.75rem 0 0.25rem', fontSize: '0.8rem', fontWeight: 600 }}>
          Expires (optional)
        </label>
        <input
          type="datetime-local"
          style={{ ...INPUT, width: '100%', boxSizing: 'border-box' }}
          value={expiry}
          onChange={(e) => setExpiry(e.target.value)}
        />

        <div style={{ display: 'flex', gap: '0.5rem', marginTop: '1rem', justifyContent: 'flex-end' }}>
          <button style={BTN} onClick={onCancel}>Cancel</button>
          <button
            style={{ ...BTN_PRIMARY, opacity: reason.trim() ? 1 : 0.5 }}
            disabled={!reason.trim()}
            onClick={() => onConfirm(reason.trim(), expiry ? new Date(expiry).toISOString() : null)}
          >
            Acknowledge
          </button>
        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Finding card
// ---------------------------------------------------------------------------

interface FindingCardProps {
  finding: Finding
  onAcknowledge: (f: Finding) => void
  onUnacknowledge: (f: Finding) => void
}

function FindingCard({ finding, onAcknowledge, onUnacknowledge }: FindingCardProps) {
  const [evidenceOpen, setEvidenceOpen] = useState(false)

  return (
    <div
      style={{
        border: '1px solid #e5e7eb',
        borderRadius: '6px',
        padding: '0.75rem 1rem',
        marginBottom: '0.5rem',
        background: finding.acknowledged ? '#f7f8fa' : '#fff',
        opacity: finding.acknowledged ? 0.75 : 1,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: '0.5rem' }}>
        <div style={{ flex: 1 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.25rem' }}>
            <SeverityBadge severity={finding.severity} />
            <span style={{ fontSize: '0.75rem', color: '#57606a', fontFamily: 'monospace' }}>
              {finding.rule_id}
            </span>
            {finding.acknowledged && (
              <span
                style={{
                  fontSize: '0.7rem',
                  background: '#e0e7ff',
                  color: '#3730a3',
                  padding: '0.1rem 0.4rem',
                  borderRadius: '3px',
                }}
              >
                Acknowledged
              </span>
            )}
          </div>
          <div style={{ fontWeight: 600, fontSize: '0.875rem', marginBottom: '0.25rem' }}>
            {finding.title}
          </div>
          <div style={{ fontSize: '0.8rem', color: '#374151', marginBottom: '0.35rem' }}>
            {finding.message}
          </div>
          <div style={{ fontSize: '0.78rem', color: '#57606a', marginBottom: '0.25rem' }}>
            <strong>Remediation:</strong> {finding.remediation}
          </div>
          {finding.acknowledged && finding.acknowledged_reason && (
            <div style={{ fontSize: '0.78rem', color: '#57606a', marginTop: '0.25rem', fontStyle: 'italic' }}>
              Acknowledged: {finding.acknowledged_reason}
            </div>
          )}
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.25rem', flexShrink: 0 }}>
          {finding.docs_url && (
            <a
              href={finding.docs_url}
              target="_blank"
              rel="noopener noreferrer"
              style={{ fontSize: '0.75rem', color: '#3b82d4' }}
            >
              Docs ↗
            </a>
          )}
          {!finding.acknowledged ? (
            <button
              style={{ ...BTN, fontSize: '0.75rem', padding: '0.2rem 0.5rem' }}
              onClick={() => onAcknowledge(finding)}
            >
              Acknowledge
            </button>
          ) : (
            <button
              style={{ ...BTN, fontSize: '0.75rem', padding: '0.2rem 0.5rem', color: '#57606a' }}
              onClick={() => onUnacknowledge(finding)}
            >
              Remove ack
            </button>
          )}
        </div>
      </div>

      {/* Evidence (collapsible) */}
      {Object.keys(finding.evidence).length > 0 && (
        <div style={{ marginTop: '0.5rem' }}>
          <button
            style={{
              background: 'none',
              border: 'none',
              padding: 0,
              cursor: 'pointer',
              fontSize: '0.75rem',
              color: '#57606a',
              fontFamily: 'inherit',
            }}
            onClick={() => setEvidenceOpen((v) => !v)}
          >
            {evidenceOpen ? '▾' : '▸'} Evidence
          </button>
          {evidenceOpen && (
            <pre
              style={{
                marginTop: '0.35rem',
                padding: '0.5rem',
                background: '#f3f4f6',
                borderRadius: '4px',
                fontSize: '0.72rem',
                overflowX: 'auto',
                lineHeight: 1.5,
              }}
            >
              {JSON.stringify(finding.evidence, null, 2)}
            </pre>
          )}
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main Assessment page
// ---------------------------------------------------------------------------

type FindingFilter = 'all' | 'hide_info' | 'blockers_only'

const Assessment: React.FC = () => {
  // Resource search
  const [resourceSearch, setResourceSearch] = useState('')
  const [resourceList, setResourceList] = useState<ResourceSummary[]>([])
  const [selectedResource, setSelectedResource] = useState<ResourceSummary | null>(null)

  // Target
  const [targetProvider, setTargetProvider] = useState('azure')
  const [targetRegion, setTargetRegion] = useState('')

  // Assessment result
  const [result, setResult] = useState<AssessmentResult | null>(null)
  const [history, setHistory] = useState<AssessmentHistoryItem[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Filter
  const [filter, setFilter] = useState<FindingFilter>('all')

  // Acknowledge modal
  const [ackFinding, setAckFinding] = useState<Finding | null>(null)

  // Load VM list for search
  useEffect(() => {
    if (!resourceSearch) {
      setResourceList([])
      return
    }
    const timer = setTimeout(async () => {
      try {
        const resp = await inventoryApi.listVms({ limit: 20, offset: 0 })
        const filtered = resp.items.filter(
          (vm) =>
            (vm.name ?? vm.native_id).toLowerCase().includes(resourceSearch.toLowerCase()),
        )
        setResourceList(filtered)
      } catch {
        setResourceList([])
      }
    }, 300)
    return () => clearTimeout(timer)
  }, [resourceSearch])

  const handleSelectResource = (vm: ResourceSummary) => {
    setSelectedResource(vm)
    setResourceSearch(vm.name ?? vm.native_id)
    setResourceList([])
    setResult(null)
    setError(null)
    // Load history
    assessmentApi.getResults(vm.id).then(setHistory).catch(() => setHistory([]))
  }

  const handleRun = useCallback(async () => {
    if (!selectedResource || !targetProvider || !targetRegion) return
    setLoading(true)
    setError(null)
    try {
      const res = await assessmentApi.runAssessment(
        selectedResource.id,
        targetProvider,
        targetRegion,
      )
      setResult(res)
      assessmentApi.getResults(selectedResource.id).then(setHistory).catch(() => {})
    } catch (err: unknown) {
      const msg = (err as { error?: string })?.error ?? 'Assessment failed'
      setError(msg)
    } finally {
      setLoading(false)
    }
  }, [selectedResource, targetProvider, targetRegion])

  const handleAcknowledge = async (finding: Finding, reason: string, expiresAt: string | null) => {
    if (!selectedResource) return
    try {
      await assessmentApi.acknowledge(selectedResource.id, finding.rule_id, {
        reason,
        expires_at: expiresAt,
      })
      // Re-run to get updated findings
      await handleRun()
    } catch {
      setError('Failed to acknowledge finding')
    }
    setAckFinding(null)
  }

  const handleUnacknowledge = async (finding: Finding) => {
    if (!selectedResource) return
    try {
      await assessmentApi.deleteAcknowledgement(selectedResource.id, finding.rule_id)
      await handleRun()
    } catch {
      setError('Failed to remove acknowledgement')
    }
  }

  // Filter findings
  const filteredFindings = result?.findings.filter((f) => {
    if (filter === 'hide_info') return f.severity !== 'info'
    if (filter === 'blockers_only') return f.severity === 'blocker'
    return true
  }) ?? []

  // Group by severity
  const blockers = filteredFindings.filter((f) => f.severity === 'blocker')
  const warnings = filteredFindings.filter((f) => f.severity === 'warning')
  const infos = filteredFindings.filter((f) => f.severity === 'info')

  return (
    <div>
      <h1 style={{ fontSize: '1.25rem', fontWeight: 700, marginBottom: '1.25rem' }}>
        Assessment
      </h1>

      {/* Controls */}
      <div style={CARD}>
        <div style={{ display: 'flex', gap: '0.75rem', flexWrap: 'wrap', alignItems: 'flex-end' }}>
          {/* Resource search */}
          <div style={{ position: 'relative', flex: '1 1 220px' }}>
            <label style={{ display: 'block', fontSize: '0.78rem', fontWeight: 600, marginBottom: '0.25rem' }}>
              Resource
            </label>
            <input
              style={{ ...INPUT, width: '100%', boxSizing: 'border-box' }}
              placeholder="Search VM by name…"
              value={resourceSearch}
              onChange={(e) => {
                setResourceSearch(e.target.value)
                if (!e.target.value) setSelectedResource(null)
              }}
            />
            {resourceList.length > 0 && (
              <div
                style={{
                  position: 'absolute',
                  top: '100%',
                  left: 0,
                  right: 0,
                  background: '#fff',
                  border: '1px solid #e5e7eb',
                  borderRadius: '4px',
                  zIndex: 50,
                  maxHeight: '200px',
                  overflowY: 'auto',
                }}
              >
                {resourceList.map((vm) => (
                  <div
                    key={vm.id}
                    style={{
                      padding: '0.4rem 0.65rem',
                      cursor: 'pointer',
                      fontSize: '0.85rem',
                      borderBottom: '1px solid #f3f4f6',
                    }}
                    onClick={() => handleSelectResource(vm)}
                    onMouseEnter={(e) => ((e.target as HTMLElement).style.background = '#f7f8fa')}
                    onMouseLeave={(e) => ((e.target as HTMLElement).style.background = '')}
                  >
                    <span style={{ fontWeight: 500 }}>{vm.name ?? vm.native_id}</span>
                    <span style={{ color: '#57606a', marginLeft: '0.5rem', fontSize: '0.75rem' }}>
                      {vm.provider.toUpperCase()} · {vm.region}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Target provider */}
          <div style={{ flex: '0 0 auto' }}>
            <label style={{ display: 'block', fontSize: '0.78rem', fontWeight: 600, marginBottom: '0.25rem' }}>
              Target Provider
            </label>
            <select
              style={INPUT}
              value={targetProvider}
              onChange={(e) => setTargetProvider(e.target.value)}
            >
              <option value="azure">Azure</option>
              <option value="aws">AWS</option>
              <option value="gcp">GCP</option>
              <option value="ibm">IBM</option>
            </select>
          </div>

          {/* Target region */}
          <div style={{ flex: '1 1 140px' }}>
            <label style={{ display: 'block', fontSize: '0.78rem', fontWeight: 600, marginBottom: '0.25rem' }}>
              Target Region
            </label>
            <input
              style={{ ...INPUT, width: '100%', boxSizing: 'border-box' }}
              placeholder="e.g. eastus"
              value={targetRegion}
              onChange={(e) => setTargetRegion(e.target.value)}
            />
          </div>

          {/* Run button */}
          <div style={{ flex: '0 0 auto', paddingTop: '1.2rem' }}>
            <button
              style={{
                ...BTN_PRIMARY,
                padding: '0.4rem 1rem',
                opacity: selectedResource && targetProvider && targetRegion && !loading ? 1 : 0.5,
              }}
              disabled={!selectedResource || !targetProvider || !targetRegion || loading}
              onClick={handleRun}
            >
              {loading ? 'Running…' : 'Run Assessment'}
            </button>
          </div>
        </div>

        {error && (
          <div style={{ marginTop: '0.75rem', color: '#991b1b', fontSize: '0.85rem' }}>
            {error}
          </div>
        )}
      </div>

      {/* Assessment result */}
      {result && (
        <>
          {/* Readiness summary */}
          <div style={CARD}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '1rem', flexWrap: 'wrap' }}>
              <ReadinessBadge status={result.readiness.status} />
              <div style={{ flex: '1 1 200px' }}>
                <ScoreBar score={result.readiness.score} />
              </div>
              <div style={{ display: 'flex', gap: '1rem', fontSize: '0.8rem', color: '#57606a' }}>
                <span style={{ color: '#991b1b', fontWeight: 600 }}>
                  {result.readiness.blockers} blocker(s)
                </span>
                <span style={{ color: '#854d0e', fontWeight: 600 }}>
                  {result.readiness.warnings} warning(s)
                </span>
                <span style={{ color: '#1e40af' }}>
                  {result.readiness.info} info
                </span>
              </div>
            </div>
            <div style={{ marginTop: '0.5rem', fontSize: '0.75rem', color: '#57606a' }}>
              Target: <strong>{result.target_provider.toUpperCase()}/{result.target_region}</strong>
              {' · '}Catalog version: <strong>{result.catalog_version}</strong>
              {' · '}Snapshot: <code style={{ fontSize: '0.7rem' }}>{result.snapshot_id.slice(0, 8) || '—'}…</code>
            </div>
          </div>

          {/* Filter bar */}
          <div style={{ display: 'flex', gap: '0.5rem', marginBottom: '0.75rem' }}>
            {(['all', 'hide_info', 'blockers_only'] as const).map((f) => (
              <button
                key={f}
                style={{
                  ...BTN,
                  background: filter === f ? '#3b82d4' : '#f7f8fa',
                  color: filter === f ? '#fff' : '#1f2328',
                  border: filter === f ? 'none' : '1px solid #e5e7eb',
                }}
                onClick={() => setFilter(f)}
              >
                {{ all: 'Show all', hide_info: 'Hide info', blockers_only: 'Blockers only' }[f]}
              </button>
            ))}
          </div>

          {/* Findings grouped by severity */}
          {blockers.length > 0 && (
            <section style={{ marginBottom: '1rem' }}>
              <h3 style={{ fontSize: '0.9rem', fontWeight: 700, color: '#991b1b', marginBottom: '0.5rem' }}>
                Blockers ({blockers.length})
              </h3>
              {blockers.map((f) => (
                <FindingCard
                  key={f.rule_id}
                  finding={f}
                  onAcknowledge={(finding) => setAckFinding(finding)}
                  onUnacknowledge={handleUnacknowledge}
                />
              ))}
            </section>
          )}

          {filter !== 'blockers_only' && warnings.length > 0 && (
            <section style={{ marginBottom: '1rem' }}>
              <h3 style={{ fontSize: '0.9rem', fontWeight: 700, color: '#854d0e', marginBottom: '0.5rem' }}>
                Warnings ({warnings.length})
              </h3>
              {warnings.map((f) => (
                <FindingCard
                  key={f.rule_id}
                  finding={f}
                  onAcknowledge={(finding) => setAckFinding(finding)}
                  onUnacknowledge={handleUnacknowledge}
                />
              ))}
            </section>
          )}

          {filter === 'all' && infos.length > 0 && (
            <section style={{ marginBottom: '1rem' }}>
              <h3 style={{ fontSize: '0.9rem', fontWeight: 700, color: '#1e40af', marginBottom: '0.5rem' }}>
                Info ({infos.length})
              </h3>
              {infos.map((f) => (
                <FindingCard
                  key={f.rule_id}
                  finding={f}
                  onAcknowledge={(finding) => setAckFinding(finding)}
                  onUnacknowledge={handleUnacknowledge}
                />
              ))}
            </section>
          )}

          {filteredFindings.length === 0 && (
            <p style={{ color: '#57606a', fontSize: '0.875rem' }}>
              No findings match the current filter.
            </p>
          )}
        </>
      )}

      {/* Assessment history */}
      {selectedResource && history.length > 0 && !result && (
        <div style={CARD}>
          <h3 style={{ margin: '0 0 0.75rem', fontSize: '0.9rem', fontWeight: 700 }}>
            Past Assessments
          </h3>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.8rem' }}>
            <thead>
              <tr style={{ color: '#57606a', fontSize: '0.75rem', textTransform: 'uppercase', borderBottom: '1px solid #e5e7eb' }}>
                <th style={{ padding: '0.4rem 0.5rem', textAlign: 'left' }}>Target</th>
                <th style={{ padding: '0.4rem 0.5rem', textAlign: 'left' }}>Readiness</th>
                <th style={{ padding: '0.4rem 0.5rem', textAlign: 'right' }}>Score</th>
                <th style={{ padding: '0.4rem 0.5rem', textAlign: 'right' }}>B / W / I</th>
                <th style={{ padding: '0.4rem 0.5rem', textAlign: 'left' }}>Date</th>
              </tr>
            </thead>
            <tbody>
              {history.map((h) => (
                <tr key={h.id} style={{ borderBottom: '1px solid #f3f4f6' }}>
                  <td style={{ padding: '0.4rem 0.5rem' }}>
                    {h.target_provider.toUpperCase()}/{h.target_region}
                  </td>
                  <td style={{ padding: '0.4rem 0.5rem' }}>
                    <ReadinessBadge status={h.readiness} />
                  </td>
                  <td style={{ padding: '0.4rem 0.5rem', textAlign: 'right', fontWeight: 600 }}>
                    {h.readiness_score}
                  </td>
                  <td style={{ padding: '0.4rem 0.5rem', textAlign: 'right', color: '#57606a' }}>
                    <span style={{ color: '#991b1b' }}>{h.blocker_count}</span>
                    {' / '}
                    <span style={{ color: '#854d0e' }}>{h.warning_count}</span>
                    {' / '}
                    {h.info_count}
                  </td>
                  <td style={{ padding: '0.4rem 0.5rem', color: '#57606a' }}>
                    {new Date(h.created_at).toLocaleDateString()}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Acknowledge modal */}
      {ackFinding && (
        <AcknowledgeModal
          finding={ackFinding}
          onConfirm={(reason, expiresAt) => handleAcknowledge(ackFinding, reason, expiresAt)}
          onCancel={() => setAckFinding(null)}
        />
      )}
    </div>
  )
}

export default Assessment
