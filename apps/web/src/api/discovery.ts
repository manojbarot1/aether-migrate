/**
 * Discovery API client — trigger workflows and query snapshot state.
 */

import { apiClient } from './client'

const BASE = '/api/v1/discovery'

export interface DiscoveryStartResponse {
  job_id: string
  snapshot_id: string
}

export interface SnapshotResponse {
  id: string
  workspace_id: string
  connection_id: string
  provider: string
  status: string
  started_at: string
  completed_at: string | null
  coverage: Record<string, string>
}

export interface ConnectionStatusResponse {
  connection_id: string
  latest_snapshot: SnapshotResponse | null
  last_discovery_time: string | null
}

export const discoveryApi = {
  /** Start a discovery workflow for a connection. */
  refresh: (connectionId: string): Promise<DiscoveryStartResponse> =>
    apiClient.post(`${BASE}/connections/${connectionId}/refresh`, {}),

  /** List all snapshots for the workspace (newest first). */
  listSnapshots: (): Promise<SnapshotResponse[]> =>
    apiClient.get(`${BASE}/snapshots`),

  /** Get snapshot detail with coverage report. */
  getSnapshot: (snapshotId: string): Promise<SnapshotResponse> =>
    apiClient.get(`${BASE}/snapshots/${snapshotId}`),

  /** Get latest snapshot + discovery time for a connection. */
  getConnectionStatus: (connectionId: string): Promise<ConnectionStatusResponse> =>
    apiClient.get(`${BASE}/connections/${connectionId}/status`),
}

export default discoveryApi
