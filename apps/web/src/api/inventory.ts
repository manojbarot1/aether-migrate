/**
 * Inventory API client — query VM and resource data.
 */

import { apiClient } from './client'

const BASE = '/api/v1/inventory'

export interface ResourceSummary {
  id: string
  workspace_id: string
  connection_id: string
  provider: string
  native_id: string
  account: string
  region: string
  zone: string | null
  kind: string
  name: string | null
  status: string
  tags: Record<string, string>
  spec: Record<string, unknown>
  snapshot_id: string | null
  snapshot_time: string | null
  discovered_at: string
}

export interface VMListResponse {
  items: ResourceSummary[]
  total: number
  snapshot_id: string | null
  snapshot_time: string | null
}

export interface ResourceEdge {
  from_id: string
  to_id: string
  kind: string
  snapshot_id: string | null
}

export interface ResourceDetailResponse {
  resource: ResourceSummary
  raw_ref: string | null
  provenance: Record<string, unknown>
  edges_out: ResourceEdge[]
  edges_in: ResourceEdge[]
}

export interface DiffResponse {
  snapshot_id_a: string
  snapshot_id_b: string
  added: string[]
  removed: string[]
  changed: string[]
}

export interface VMFilter {
  provider?: string
  region?: string
  status?: string
  min_vcpu?: number
  min_memory_gib?: number
  snapshot_id?: string
  limit?: number
  offset?: number
}

function buildQuery(params: Record<string, string | number | undefined | null>): string {
  const parts = Object.entries(params)
    .filter(([, v]) => v !== undefined && v !== null && v !== '')
    .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`)
  return parts.length ? `?${parts.join('&')}` : ''
}

export const inventoryApi = {
  /** List VMs with optional filters. */
  listVms: (filter: VMFilter = {}): Promise<VMListResponse> =>
    apiClient.get(
      `${BASE}/vms${buildQuery({
        provider: filter.provider,
        region: filter.region,
        status: filter.status,
        min_vcpu: filter.min_vcpu,
        min_memory_gib: filter.min_memory_gib,
        snapshot_id: filter.snapshot_id,
        limit: filter.limit ?? 50,
        offset: filter.offset ?? 0,
      })}`
    ),

  /** Get full resource detail. */
  getResource: (resourceId: string, includeRaw = false): Promise<ResourceDetailResponse> =>
    apiClient.get(`${BASE}/resources/${resourceId}${includeRaw ? '?include_raw=true' : ''}`),

  /** Compare two snapshots. */
  diffSnapshots: (snapshotIdA: string, snapshotIdB: string): Promise<DiffResponse> =>
    apiClient.get(`${BASE}/diff?snapshot_id_a=${snapshotIdA}&snapshot_id_b=${snapshotIdB}`),
}

export default inventoryApi
