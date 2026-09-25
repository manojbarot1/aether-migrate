/**
 * Compare page — Phase 5
 *
 * Allows analysts to select a VM from inventory, configure target cloud/region,
 * choose pricing scenarios, and view the cost comparison result.
 *
 * IMPORTANT: All cost numbers come from the backend CostEngine — never from the LLM.
 * The "estimate — not a quote" label is always visible.
 */

import React from 'react'
import {
  CatalogProviderStatus,
  CostBreakdown,
  CostCompareRequest,
  CostCompareResponse,
  CostTarget,
  costApi,
} from '../api/cost'
import apiClient from '../api/client'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface VMSummary {
  id: string
  name: string | null
  provider: string
  region: string
  spec: Record<string, unknown>
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const PROVIDERS = [
  { value: 'azure', label: 'Microsoft Azure' },
  { value: 'aws', label: 'Amazon Web Services' },
  { value: 'gcp', label: 'Google Cloud' },
]

const AZURE_REGIONS = [
  { value: 'eastus', label: 'East US' },
  { value: 'westeurope', label: 'West Europe' },
  { value: 'northeurope', label: 'North Europe' },
  { value: 'uksouth', label: 'UK South' },
  { value: 'westus2', label: 'West US 2' },
  { value: 'southeastasia', label: 'Southeast Asia' },
]

const AWS_REGIONS = [
  { value: 'us-east-1', label: 'US East (N. Virginia)' },
  { value: 'us-west-2', label: 'US West (Oregon)' },
  { value: 'eu-west-1', label: 'Europe (Ireland)' },
  { value: 'eu-central-1', label: 'Europe (Frankfurt)' },
]

const GCP_REGIONS = [
  { value: 'us-central1', label: 'Iowa' },
  { value: 'europe-west1', label: 'Belgium' },
  { value: 'europe-west4', label: 'Netherlands' },
  { value: 'asia-east1', label: 'Taiwan' },
]

const REGION_OPTIONS: Record<string, { value: string; label: string }[]> = {
  azure: AZURE_REGIONS,
  aws: AWS_REGIONS,
  gcp: GCP_REGIONS,
}

const SCENARIOS = [
  { value: 'on_demand', label: 'On-Demand' },
  { value: 'reserved_1yr_std', label: '1yr Reserved (Standard)' },
  { value: 'reserved_3yr_std', label: '3yr Reserved (Standard)' },
  { value: 'reserved_1yr_conv', label: '1yr Reserved (Convertible)' },
]

const CURRENCIES = ['USD', 'EUR', 'GBP', 'JPY', 'PLN']

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function fmt(value: string | null | undefined): string {
  if (!value) return '—'
  const n = parseFloat(value)
  if (isNaN(n)) return value
  return n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function savingsPct(sourceCost: string | null, targetCost: string): string | null {
  if (!sourceCost) return null
  const src = parseFloat(sourceCost)
  const tgt = parseFloat(targetCost)
  if (src === 0 || isNaN(src) || isNaN(tgt)) return null
  const pct = ((src - tgt) / src) * 100
  return pct.toFixed(1)
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

const Badge: React.FC<{ text: string; color?: string }> = ({ text, color = '#57606a' }) => (
  <span
    style={{
      display: 'inline-block',
      padding: '2px 8px',
      borderRadius: '4px',
      background: '#f7f8fa',
      border: '1px solid #e5e7eb',
      color,
      fontSize: '0.75rem',
      fontWeight: 500,
    }}
  >
    {text}
  </span>
)

const EstimateLabel: React.FC = () => (
  <div
    style={{
      background: '#fff8e1',
      border: '1px solid #f0c000',
      borderRadius: '4px',
      padding: '6px 12px',
      fontSize: '0.8rem',
      color: '#856404',
      fontWeight: 500,
    }}
  >
    ⚠️ Estimates only — not a quote. Prices are from cloud provider catalogs and subject to change.
  </div>
)

const CatalogFreshnessBadge: React.FC<{ status: CatalogProviderStatus }> = ({ status }) => {
  if (!status.version) {
    return <Badge text={`${status.provider.toUpperCase()}: no catalog`} color="#dc2626" />
  }
  const color = status.is_stale ? '#dc2626' : '#16a34a'
  const age = status.age_hours !== null ? `${status.age_hours.toFixed(0)}h ago` : ''
  return (
    <Badge
      text={`${status.provider.toUpperCase()} catalog v${status.version}${age ? `, synced ${age}` : ''}`}
      color={color}
    />
  )
}

const AssumptionsPanel: React.FC<{ assumptions: string[] }> = ({ assumptions }) => {
  const [open, setOpen] = React.useState(false)
  return (
    <div style={{ marginTop: '8px', border: '1px solid #e5e7eb', borderRadius: '4px' }}>
      <button
        onClick={() => setOpen(v => !v)}
        style={{
          width: '100%',
          textAlign: 'left',
          padding: '8px 12px',
          background: '#f7f8fa',
          border: 'none',
          cursor: 'pointer',
          fontSize: '0.8rem',
          color: '#57606a',
          fontWeight: 500,
          borderRadius: '4px',
        }}
      >
        {open ? '▾' : '▸'} {assumptions.length} assumptions
      </button>
      {open && (
        <ul style={{ margin: 0, padding: '8px 12px 8px 28px', fontSize: '0.78rem', color: '#57606a' }}>
          {assumptions.map((a, i) => (
            <li key={i} style={{ marginBottom: '2px' }}>{a}</li>
          ))}
        </ul>
      )}
    </div>
  )
}

const BreakdownTable: React.FC<{ bd: CostBreakdown }> = ({ bd }) => {
  const rows = [
    { label: 'Compute', value: bd.compute_monthly },
    { label: 'Storage', value: bd.storage_monthly },
    { label: 'Network (egress)', value: bd.network_monthly },
    { label: 'Migration egress (one-time)', value: bd.migration_egress },
    { label: `Dual-run (14 days)`, value: bd.dual_run },
  ]
  return (
    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.82rem' }}>
      <tbody>
        {rows.map(r => (
          <tr key={r.label}>
            <td style={{ padding: '3px 8px', color: '#57606a' }}>{r.label}</td>
            <td style={{ padding: '3px 8px', textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>
              {bd.currency} {fmt(r.value)}
            </td>
          </tr>
        ))}
        <tr style={{ borderTop: '1px solid #e5e7eb', fontWeight: 600 }}>
          <td style={{ padding: '5px 8px' }}>Monthly total</td>
          <td style={{ padding: '5px 8px', textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>
            {bd.currency} {fmt(bd.total_monthly)}
          </td>
        </tr>
        <tr style={{ fontWeight: 600, color: '#3b82d4' }}>
          <td style={{ padding: '3px 8px' }}>First-year total</td>
          <td style={{ padding: '3px 8px', textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>
            {bd.currency} {fmt(bd.total_first_year)}
          </td>
        </tr>
      </tbody>
    </table>
  )
}

const TargetAccordion: React.FC<{
  target: CostTarget
  sourceMonthlyCost: string | null
}> = ({ target, sourceMonthlyCost }) => {
  const [open, setOpen] = React.useState(false)
  const bd = target.breakdown
  const pct = savingsPct(sourceMonthlyCost, bd.total_monthly)

  return (
    <div style={{ border: '1px solid #e5e7eb', borderRadius: '6px', marginBottom: '8px' }}>
      <button
        onClick={() => setOpen(v => !v)}
        style={{
          width: '100%',
          textAlign: 'left',
          padding: '10px 14px',
          background: '#fff',
          border: 'none',
          cursor: 'pointer',
          borderRadius: '6px',
          display: 'flex',
          alignItems: 'center',
          gap: '12px',
        }}
      >
        <span style={{ fontWeight: 600, fontSize: '0.9rem', minWidth: '180px' }}>{target.sku}</span>
        <Badge text={`${target.provider} · ${target.region}`} />
        <Badge text={target.scenario.replace(/_/g, ' ')} />
        <span style={{ marginLeft: 'auto', fontVariantNumeric: 'tabular-nums', fontWeight: 600 }}>
          {bd.currency} {fmt(bd.total_monthly)}/mo
        </span>
        {pct !== null && (
          <span style={{ color: parseFloat(pct) > 0 ? '#16a34a' : '#dc2626', fontWeight: 600, fontSize: '0.85rem' }}>
            {parseFloat(pct) > 0 ? `↓${pct}%` : `↑${Math.abs(parseFloat(pct)).toFixed(1)}%`}
          </span>
        )}
        <span style={{ color: '#57606a', marginLeft: '4px' }}>{open ? '▲' : '▼'}</span>
      </button>

      {open && (
        <div style={{ padding: '0 14px 14px' }}>
          {target.licence_review_required && (
            <div style={{ background: '#fff3cd', border: '1px solid #f0c000', borderRadius: '4px', padding: '8px 12px', marginBottom: '10px', fontSize: '0.82rem', color: '#856404' }}>
              ⚠️ Windows / SQL Server licence portability requires review. Cost shown for instance only.
            </div>
          )}
          {target.warnings.filter(w => !w.includes('licence')).map((w, i) => (
            <div key={i} style={{ background: '#fee2e2', border: '1px solid #fca5a5', borderRadius: '4px', padding: '6px 10px', marginBottom: '6px', fontSize: '0.8rem', color: '#991b1b' }}>
              {w}
            </div>
          ))}
          <BreakdownTable bd={bd} />
          <AssumptionsPanel assumptions={bd.assumptions} />
          <div style={{ marginTop: '6px', fontSize: '0.75rem', color: '#57606a' }}>
            Catalog: v{bd.catalog_version}
            {bd.fx_rate && ` · FX 1 USD = ${bd.fx_rate} ${bd.currency}`}
          </div>
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main Compare page
// ---------------------------------------------------------------------------

const ComparePage: React.FC = () => {
  // VM search
  const [search, setSearch] = React.useState('')
  const [vms, setVms] = React.useState<VMSummary[]>([])
  const [selectedVm, setSelectedVm] = React.useState<VMSummary | null>(null)
  const [searching, setSearching] = React.useState(false)

  // Target config
  const [selectedProviders, setSelectedProviders] = React.useState<string[]>(['azure'])
  const [regionsByProvider, setRegionsByProvider] = React.useState<Record<string, string>>({
    azure: 'eastus',
    aws: 'us-east-1',
    gcp: 'us-central1',
  })
  const [selectedScenarios, setSelectedScenarios] = React.useState<string[]>(['on_demand'])
  const [currency, setCurrency] = React.useState('USD')

  // Results
  const [result, setResult] = React.useState<CostCompareResponse | null>(null)
  const [catalogStatus, setCatalogStatus] = React.useState<CatalogProviderStatus[]>([])
  const [loading, setLoading] = React.useState(false)
  const [error, setError] = React.useState<string | null>(null)

  // Load catalog status on mount
  React.useEffect(() => {
    costApi.catalogStatus()
      .then(res => setCatalogStatus(res.providers))
      .catch(() => {/* catalog status is non-critical */})
  }, [])

  // VM search with debounce
  React.useEffect(() => {
    if (search.length < 2) { setVms([]); return }
    const t = setTimeout(async () => {
      setSearching(true)
      try {
        const res = await apiClient.get<{ items: VMSummary[] }>(`/api/v1/inventory/vms?limit=20${search ? `&name=${encodeURIComponent(search)}` : ''}`)
        setVms(res.items || [])
      } catch {
        setVms([])
      } finally {
        setSearching(false)
      }
    }, 300)
    return () => clearTimeout(t)
  }, [search])

  const toggleProvider = (p: string) => {
    setSelectedProviders(prev =>
      prev.includes(p) ? prev.filter(x => x !== p) : [...prev, p]
    )
  }

  const toggleScenario = (s: string) => {
    setSelectedScenarios(prev =>
      prev.includes(s) ? prev.filter(x => x !== s) : [...prev, s]
    )
  }

  const handleCompare = async () => {
    if (!selectedVm || selectedProviders.length === 0 || selectedScenarios.length === 0) return
    setLoading(true)
    setError(null)
    setResult(null)
    try {
      const req: CostCompareRequest = {
        resource_id: selectedVm.id,
        target_providers: selectedProviders,
        target_regions: Object.fromEntries(
          selectedProviders.map(p => [p, regionsByProvider[p] || ''])
        ),
        scenarios: selectedScenarios,
        currency,
      }
      const res = await costApi.compare(req)
      setResult(res)
    } catch (e: unknown) {
      const msg = (e as { error?: string })?.error ?? 'Comparison failed'
      setError(msg)
    } finally {
      setLoading(false)
    }
  }

  const sourceCost = result?.source_list_monthly_usd ?? result?.source_actual_monthly_usd ?? null

  // Group targets by provider for summary table
  const providerSummary = result
    ? selectedProviders.map(p => {
        const targets = result.targets.filter(t => t.provider === p && t.scenario === 'on_demand')
        const best = targets.sort((a, b) => parseFloat(a.breakdown.total_monthly) - parseFloat(b.breakdown.total_monthly))[0]
        return { provider: p, best }
      })
    : []

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  const sectionStyle: React.CSSProperties = {
    background: '#fff',
    border: '1px solid #e5e7eb',
    borderRadius: '8px',
    padding: '1.25rem',
    marginBottom: '1rem',
  }

  return (
    <div style={{ maxWidth: '900px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '1.25rem' }}>
        <div>
          <h1 style={{ fontSize: '1.3rem', fontWeight: 700, margin: 0 }}>Cost Comparison</h1>
          <p style={{ color: '#57606a', fontSize: '0.85rem', marginTop: '4px' }}>
            Compare migration costs across cloud providers using catalog pricing.
          </p>
        </div>
        <EstimateLabel />
      </div>

      {/* Catalog freshness */}
      {catalogStatus.length > 0 && (
        <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', marginBottom: '1rem' }}>
          {catalogStatus.map(s => <CatalogFreshnessBadge key={s.provider} status={s} />)}
        </div>
      )}

      {/* Step 1: VM selector */}
      <div style={sectionStyle}>
        <h2 style={{ fontSize: '0.95rem', fontWeight: 600, margin: '0 0 0.75rem' }}>
          1. Select source VM
        </h2>
        <input
          type="text"
          value={search}
          onChange={e => setSearch(e.target.value)}
          placeholder="Search by VM name or ID…"
          style={{
            width: '100%',
            padding: '8px 12px',
            border: '1px solid #e5e7eb',
            borderRadius: '4px',
            fontSize: '0.875rem',
            boxSizing: 'border-box',
          }}
        />
        {searching && <div style={{ fontSize: '0.8rem', color: '#57606a', marginTop: '6px' }}>Searching…</div>}
        {vms.length > 0 && !selectedVm && (
          <ul style={{ listStyle: 'none', margin: '4px 0 0', padding: 0, border: '1px solid #e5e7eb', borderRadius: '4px', maxHeight: '200px', overflow: 'auto' }}>
            {vms.map(vm => (
              <li
                key={vm.id}
                onClick={() => { setSelectedVm(vm); setSearch(vm.name || vm.id); setVms([]) }}
                style={{ padding: '8px 12px', cursor: 'pointer', fontSize: '0.875rem', borderBottom: '1px solid #f0f0f0' }}
              >
                <strong>{vm.name || vm.id}</strong>
                <span style={{ color: '#57606a', marginLeft: '8px' }}>{vm.provider} · {vm.region}</span>
                <span style={{ color: '#57606a', marginLeft: '8px', fontSize: '0.78rem' }}>
                  {(vm.spec as { vcpu?: number })?.vcpu ?? '?'} vCPU /
                  {(vm.spec as { memory_gib?: number })?.memory_gib ?? '?'} GiB
                </span>
              </li>
            ))}
          </ul>
        )}
        {selectedVm && (
          <div style={{ marginTop: '10px', display: 'flex', alignItems: 'center', gap: '10px' }}>
            <Badge text={`${selectedVm.provider} · ${selectedVm.region}`} />
            <span style={{ fontSize: '0.82rem', color: '#57606a' }}>
              {(selectedVm.spec as { vcpu?: number })?.vcpu ?? '?'} vCPU /
              {(selectedVm.spec as { memory_gib?: number })?.memory_gib ?? '?'} GiB
            </span>
            <button
              onClick={() => { setSelectedVm(null); setSearch(''); setResult(null) }}
              style={{ marginLeft: 'auto', fontSize: '0.78rem', color: '#57606a', background: 'none', border: 'none', cursor: 'pointer' }}
            >
              Change
            </button>
          </div>
        )}
      </div>

      {/* Step 2: Target config */}
      <div style={sectionStyle}>
        <h2 style={{ fontSize: '0.95rem', fontWeight: 600, margin: '0 0 0.75rem' }}>
          2. Configure target
        </h2>
        <div style={{ display: 'flex', gap: '2rem', flexWrap: 'wrap' }}>
          {/* Provider selection */}
          <div>
            <div style={{ fontSize: '0.82rem', fontWeight: 500, color: '#57606a', marginBottom: '6px' }}>
              Target cloud providers
            </div>
            {PROVIDERS.map(p => (
              <label key={p.value} style={{ display: 'block', fontSize: '0.875rem', marginBottom: '4px', cursor: 'pointer' }}>
                <input
                  type="checkbox"
                  checked={selectedProviders.includes(p.value)}
                  onChange={() => toggleProvider(p.value)}
                  style={{ marginRight: '6px' }}
                />
                {p.label}
              </label>
            ))}
          </div>

          {/* Region per selected provider */}
          <div>
            <div style={{ fontSize: '0.82rem', fontWeight: 500, color: '#57606a', marginBottom: '6px' }}>
              Region per provider
            </div>
            {selectedProviders.map(p => (
              <div key={p} style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '6px' }}>
                <span style={{ fontSize: '0.82rem', width: '48px', color: '#57606a' }}>{p.toUpperCase()}</span>
                <select
                  value={regionsByProvider[p] || ''}
                  onChange={e => setRegionsByProvider(prev => ({ ...prev, [p]: e.target.value }))}
                  style={{ padding: '4px 8px', border: '1px solid #e5e7eb', borderRadius: '4px', fontSize: '0.82rem' }}
                >
                  {(REGION_OPTIONS[p] || []).map(r => (
                    <option key={r.value} value={r.value}>{r.label}</option>
                  ))}
                </select>
              </div>
            ))}
          </div>

          {/* Scenarios */}
          <div>
            <div style={{ fontSize: '0.82rem', fontWeight: 500, color: '#57606a', marginBottom: '6px' }}>
              Pricing scenarios
            </div>
            {SCENARIOS.map(s => (
              <label key={s.value} style={{ display: 'block', fontSize: '0.875rem', marginBottom: '4px', cursor: 'pointer' }}>
                <input
                  type="checkbox"
                  checked={selectedScenarios.includes(s.value)}
                  onChange={() => toggleScenario(s.value)}
                  style={{ marginRight: '6px' }}
                />
                {s.label}
              </label>
            ))}
          </div>

          {/* Currency */}
          <div>
            <div style={{ fontSize: '0.82rem', fontWeight: 500, color: '#57606a', marginBottom: '6px' }}>
              Currency
            </div>
            <select
              value={currency}
              onChange={e => setCurrency(e.target.value)}
              style={{ padding: '6px 10px', border: '1px solid #e5e7eb', borderRadius: '4px', fontSize: '0.875rem' }}
            >
              {CURRENCIES.map(c => <option key={c} value={c}>{c}</option>)}
            </select>
          </div>
        </div>
      </div>

      {/* Compare button */}
      <button
        onClick={handleCompare}
        disabled={!selectedVm || selectedProviders.length === 0 || selectedScenarios.length === 0 || loading}
        style={{
          padding: '10px 28px',
          background: loading ? '#93c5fd' : '#3b82d4',
          color: '#fff',
          border: 'none',
          borderRadius: '6px',
          fontSize: '0.95rem',
          fontWeight: 600,
          cursor: loading ? 'not-allowed' : 'pointer',
          marginBottom: '1.5rem',
        }}
      >
        {loading ? 'Comparing…' : 'Compare'}
      </button>

      {/* Error */}
      {error && (
        <div style={{ background: '#fee2e2', border: '1px solid #fca5a5', borderRadius: '6px', padding: '10px 14px', marginBottom: '1rem', color: '#991b1b', fontSize: '0.875rem' }}>
          {error}
        </div>
      )}

      {/* Results */}
      {result && (
        <>
          {/* Summary table */}
          <div style={sectionStyle}>
            <h2 style={{ fontSize: '0.95rem', fontWeight: 600, margin: '0 0 0.75rem' }}>
              Summary (on-demand, lowest-cost candidate)
            </h2>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.875rem' }}>
              <thead>
                <tr style={{ background: '#f7f8fa' }}>
                  <th style={{ padding: '8px 12px', textAlign: 'left', fontWeight: 600 }}>Provider</th>
                  <th style={{ padding: '8px 12px', textAlign: 'left', fontWeight: 600 }}>Best SKU</th>
                  <th style={{ padding: '8px 12px', textAlign: 'right', fontWeight: 600 }}>Monthly ({currency})</th>
                  <th style={{ padding: '8px 12px', textAlign: 'right', fontWeight: 600 }}>vs. Source</th>
                </tr>
              </thead>
              <tbody>
                {sourceCost && (
                  <tr style={{ background: '#f7f8fa', fontWeight: 500 }}>
                    <td style={{ padding: '8px 12px' }}>
                      {selectedVm?.provider.toUpperCase() ?? '—'} (source)
                    </td>
                    <td style={{ padding: '8px 12px', color: '#57606a' }}>
                      {selectedVm?.spec && (selectedVm.spec as { instance_type?: string }).instance_type || '—'}
                    </td>
                    <td style={{ padding: '8px 12px', textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>
                      {fmt(sourceCost)}
                    </td>
                    <td style={{ padding: '8px 12px', textAlign: 'right' }}>—</td>
                  </tr>
                )}
                {providerSummary.map(({ provider, best }) => {
                  const pct = best ? savingsPct(sourceCost, best.breakdown.total_monthly) : null
                  return (
                    <tr key={provider}>
                      <td style={{ padding: '8px 12px', fontWeight: 500 }}>{provider.toUpperCase()}</td>
                      <td style={{ padding: '8px 12px', color: '#57606a' }}>{best?.sku ?? 'No data'}</td>
                      <td style={{ padding: '8px 12px', textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>
                        {best ? fmt(best.breakdown.total_monthly) : '—'}
                      </td>
                      <td style={{ padding: '8px 12px', textAlign: 'right', fontWeight: 600 }}>
                        {pct !== null && (
                          <span style={{ color: parseFloat(pct) > 0 ? '#16a34a' : '#dc2626' }}>
                            {parseFloat(pct) > 0 ? `↓${pct}%` : `↑${Math.abs(parseFloat(pct)).toFixed(1)}%`}
                          </span>
                        )}
                        {pct === null && '—'}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>

          {/* Detailed accordion */}
          <div style={sectionStyle}>
            <h2 style={{ fontSize: '0.95rem', fontWeight: 600, margin: '0 0 0.75rem' }}>
              Detailed breakdown
            </h2>
            {result.targets.length === 0 && (
              <div style={{ color: '#57606a', fontSize: '0.875rem' }}>
                No results. Ensure the catalog has been synced for the selected providers and regions.
              </div>
            )}
            {result.targets.map((t, i) => (
              <TargetAccordion key={i} target={t} sourceMonthlyCost={sourceCost} />
            ))}
          </div>

          {/* Footer: catalog version + estimate disclaimer */}
          <div style={{ color: '#57606a', fontSize: '0.78rem', marginBottom: '2rem' }}>
            Catalog version: {result.catalog_version || '—'} · Snapshot: {result.snapshot_id || '—'}
            <br />
            Prices sourced from cloud provider public catalogs. All figures are estimates —
            actual costs depend on usage, discounts, and contract terms.
          </div>
        </>
      )}
    </div>
  )
}

export default ComparePage
