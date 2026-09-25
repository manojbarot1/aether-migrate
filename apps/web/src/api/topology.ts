/**
 * Topology API client.
 *
 * Wraps /api/v1/topology/* endpoints.
 */

import { apiClient } from './client'

export interface TopologyNode {
  id: string
  native_id: string
  name: string
  kind: string
  provider: string
  region: string
  status: string
  is_root: boolean
  metadata: Record<string, unknown>
}

export interface TopologyEdge {
  from_id: string
  to_id: string
  kind: string
  label: string
}

export interface TopologyGraphResponse {
  nodes: TopologyNode[]
  edges: TopologyEdge[]
  root_id: string
  snapshot_id: string | null
  snapshot_time: string | null
  truncated: boolean
}

export interface TopologyDiffResponse {
  added_nodes: TopologyNode[]
  removed_nodes: TopologyNode[]
  changed_nodes: TopologyNode[]
  added_edges: TopologyEdge[]
  removed_edges: TopologyEdge[]
}

function buildUrl(base: string, params: Record<string, string | number | undefined>): string {
  const qs = Object.entries(params)
    .filter(([, v]) => v !== undefined)
    .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`)
    .join('&')
  return qs ? `${base}?${qs}` : base
}

export async function fetchResourceSubgraph(
  resourceId: string,
  depth: number = 2,
  direction: string = 'both',
  snapshotId?: string,
): Promise<TopologyGraphResponse> {
  const url = buildUrl(`/api/v1/topology/resources/${resourceId}`, {
    depth, direction, snapshot_id: snapshotId,
  })
  return apiClient.get<TopologyGraphResponse>(url)
}

export async function fetchRegionGraph(
  region: string,
  connectionId?: string,
  snapshotId?: string,
): Promise<TopologyGraphResponse> {
  const url = buildUrl(`/api/v1/topology/regions/${encodeURIComponent(region)}`, {
    connection_id: connectionId, snapshot_id: snapshotId,
  })
  return apiClient.get<TopologyGraphResponse>(url)
}

export async function exportMermaid(
  resourceId: string,
  depth: number = 2,
  snapshotId?: string,
): Promise<string> {
  const url = buildUrl(`/api/v1/topology/export/${resourceId}`, {
    format: 'mermaid', depth, snapshot_id: snapshotId,
  })
  return apiClient.get<string>(url)
}

export async function fetchTopologyDiff(
  snapshotIdA: string,
  snapshotIdB: string,
): Promise<TopologyDiffResponse> {
  return apiClient.get<TopologyDiffResponse>(`/api/v1/topology/diff/${snapshotIdA}/${snapshotIdB}`)
}
