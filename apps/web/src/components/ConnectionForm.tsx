/**
 * ConnectionForm — form for creating a new AWS connection.
 * Supports role ARN + External ID (preferred) or access key fallback.
 */

import React, { useState } from 'react'

export interface ConnectionFormData {
  name: string
  provider: 'aws'
  mode: 'read-only' | 'execute'
  aws_role_arn: string
  aws_external_id: string
  aws_access_key_id: string
  aws_secret_access_key: string
  aws_default_region: string
}

interface ConnectionFormProps {
  onSubmit: (data: ConnectionFormData) => Promise<void>
  onCancel: () => void
  loading?: boolean
  error?: string | null
}

const INPUT_STYLE: React.CSSProperties = {
  width: '100%',
  padding: '0.4rem 0.5rem',
  border: '1px solid #e5e7eb',
  borderRadius: '4px',
  fontSize: '0.875rem',
  fontFamily: 'inherit',
  boxSizing: 'border-box',
}

const LABEL_STYLE: React.CSSProperties = {
  display: 'block',
  fontSize: '0.8rem',
  color: '#57606a',
  marginBottom: '0.25rem',
  fontWeight: 500,
}

const FIELD_STYLE: React.CSSProperties = {
  marginBottom: '1rem',
}

const AWS_REGIONS = [
  'us-east-1', 'us-east-2', 'us-west-1', 'us-west-2',
  'eu-west-1', 'eu-west-2', 'eu-central-1',
  'ap-northeast-1', 'ap-southeast-1', 'ap-southeast-2',
  'ap-south-1', 'sa-east-1', 'ca-central-1',
]

const BUTTON_STYLE: React.CSSProperties = {
  padding: '0.45rem 1rem',
  borderRadius: '4px',
  fontSize: '0.875rem',
  cursor: 'pointer',
  border: 'none',
  fontFamily: 'inherit',
}

export const ConnectionForm: React.FC<ConnectionFormProps> = ({
  onSubmit,
  onCancel,
  loading = false,
  error = null,
}) => {
  const [form, setForm] = useState<ConnectionFormData>({
    name: '',
    provider: 'aws',
    mode: 'read-only',
    aws_role_arn: '',
    aws_external_id: '',
    aws_access_key_id: '',
    aws_secret_access_key: '',
    aws_default_region: 'us-east-1',
  })

  const [authMethod, setAuthMethod] = useState<'role' | 'key'>('role')

  const set = (field: keyof ConnectionFormData) =>
    (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
      setForm((prev) => ({ ...prev, [field]: e.target.value }))

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    // Build payload: clear unused auth fields
    const payload: ConnectionFormData = {
      ...form,
      aws_role_arn: authMethod === 'role' ? form.aws_role_arn : '',
      aws_external_id: authMethod === 'role' ? form.aws_external_id : '',
      aws_access_key_id: authMethod === 'key' ? form.aws_access_key_id : '',
      aws_secret_access_key: authMethod === 'key' ? form.aws_secret_access_key : '',
    }
    onSubmit(payload)
  }

  return (
    <form onSubmit={handleSubmit}>
      {error && (
        <div style={{ color: '#ef4444', fontSize: '0.85rem', marginBottom: '1rem' }}>
          {error}
        </div>
      )}

      <div style={FIELD_STYLE}>
        <label style={LABEL_STYLE}>Name *</label>
        <input style={INPUT_STYLE} value={form.name} onChange={set('name')} required />
      </div>

      <div style={FIELD_STYLE}>
        <label style={LABEL_STYLE}>Provider</label>
        <select style={INPUT_STYLE} value={form.provider} disabled>
          <option value="aws">AWS</option>
        </select>
      </div>

      <div style={FIELD_STYLE}>
        <label style={LABEL_STYLE}>Default Region</label>
        <select style={INPUT_STYLE} value={form.aws_default_region} onChange={set('aws_default_region')}>
          {AWS_REGIONS.map((r) => (
            <option key={r} value={r}>
              {r}
            </option>
          ))}
        </select>
      </div>

      <div style={{ ...FIELD_STYLE, display: 'flex', gap: '1rem', marginBottom: '0.75rem' }}>
        <label style={{ fontSize: '0.85rem', cursor: 'pointer' }}>
          <input
            type="radio"
            name="authMethod"
            value="role"
            checked={authMethod === 'role'}
            onChange={() => setAuthMethod('role')}
          />{' '}
          IAM Role (recommended)
        </label>
        <label style={{ fontSize: '0.85rem', cursor: 'pointer' }}>
          <input
            type="radio"
            name="authMethod"
            value="key"
            checked={authMethod === 'key'}
            onChange={() => setAuthMethod('key')}
          />{' '}
          Access Key
        </label>
      </div>

      {authMethod === 'role' ? (
        <>
          <div style={FIELD_STYLE}>
            <label style={LABEL_STYLE}>Role ARN *</label>
            <input
              style={INPUT_STYLE}
              placeholder="arn:aws:iam::123456789012:role/AetherMigrateDiscovery"
              value={form.aws_role_arn}
              onChange={set('aws_role_arn')}
              required={authMethod === 'role'}
            />
          </div>
          <div style={FIELD_STYLE}>
            <label style={LABEL_STYLE}>External ID</label>
            <input
              style={INPUT_STYLE}
              placeholder="aether-migrate-&lt;workspace-id&gt;"
              value={form.aws_external_id}
              onChange={set('aws_external_id')}
            />
          </div>
        </>
      ) : (
        <>
          <div style={FIELD_STYLE}>
            <label style={LABEL_STYLE}>Access Key ID *</label>
            <input
              style={INPUT_STYLE}
              placeholder="AKIA..."
              value={form.aws_access_key_id}
              onChange={set('aws_access_key_id')}
              required={authMethod === 'key'}
              autoComplete="off"
            />
          </div>
          <div style={FIELD_STYLE}>
            <label style={LABEL_STYLE}>Secret Access Key *</label>
            <input
              style={INPUT_STYLE}
              type="password"
              placeholder="••••••••"
              value={form.aws_secret_access_key}
              onChange={set('aws_secret_access_key')}
              required={authMethod === 'key'}
              autoComplete="new-password"
            />
          </div>
        </>
      )}

      <div style={{ display: 'flex', gap: '0.5rem', justifyContent: 'flex-end', marginTop: '1rem' }}>
        <button
          type="button"
          style={{ ...BUTTON_STYLE, background: '#f7f8fa', border: '1px solid #e5e7eb', color: '#1f2328' }}
          onClick={onCancel}
          disabled={loading}
        >
          Cancel
        </button>
        <button
          type="submit"
          style={{ ...BUTTON_STYLE, background: '#3b82d4', color: '#fff' }}
          disabled={loading}
        >
          {loading ? 'Creating…' : 'Create Connection'}
        </button>
      </div>
    </form>
  )
}

export default ConnectionForm
