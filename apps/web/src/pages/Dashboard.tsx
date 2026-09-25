import React from 'react'

interface StatusCard {
  label: string
  value: string
  status: 'ok' | 'warn' | 'unknown'
}

const CARDS: StatusCard[] = [
  { label: 'Phase', value: 'Phase 0 — Foundation', status: 'ok' },
  { label: 'API', value: 'Connecting…', status: 'unknown' },
  { label: 'Providers', value: '0 connected', status: 'unknown' },
  { label: 'Resources', value: '0 discovered', status: 'unknown' },
]

const STATUS_COLORS: Record<string, string> = {
  ok: '#22c55e',
  warn: '#f59e0b',
  unknown: '#57606a',
}

const Dashboard: React.FC = () => (
  <div>
    <h1 style={{ fontSize: '1.5rem', fontWeight: 700, marginBottom: '1.5rem' }}>
      AETHER MIGRATE
    </h1>
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: '1rem' }}>
      {CARDS.map((card) => (
        <div
          key={card.label}
          style={{
            border: '1px solid #e5e7eb',
            borderRadius: '8px',
            padding: '1.25rem',
            background: '#f7f8fa',
          }}
        >
          <div style={{ fontSize: '0.75rem', color: '#57606a', marginBottom: '0.5rem', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
            {card.label}
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
            <span
              style={{
                width: '8px',
                height: '8px',
                borderRadius: '50%',
                background: STATUS_COLORS[card.status],
                flexShrink: 0,
              }}
            />
            <span style={{ fontSize: '0.95rem', fontWeight: 600 }}>{card.value}</span>
          </div>
        </div>
      ))}
    </div>
    <p style={{ marginTop: '2rem', color: '#57606a', fontSize: '0.875rem' }}>
      Full UI implementation in Phase 1. See <a href="#/settings" style={{ color: '#3b82d4' }}>Settings</a> to connect your first cloud provider.
    </p>
  </div>
)

export default Dashboard
