import React from 'react'

const Login: React.FC = () => {
  const handleSignIn = () => {
    const issuer = import.meta.env.VITE_OIDC_ISSUER || 'http://localhost:8080/realms/aether'
    const clientId = import.meta.env.VITE_OIDC_CLIENT_ID || 'aether-web'
    const redirectUri = encodeURIComponent(`${window.location.origin}/`)
    const state = Math.random().toString(36).slice(2)
    const codeVerifier = Math.random().toString(36).slice(2).repeat(2)

    sessionStorage.setItem('oidc_state', state)
    sessionStorage.setItem('oidc_code_verifier', codeVerifier)

    const url =
      `${issuer}/protocol/openid-connect/auth` +
      `?client_id=${clientId}` +
      `&redirect_uri=${redirectUri}` +
      `&response_type=code` +
      `&scope=openid+email+profile` +
      `&state=${state}` +
      `&code_challenge_method=S256`

    window.location.href = url
  }

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        minHeight: '60vh',
        gap: '1.5rem',
      }}
    >
      <h1 style={{ fontSize: '2rem', fontWeight: 700, color: '#1f2328' }}>AETHER MIGRATE</h1>
      <p style={{ color: '#57606a' }}>Self-hosted cloud-neutral migration control plane</p>
      <button
        onClick={handleSignIn}
        style={{
          padding: '0.75rem 2rem',
          background: '#3b82d4',
          color: '#fff',
          border: 'none',
          borderRadius: '6px',
          fontSize: '1rem',
          cursor: 'pointer',
          fontWeight: 600,
        }}
      >
        Sign in with Keycloak
      </button>
    </div>
  )
}

export default Login
