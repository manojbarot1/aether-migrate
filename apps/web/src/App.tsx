import React, { Suspense } from 'react'
import Assistant from './components/Assistant'

// ---------------------------------------------------------------------------
// Page imports
// ---------------------------------------------------------------------------

const Login = React.lazy(() => import('./pages/Login'))
const Dashboard = React.lazy(() => import('./pages/Dashboard'))
const Connections = React.lazy(() => import('./pages/Connections'))
const Inventory = React.lazy(() => import('./pages/Inventory'))
const ResourceDetail = React.lazy(() => import('./pages/ResourceDetail'))
const TopologyPage = React.lazy(() => import('./pages/Topology'))
const ComparePage = React.lazy(() => import('./pages/Compare'))
const AssessmentPage = React.lazy(() => import('./pages/Assessment'))
const PlansPage = React.lazy(() => import('./pages/Plans'))

const PlaceholderPage: React.FC<{ title: string }> = ({ title }) => (
  <div style={{ padding: '2rem' }}>
    <h2 style={{ color: '#57606a', fontSize: '1.25rem' }}>{title}</h2>
    <p style={{ color: '#57606a', marginTop: '0.5rem' }}>
      This page will be implemented in a future phase.
    </p>
  </div>
)

// ---------------------------------------------------------------------------
// Simple hash-based router
// ---------------------------------------------------------------------------

function useHash(): string {
  const [hash, setHash] = React.useState(window.location.hash || '#/')
  React.useEffect(() => {
    const handler = () => setHash(window.location.hash || '#/')
    window.addEventListener('hashchange', handler)
    return () => window.removeEventListener('hashchange', handler)
  }, [])
  return hash
}

// Resource detail is a dynamic route — match #/resources/<id>
function matchRoute(hash: string): React.ReactNode {
  if (hash.startsWith('#/resources/')) return <ResourceDetail />

  const ROUTES: Record<string, React.ReactNode> = {
    '#/': <Dashboard />,
    '#/login': <Login />,
    '#/connections': <Connections />,
    '#/inventory': <Inventory />,
    '#/topology': <TopologyPage />,
    '#/compare': <ComparePage />,
    '#/assessment': <AssessmentPage />,
    '#/plans': <PlansPage />,
    '#/jobs': <PlaceholderPage title="Jobs" />,
    '#/audit': <PlaceholderPage title="Audit Log" />,
    '#/settings': <PlaceholderPage title="Settings" />,
  }

  return ROUTES[hash] ?? <PlaceholderPage title="Not Found" />
}

const NAV_ITEMS = [
  { route: '#/', label: 'Dashboard' },
  { route: '#/connections', label: 'Connections' },
  { route: '#/inventory', label: 'Inventory' },
  { route: '#/topology', label: 'Topology' },
  { route: '#/compare', label: 'Compare' },
  { route: '#/assessment', label: 'Assessment' },
  { route: '#/plans', label: 'Plans' },
  { route: '#/jobs', label: 'Jobs' },
  { route: '#/assistant', label: 'AI Assistant' },
  { route: '#/audit', label: 'Audit Log' },
  { route: '#/settings', label: 'Settings' },
]

const Nav: React.FC = () => {
  const hash = useHash()
  return (
    <nav
      style={{
        display: 'flex',
        flexDirection: 'column',
        width: '200px',
        minHeight: '100vh',
        borderRight: '1px solid #e5e7eb',
        padding: '1rem 0',
        background: '#f7f8fa',
        flexShrink: 0,
      }}
    >
      <div style={{ padding: '0 1rem 1.5rem', fontWeight: 700, fontSize: '0.9rem', color: '#3b82d4' }}>
        AETHER MIGRATE
      </div>
      {NAV_ITEMS.map(({ route, label }) => {
        const isActive = hash === route || (route !== '#/' && hash.startsWith(route))
        return (
          <a
            key={route}
            href={route}
            style={{
              padding: '0.4rem 1rem',
              color: isActive ? '#3b82d4' : '#1f2328',
              textDecoration: 'none',
              fontSize: '0.875rem',
              fontWeight: isActive ? 600 : 400,
              background: isActive ? '#eff6ff' : 'transparent',
              borderLeft: isActive ? '3px solid #3b82d4' : '3px solid transparent',
            }}
          >
            {label}
          </a>
        )
      })}
    </nav>
  )
}

export default function App() {
  const hash = useHash()
  const page = matchRoute(hash)
  const [assistantOpen, setAssistantOpen] = React.useState(false)

  return (
    <div style={{ display: 'flex' }}>
      <Nav />
      <main style={{ flex: 1, padding: '2rem', minWidth: 0, paddingRight: assistantOpen ? '440px' : '2rem', transition: 'padding-right 0.25s ease' }}>
        <Suspense fallback={<span style={{ color: '#57606a' }}>Loading…</span>}>
          {page}
        </Suspense>
      </main>

      {/* Floating AI Assistant toggle */}
      <button
        onClick={() => setAssistantOpen((v) => !v)}
        title="AI Assistant"
        style={{
          position: 'fixed',
          bottom: '24px',
          right: assistantOpen ? '436px' : '24px',
          zIndex: 300,
          background: '#3b82d4',
          color: '#fff',
          border: 'none',
          borderRadius: '50%',
          width: '48px',
          height: '48px',
          fontSize: '1.3rem',
          cursor: 'pointer',
          transition: 'right 0.25s ease',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
        }}
      >
        {assistantOpen ? '✕' : '✦'}
      </button>

      <Assistant open={assistantOpen} onClose={() => setAssistantOpen(false)} />
    </div>
  )
}
