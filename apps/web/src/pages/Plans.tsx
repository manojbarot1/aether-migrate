/**
 * Plans page — list, create, and review migration plans.
 *
 * Views:
 *  - List:   table of all plans with status badges and actions
 *  - Create: 4-step wizard (select VMs → target → sizing → review)
 *  - Detail: full plan document with steps, findings, approval UX
 */

import React, { useCallback, useEffect, useState } from 'react'
import plansApi, {
  type PlanSummary,
  type PlanDetail,
  type PlanCreateRequest,
  type PlanStep,
} from '../api/plans'
import inventoryApi, { type ResourceSummary } from '../api/inventory'

// ---------------------------------------------------------------------------
// Style constants (matching project palette)
// ---------------------------------------------------------------------------

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

const INPUT: React.CSSProperties = {
  padding: '0.3rem 0.5rem',
  borderRadius: '4px',
  border: '1px solid #e5e7eb',
  fontSize: '0.85rem',
  fontFamily: 'inherit',
  width: '100%',
  boxSizing: 'border-box',
}

const TH: React.CSSProperties = {
  textAlign: 'left',
  padding: '0.5rem 0.75rem',
  borderBottom: '1px solid #e5e7eb',
  color: '#57606a',
  fontWeight: 600,
  fontSize: '0.8rem',
}

const TD: React.CSSProperties = {
  padding: '0.5rem 0.75rem',
  borderBottom: '1px solid #e5e7eb',
  fontSize: '0.85rem',
  verticalAlign: 'top',
}

// ---------------------------------------------------------------------------
// Status badge
// ---------------------------------------------------------------------------

const STATUS_STYLE: Record<string, React.CSSProperties> = {
  draft: { background: '#f3f4f6', color: '#374151', border: '1px solid #d1d5db' },
  in_review: { background: '#fef9c3', color: '#854d0e', border: '1px solid #fde047' },
  approved: { background: '#dcfce7', color: '#166534', border: '1px solid #86efac' },
  superseded: { background: '#e5e7eb', color: '#6b7280', border: '1px solid #d1d5db' },
  executed: { background: '#eff6ff', color: '#1d4ed8', border: '1px solid #93c5fd' },
}

const StatusBadge: React.FC<{ status: string }> = ({ status }) => (
  <span
    style={{
      ...STATUS_STYLE[status],
      padding: '0.15rem 0.5rem',
      borderRadius: '4px',
      fontSize: '0.75rem',
      fontWeight: 600,
      whiteSpace: 'nowrap',
    }}
  >
    {status.replace('_', ' ').toUpperCase()}
  </span>
)

// ---------------------------------------------------------------------------
// Plans list view
// ---------------------------------------------------------------------------

interface PlanListProps {
  onSelect: (id: string) => void
  onCreateNew: () => void
}

const PlanList: React.FC<PlanListProps> = ({ onSelect, onCreateNew }) => {
  const [plans, setPlans] = useState<PlanSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    plansApi.list()
      .then(setPlans)
      .catch((e) => setError(e.error ?? 'Failed to load plans'))
      .finally(() => setLoading(false))
  }, [])

  if (loading) return <p style={{ color: '#57606a' }}>Loading plans…</p>
  if (error) return <p style={{ color: '#dc2626' }}>Error: {error}</p>

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1rem' }}>
        <h2 style={{ fontSize: '1.1rem', margin: 0 }}>Migration Plans</h2>
        <button style={BTN_PRIMARY} onClick={onCreateNew}>+ Create Plan</button>
      </div>

      {plans.length === 0 ? (
        <div style={CARD}>
          <p style={{ color: '#57606a', margin: 0 }}>
            No plans yet. Create a plan to generate a migration roadmap for your VMs.
          </p>
        </div>
      ) : (
        <table style={{ width: '100%', borderCollapse: 'collapse', background: '#fff', borderRadius: '6px', overflow: 'hidden', border: '1px solid #e5e7eb' }}>
          <thead style={{ background: '#f7f8fa' }}>
            <tr>
              <th style={TH}>Name</th>
              <th style={TH}>Target</th>
              <th style={TH}>VMs</th>
              <th style={TH}>Version</th>
              <th style={TH}>Status</th>
              <th style={TH}>Hash</th>
              <th style={TH}>Created</th>
              <th style={TH}></th>
            </tr>
          </thead>
          <tbody>
            {plans.map((p) => (
              <tr key={p.id} style={{ cursor: 'pointer' }} onClick={() => onSelect(p.id)}>
                <td style={TD}>
                  <strong style={{ fontSize: '0.875rem' }}>{p.name}</strong>
                  {p.description && (
                    <div style={{ color: '#57606a', fontSize: '0.8rem', marginTop: '2px' }}>{p.description}</div>
                  )}
                </td>
                <td style={TD}>{p.target_provider}/{p.target_region}</td>
                <td style={TD}>{p.source_resource_count}</td>
                <td style={TD}>v{p.version}</td>
                <td style={TD}><StatusBadge status={p.status} /></td>
                <td style={{ ...TD, fontFamily: 'monospace', fontSize: '0.75rem', color: '#57606a' }}>
                  {p.content_hash ? p.content_hash.slice(0, 8) : '—'}
                </td>
                <td style={TD}>{new Date(p.created_at).toLocaleDateString()}</td>
                <td style={TD}>
                  <button
                    style={BTN}
                    onClick={(e) => { e.stopPropagation(); onSelect(p.id) }}
                  >
                    View
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Create plan wizard
// ---------------------------------------------------------------------------

const PROVIDERS = ['aws', 'azure', 'gcp', 'ibm']
const REGIONS: Record<string, string[]> = {
  azure: ['eastus', 'westus2', 'westeurope', 'northeurope', 'southeastasia', 'uksouth'],
  aws: ['us-east-1', 'us-west-2', 'eu-west-1', 'eu-central-1', 'ap-southeast-1'],
  gcp: ['us-central1', 'europe-west1', 'asia-east1'],
  ibm: ['us-south', 'eu-de', 'eu-gb'],
}
const STRATEGIES = [
  { value: 'right_sized', label: 'Right-sized (p95 metrics + 30% headroom)' },
  { value: 'like_for_like', label: 'Like-for-like (match source vCPU/memory)' },
  { value: 'cheapest_fit', label: 'Cheapest fit (minimum requirements, lowest price)' },
]

interface WizardState {
  step: 1 | 2 | 3 | 4
  selectedVMs: ResourceSummary[]
  targetProvider: string
  targetRegion: string
  sizingStrategy: string
  planName: string
  description: string
  bandwidthMbps: string
}

interface CreatePlanWizardProps {
  onCreated: (id: string) => void
  onCancel: () => void
}

const CreatePlanWizard: React.FC<CreatePlanWizardProps> = ({ onCreated, onCancel }) => {
  const [state, setState] = useState<WizardState>({
    step: 1,
    selectedVMs: [],
    targetProvider: 'azure',
    targetRegion: 'eastus',
    sizingStrategy: 'right_sized',
    planName: '',
    description: '',
    bandwidthMbps: '',
  })
  const [vms, setVMs] = useState<ResourceSummary[]>([])
  const [loadingVMs, setLoadingVMs] = useState(true)
  const [creating, setCreating] = useState(false)
  const [createError, setCreateError] = useState<string | null>(null)

  useEffect(() => {
    inventoryApi.listVms({ limit: 100 })
      .then((resp) => setVMs(resp.items))
      .catch(() => setVMs([]))
      .finally(() => setLoadingVMs(false))
  }, [])

  const update = (patch: Partial<WizardState>) =>
    setState((s) => ({ ...s, ...patch }))

  const toggleVM = (vm: ResourceSummary) => {
    const exists = state.selectedVMs.some((v) => v.id === vm.id)
    update({
      selectedVMs: exists
        ? state.selectedVMs.filter((v) => v.id !== vm.id)
        : [...state.selectedVMs, vm],
    })
  }

  const handleCreate = async () => {
    setCreating(true)
    setCreateError(null)
    const req: PlanCreateRequest = {
      resource_ids: state.selectedVMs.map((v) => v.id),
      name: state.planName || undefined,
      description: state.description || undefined,
      target_provider: state.targetProvider,
      target_region: state.targetRegion,
      sizing_strategy: state.sizingStrategy,
      assumed_bandwidth_mbps: state.bandwidthMbps ? parseFloat(state.bandwidthMbps) : undefined,
    }
    try {
      const plan = await plansApi.create(req)
      onCreated(plan.id)
    } catch (e: unknown) {
      const err = e as { error?: string }
      setCreateError(err.error ?? 'Failed to create plan')
    } finally {
      setCreating(false)
    }
  }

  const regions = REGIONS[state.targetProvider] || []

  return (
    <div>
      <div style={{ display: 'flex', gap: '0.5rem', marginBottom: '1.5rem', alignItems: 'center' }}>
        <button style={BTN} onClick={onCancel}>← Back</button>
        <h2 style={{ margin: 0, fontSize: '1.1rem' }}>Create Migration Plan</h2>
      </div>

      {/* Step indicator */}
      <div style={{ display: 'flex', gap: '0.5rem', marginBottom: '1.5rem' }}>
        {([1, 2, 3, 4] as const).map((n) => (
          <div
            key={n}
            style={{
              padding: '0.25rem 0.75rem',
              borderRadius: '4px',
              fontSize: '0.8rem',
              fontWeight: 600,
              background: state.step === n ? '#3b82d4' : state.step > n ? '#dcfce7' : '#f7f8fa',
              color: state.step === n ? '#fff' : state.step > n ? '#166534' : '#57606a',
              border: '1px solid #e5e7eb',
            }}
          >
            {n}. {['Select VMs', 'Target', 'Sizing', 'Review'][n - 1]}
          </div>
        ))}
      </div>

      {/* Step 1: Select VMs */}
      {state.step === 1 && (
        <div style={CARD}>
          <h3 style={{ marginTop: 0, fontSize: '0.95rem' }}>Select source VMs</h3>
          {loadingVMs ? (
            <p style={{ color: '#57606a' }}>Loading VMs…</p>
          ) : vms.length === 0 ? (
            <p style={{ color: '#57606a' }}>No VMs found. Run discovery first.</p>
          ) : (
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr>
                  <th style={TH}></th>
                  <th style={TH}>Name</th>
                  <th style={TH}>Provider</th>
                  <th style={TH}>Region</th>
                  <th style={TH}>Instance Type</th>
                </tr>
              </thead>
              <tbody>
                {vms.map((vm) => {
                  const sel = state.selectedVMs.some((v) => v.id === vm.id)
                  return (
                    <tr
                      key={vm.id}
                      onClick={() => toggleVM(vm)}
                      style={{ cursor: 'pointer', background: sel ? '#eff6ff' : 'transparent' }}
                    >
                      <td style={TD}>
                        <input type="checkbox" checked={sel} onChange={() => toggleVM(vm)} onClick={(e) => e.stopPropagation()} />
                      </td>
                      <td style={TD}>{vm.name || vm.native_id}</td>
                      <td style={TD}>{vm.provider}</td>
                      <td style={TD}>{vm.region}</td>
                      <td style={{ ...TD, fontFamily: 'monospace', fontSize: '0.8rem' }}>
                        {(vm.spec as Record<string, unknown>)?.instance_type as string || '—'}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          )}
          <div style={{ marginTop: '1rem', display: 'flex', justifyContent: 'flex-end' }}>
            <button
              style={{ ...BTN_PRIMARY, opacity: state.selectedVMs.length === 0 ? 0.5 : 1 }}
              disabled={state.selectedVMs.length === 0}
              onClick={() => update({ step: 2 })}
            >
              Next ({state.selectedVMs.length} VM{state.selectedVMs.length !== 1 ? 's' : ''} selected) →
            </button>
          </div>
        </div>
      )}

      {/* Step 2: Target provider + region */}
      {state.step === 2 && (
        <div style={CARD}>
          <h3 style={{ marginTop: 0, fontSize: '0.95rem' }}>Select target provider and region</h3>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1rem', marginBottom: '1rem' }}>
            <div>
              <label style={{ display: 'block', marginBottom: '0.25rem', fontSize: '0.85rem', fontWeight: 600 }}>
                Target Provider
              </label>
              <select
                style={INPUT}
                value={state.targetProvider}
                onChange={(e) => {
                  const p = e.target.value
                  update({ targetProvider: p, targetRegion: (REGIONS[p] || [])[0] || '' })
                }}
              >
                {PROVIDERS.map((p) => <option key={p} value={p}>{p.toUpperCase()}</option>)}
              </select>
            </div>
            <div>
              <label style={{ display: 'block', marginBottom: '0.25rem', fontSize: '0.85rem', fontWeight: 600 }}>
                Target Region
              </label>
              <select
                style={INPUT}
                value={state.targetRegion}
                onChange={(e) => update({ targetRegion: e.target.value })}
              >
                {regions.map((r) => <option key={r} value={r}>{r}</option>)}
              </select>
            </div>
          </div>
          <div style={{ display: 'flex', justifyContent: 'space-between' }}>
            <button style={BTN} onClick={() => update({ step: 1 })}>← Back</button>
            <button style={BTN_PRIMARY} onClick={() => update({ step: 3 })}>Next →</button>
          </div>
        </div>
      )}

      {/* Step 3: Sizing strategy */}
      {state.step === 3 && (
        <div style={CARD}>
          <h3 style={{ marginTop: 0, fontSize: '0.95rem' }}>Sizing strategy</h3>
          {STRATEGIES.map((s) => (
            <label
              key={s.value}
              style={{
                display: 'flex',
                alignItems: 'flex-start',
                gap: '0.5rem',
                padding: '0.5rem',
                marginBottom: '0.5rem',
                borderRadius: '4px',
                background: state.sizingStrategy === s.value ? '#eff6ff' : 'transparent',
                cursor: 'pointer',
                border: state.sizingStrategy === s.value ? '1px solid #93c5fd' : '1px solid transparent',
              }}
            >
              <input
                type="radio"
                name="strategy"
                value={s.value}
                checked={state.sizingStrategy === s.value}
                onChange={() => update({ sizingStrategy: s.value })}
                style={{ marginTop: '3px' }}
              />
              <span style={{ fontSize: '0.85rem' }}>{s.label}</span>
            </label>
          ))}

          <div style={{ marginTop: '1rem' }}>
            <label style={{ display: 'block', marginBottom: '0.25rem', fontSize: '0.85rem', fontWeight: 600 }}>
              Network bandwidth estimate (Mbps, optional)
            </label>
            <input
              type="number"
              style={{ ...INPUT, width: '200px' }}
              placeholder="e.g. 500"
              value={state.bandwidthMbps}
              onChange={(e) => update({ bandwidthMbps: e.target.value })}
            />
            <p style={{ fontSize: '0.8rem', color: '#57606a', margin: '0.25rem 0 0' }}>
              Used to estimate migration downtime. Leave blank if unknown.
            </p>
          </div>

          <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: '1rem' }}>
            <button style={BTN} onClick={() => update({ step: 2 })}>← Back</button>
            <button style={BTN_PRIMARY} onClick={() => update({ step: 4 })}>Next →</button>
          </div>
        </div>
      )}

      {/* Step 4: Review and create */}
      {state.step === 4 && (
        <div style={CARD}>
          <h3 style={{ marginTop: 0, fontSize: '0.95rem' }}>Review and create</h3>

          <div style={{ marginBottom: '1rem' }}>
            <label style={{ display: 'block', marginBottom: '0.25rem', fontSize: '0.85rem', fontWeight: 600 }}>
              Plan name (optional)
            </label>
            <input
              style={{ ...INPUT, width: '400px' }}
              type="text"
              placeholder="e.g. Prod web-tier migration to Azure eastus"
              value={state.planName}
              onChange={(e) => update({ planName: e.target.value })}
            />
          </div>

          <div style={{ marginBottom: '1rem' }}>
            <label style={{ display: 'block', marginBottom: '0.25rem', fontSize: '0.85rem', fontWeight: 600 }}>
              Description (optional)
            </label>
            <input
              style={{ ...INPUT, width: '400px' }}
              type="text"
              value={state.description}
              onChange={(e) => update({ description: e.target.value })}
            />
          </div>

          <div style={{ ...CARD, background: '#fff', marginBottom: '1rem' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.85rem' }}>
              <tbody>
                <tr>
                  <td style={{ ...TD, fontWeight: 600, width: '160px' }}>VMs</td>
                  <td style={TD}>{state.selectedVMs.map((v) => v.name || v.native_id).join(', ')}</td>
                </tr>
                <tr>
                  <td style={{ ...TD, fontWeight: 600 }}>Target</td>
                  <td style={TD}>{state.targetProvider}/{state.targetRegion}</td>
                </tr>
                <tr>
                  <td style={{ ...TD, fontWeight: 600 }}>Sizing strategy</td>
                  <td style={TD}>{STRATEGIES.find((s) => s.value === state.sizingStrategy)?.label}</td>
                </tr>
                {state.bandwidthMbps && (
                  <tr>
                    <td style={{ ...TD, fontWeight: 600 }}>Bandwidth</td>
                    <td style={TD}>{state.bandwidthMbps} Mbps</td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>

          {createError && (
            <div style={{ background: '#fee2e2', border: '1px solid #fca5a5', borderRadius: '4px', padding: '0.5rem', marginBottom: '1rem', color: '#991b1b', fontSize: '0.85rem' }}>
              {createError}
            </div>
          )}

          <div style={{ display: 'flex', justifyContent: 'space-between' }}>
            <button style={BTN} onClick={() => update({ step: 3 })}>← Back</button>
            <button style={BTN_PRIMARY} disabled={creating} onClick={handleCreate}>
              {creating ? 'Creating plan…' : 'Create Plan'}
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Plan detail view
// ---------------------------------------------------------------------------

interface PlanDetailViewProps {
  planId: string
  onBack: () => void
}

const StepRow: React.FC<{ step: PlanStep; expanded: boolean; toggle: () => void }> = ({ step, expanded, toggle }) => (
  <tr>
    <td colSpan={5} style={{ padding: 0 }}>
      <div
        onClick={toggle}
        style={{
          padding: '0.5rem 0.75rem',
          borderBottom: '1px solid #e5e7eb',
          cursor: 'pointer',
          background: expanded ? '#eff6ff' : 'transparent',
          display: 'grid',
          gridTemplateColumns: '40px 1fr 80px 80px 80px',
          alignItems: 'center',
          gap: '0.5rem',
        }}
      >
        <span style={{ fontSize: '0.8rem', fontWeight: 700, color: '#57606a' }}>
          #{step.step_number}
        </span>
        <span style={{ fontSize: '0.85rem', fontWeight: 600 }}>
          {step.title}
          {step.is_manual && (
            <span style={{ marginLeft: '0.4rem', fontSize: '0.7rem', background: '#fef9c3', color: '#854d0e', padding: '0.1rem 0.35rem', borderRadius: '3px', border: '1px solid #fde047' }}>
              MANUAL
            </span>
          )}
        </span>
        <span style={{ fontSize: '0.75rem', color: '#57606a' }}>{step.estimated_duration_minutes}m</span>
        <span style={{ fontSize: '0.75rem', color: '#57606a' }}>{expanded ? '▲' : '▼'}</span>
      </div>
      {expanded && (
        <div style={{ padding: '0.75rem 1rem 0.75rem 2.5rem', background: '#fafbfc', borderBottom: '1px solid #e5e7eb', fontSize: '0.85rem' }}>
          <p style={{ marginTop: 0 }}>{step.description}</p>
          <table style={{ borderCollapse: 'collapse', width: '100%' }}>
            <tbody>
              {([
                ['Pre-check', step.pre_check],
                ['Action', step.action],
                ['Post-check', step.post_check],
                ['Compensation', step.compensation],
              ] as [string, string][]).map(([label, value]) => (
                <tr key={label}>
                  <td style={{ padding: '0.25rem 0.5rem 0.25rem 0', fontWeight: 600, color: '#57606a', width: '120px', verticalAlign: 'top', whiteSpace: 'nowrap' }}>
                    {label}
                  </td>
                  <td style={{ padding: '0.25rem 0' }}>{value}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </td>
  </tr>
)

const PlanDetailView: React.FC<PlanDetailViewProps> = ({ planId, onBack }) => {
  const [plan, setPlan] = useState<PlanDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [expandedSteps, setExpandedSteps] = useState<Set<number>>(new Set())
  const [approving, setApproving] = useState(false)
  const [approveError, setApproveError] = useState<string | null>(null)
  const [generatingNarrative, setGeneratingNarrative] = useState(false)
  const [validating, setValidating] = useState(false)
  const [validationResult, setValidationResult] = useState<{ valid: boolean; errors: string[]; warnings: string[] } | null>(null)

  const load = useCallback(() => {
    setLoading(true)
    plansApi.get(planId)
      .then(setPlan)
      .catch((e) => setError(e.error ?? 'Failed to load plan'))
      .finally(() => setLoading(false))
  }, [planId])

  useEffect(() => { load() }, [load])

  const toggleStep = (n: number) =>
    setExpandedSteps((s) => {
      const next = new Set(s)
      if (next.has(n)) next.delete(n)
      else next.add(n)
      return next
    })

  const handleApprove = async () => {
    if (!plan) return
    setApproving(true)
    setApproveError(null)
    try {
      await plansApi.approve(plan.id)
      load()
    } catch (e: unknown) {
      const err = e as { error?: string }
      setApproveError(err.error ?? 'Approval failed')
    } finally {
      setApproving(false)
    }
  }

  const handleGenerateNarrative = async () => {
    if (!plan) return
    setGeneratingNarrative(true)
    try {
      const updated = await plansApi.generateNarrative(plan.id)
      setPlan(updated)
    } catch {
      // silent
    } finally {
      setGeneratingNarrative(false)
    }
  }

  const handleValidateTofu = async () => {
    if (!plan) return
    setValidating(true)
    setValidationResult(null)
    try {
      const result = await plansApi.validateTofu(plan.id)
      setValidationResult(result)
    } catch (e: unknown) {
      const err = e as { error?: string }
      setValidationResult({ valid: false, errors: [err.error ?? 'Validation failed'], warnings: [] })
    } finally {
      setValidating(false)
    }
  }

  if (loading) return <p style={{ color: '#57606a' }}>Loading plan…</p>
  if (error || !plan) return <p style={{ color: '#dc2626' }}>Error: {error || 'Plan not found'}</p>

  const doc = plan.plan_document
  const isApproved = plan.status === 'approved'

  return (
    <div>
      {/* Header */}
      <div style={{ display: 'flex', gap: '0.5rem', marginBottom: '1rem', alignItems: 'center' }}>
        <button style={BTN} onClick={onBack}>← Plans</button>
        <h2 style={{ margin: 0, fontSize: '1.1rem', flex: 1 }}>{plan.name}</h2>
        <StatusBadge status={plan.status} />
      </div>

      {/* Meta row */}
      <div style={{ ...CARD, display: 'flex', gap: '2rem', flexWrap: 'wrap', fontSize: '0.85rem' }}>
        <div><span style={{ color: '#57606a' }}>Target:</span> <strong>{plan.target_provider}/{plan.target_region}</strong></div>
        <div><span style={{ color: '#57606a' }}>Version:</span> <strong>v{plan.version}</strong></div>
        <div><span style={{ color: '#57606a' }}>Hash:</span> <code style={{ fontSize: '0.8rem' }}>{plan.content_hash?.slice(0, 8) ?? '—'}…</code></div>
        <div><span style={{ color: '#57606a' }}>Steps:</span> <strong>{doc.steps.length}</strong></div>
        <div><span style={{ color: '#57606a' }}>Created:</span> <strong>{new Date(plan.created_at).toLocaleDateString()}</strong></div>
      </div>

      {/* Download buttons */}
      <div style={{ display: 'flex', gap: '0.5rem', marginBottom: '1rem', flexWrap: 'wrap' }}>
        <a href={plansApi.exportJsonUrl(plan.id)} download style={{ textDecoration: 'none' }}>
          <button style={BTN}>↓ Download JSON</button>
        </a>
        <a href={plansApi.exportMarkdownUrl(plan.id)} download style={{ textDecoration: 'none' }}>
          <button style={BTN}>↓ Download Markdown</button>
        </a>
        <a href={plansApi.exportTofuUrl(plan.id)} download style={{ textDecoration: 'none' }}>
          <button style={BTN}>↓ Download OpenTofu ZIP</button>
        </a>
        <button style={BTN} disabled={validating} onClick={handleValidateTofu}>
          {validating ? 'Validating…' : '✓ Validate Tofu'}
        </button>
      </div>

      {/* Tofu validation result */}
      {validationResult && (
        <div style={{
          ...CARD,
          background: validationResult.valid ? '#dcfce7' : '#fee2e2',
          border: `1px solid ${validationResult.valid ? '#86efac' : '#fca5a5'}`,
        }}>
          <strong style={{ color: validationResult.valid ? '#166534' : '#991b1b' }}>
            {validationResult.valid ? '✓ OpenTofu module is valid' : '✗ OpenTofu validation failed'}
          </strong>
          {validationResult.errors.map((e, i) => (
            <p key={i} style={{ margin: '0.25rem 0 0', fontSize: '0.8rem', color: '#991b1b' }}>{e}</p>
          ))}
          {validationResult.warnings.map((w, i) => (
            <p key={i} style={{ margin: '0.25rem 0 0', fontSize: '0.8rem', color: '#854d0e' }}>{w}</p>
          ))}
        </div>
      )}

      {/* Prerequisites */}
      {doc.prerequisites.length > 0 && (
        <section style={{ marginBottom: '1.5rem' }}>
          <h3 style={{ fontSize: '0.95rem', marginBottom: '0.5rem' }}>Prerequisites</h3>
          <div style={CARD}>
            <ol style={{ margin: 0, paddingLeft: '1.2rem' }}>
              {doc.prerequisites.map((p, i) => (
                <li key={i} style={{ fontSize: '0.85rem', marginBottom: '0.25rem', color: p.startsWith('[MUST RESOLVE]') ? '#991b1b' : 'inherit' }}>{p}</li>
              ))}
            </ol>
          </div>
        </section>
      )}

      {/* Steps */}
      <section style={{ marginBottom: '1.5rem' }}>
        <h3 style={{ fontSize: '0.95rem', marginBottom: '0.5rem' }}>Migration Steps ({doc.steps.length})</h3>
        <div style={{ border: '1px solid #e5e7eb', borderRadius: '6px', overflow: 'hidden' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <tbody>
              {doc.steps.map((step) => (
                <StepRow
                  key={step.step_number}
                  step={step}
                  expanded={expandedSteps.has(step.step_number)}
                  toggle={() => toggleStep(step.step_number)}
                />
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {/* Downtime estimate */}
      <section style={{ marginBottom: '1.5rem' }}>
        <h3 style={{ fontSize: '0.95rem', marginBottom: '0.5rem' }}>Downtime Estimate</h3>
        <div style={CARD}>
          {doc.downtime_estimate_minutes > 0 ? (
            <>
              <strong>{doc.downtime_estimate_minutes} minutes</strong>
              <p style={{ margin: '0.25rem 0 0', fontSize: '0.85rem', color: '#57606a' }}>{doc.downtime_basis}</p>
            </>
          ) : (
            <p style={{ margin: 0, color: '#854d0e', fontSize: '0.85rem' }}>
              ⚠️ {doc.downtime_basis}
            </p>
          )}
        </div>
      </section>

      {/* Assumptions */}
      {doc.assumptions.length > 0 && (
        <section style={{ marginBottom: '1.5rem' }}>
          <h3 style={{ fontSize: '0.95rem', marginBottom: '0.5rem' }}>Assumptions</h3>
          <div style={CARD}>
            <ul style={{ margin: 0, paddingLeft: '1.2rem' }}>
              {doc.assumptions.map((a, i) => (
                <li key={i} style={{ fontSize: '0.85rem', marginBottom: '0.25rem' }}>{a}</li>
              ))}
            </ul>
          </div>
        </section>
      )}

      {/* AI Narrative */}
      <section style={{ marginBottom: '1.5rem' }}>
        <h3 style={{ fontSize: '0.95rem', marginBottom: '0.5rem' }}>
          Narrative
          {plan.ai_narrative && (
            <span style={{ marginLeft: '0.5rem', fontSize: '0.75rem', background: '#f3e8ff', color: '#7c3aed', padding: '0.1rem 0.4rem', borderRadius: '3px', border: '1px solid #d8b4fe' }}>
              AI-generated
            </span>
          )}
        </h3>
        {plan.ai_narrative ? (
          <div style={{ ...CARD, fontSize: '0.85rem', lineHeight: 1.6 }}>
            {plan.ai_narrative}
            {plan.ai_narrative_generated_at && (
              <p style={{ margin: '0.5rem 0 0', fontSize: '0.75rem', color: '#57606a' }}>
                Generated {new Date(plan.ai_narrative_generated_at).toLocaleString()} by {plan.ai_narrative_model}
              </p>
            )}
          </div>
        ) : (
          <div style={CARD}>
            <p style={{ margin: '0 0 0.5rem', fontSize: '0.85rem', color: '#57606a' }}>
              No narrative generated yet.
            </p>
            {!isApproved && (
              <button
                style={BTN_PRIMARY}
                disabled={generatingNarrative}
                onClick={handleGenerateNarrative}
              >
                {generatingNarrative ? 'Generating…' : 'Generate AI Narrative'}
              </button>
            )}
          </div>
        )}
      </section>

      {/* Approval section */}
      <section style={{ marginBottom: '1.5rem' }}>
        <h3 style={{ fontSize: '0.95rem', marginBottom: '0.5rem' }}>Approval</h3>
        {isApproved ? (
          <div style={{ ...CARD, background: '#dcfce7', border: '1px solid #86efac' }}>
            <strong style={{ color: '#166534' }}>✓ Approved</strong>
            <p style={{ margin: '0.25rem 0 0', fontSize: '0.85rem' }}>
              Approved at {plan.approved_at ? new Date(plan.approved_at).toLocaleString() : '—'}
              {plan.approval_expires_at && (
                <span style={{ marginLeft: '0.5rem', color: '#57606a' }}>
                  (expires {new Date(plan.approval_expires_at).toLocaleDateString()})
                </span>
              )}
            </p>
          </div>
        ) : (
          <div style={CARD}>
            <p style={{ margin: '0 0 0.75rem', fontSize: '0.85rem', color: '#57606a' }}>
              This plan requires approval before execution. The approver must be a different user from the creator.
            </p>
            {approveError && (
              <div style={{ background: '#fee2e2', border: '1px solid #fca5a5', borderRadius: '4px', padding: '0.5rem', marginBottom: '0.75rem', color: '#991b1b', fontSize: '0.85rem' }}>
                {approveError}
              </div>
            )}
            <button
              style={BTN_PRIMARY}
              disabled={approving}
              onClick={handleApprove}
            >
              {approving ? 'Approving…' : 'Approve Plan'}
            </button>
          </div>
        )}
      </section>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main Plans page — router between list, create, detail
// ---------------------------------------------------------------------------

type PlansView = { type: 'list' } | { type: 'create' } | { type: 'detail'; id: string }

const Plans: React.FC = () => {
  const [view, setView] = useState<PlansView>({ type: 'list' })

  return (
    <div>
      <h1 style={{ fontSize: '1.25rem', marginTop: 0, marginBottom: '1.5rem' }}>Plans</h1>

      {view.type === 'list' && (
        <PlanList
          onSelect={(id) => setView({ type: 'detail', id })}
          onCreateNew={() => setView({ type: 'create' })}
        />
      )}

      {view.type === 'create' && (
        <CreatePlanWizard
          onCreated={(id) => setView({ type: 'detail', id })}
          onCancel={() => setView({ type: 'list' })}
        />
      )}

      {view.type === 'detail' && (
        <PlanDetailView
          planId={view.id}
          onBack={() => setView({ type: 'list' })}
        />
      )}
    </div>
  )
}

export default Plans
