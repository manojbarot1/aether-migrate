/**
 * AETHER MIGRATE — cost comparison API client
 */

import apiClient from './client'

export interface CostCompareRequest {
  resource_id: string
  target_providers: string[]
  target_regions: Record<string, string>
  scenarios: string[]
  currency: string
}

export interface CostBreakdown {
  compute_monthly: string
  storage_monthly: string
  network_monthly: string
  migration_egress: string
  dual_run: string
  total_monthly: string
  total_first_year: string
  currency: string
  fx_rate: string | null
  catalog_version: string
  catalog_date: string
  assumptions: string[]
  is_estimate: boolean
}

export interface CostTarget {
  sku: string
  provider: string
  region: string
  scenario: string
  breakdown: CostBreakdown
  licence_review_required: boolean
  warnings: string[]
}

export interface CostCompareResponse {
  resource_id: string
  source_list_monthly_usd: string | null
  source_actual_monthly_usd: string | null
  targets: CostTarget[]
  catalog_version: string
  snapshot_id: string
}

export interface CatalogProviderStatus {
  provider: string
  version: string | null
  effective_from: string | null
  age_hours: number | null
  is_stale: boolean
}

export interface CatalogStatusResponse {
  providers: CatalogProviderStatus[]
  checked_at: string
}

export const costApi = {
  compare: (req: CostCompareRequest) =>
    apiClient.post<CostCompareResponse>('/api/v1/cost/compare', req),

  catalogStatus: () =>
    apiClient.get<CatalogStatusResponse>('/api/v1/cost/catalog/status'),
}

export default costApi
