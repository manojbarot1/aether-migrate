/**
 * Plans API client.
 */

import { apiClient } from './client'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface PlanSummary {
  id: string
  name: string
  description: string | null
  status: 'draft' | 'in_review' | 'approved' | 'superseded' | 'executed'
  target_provider: string
  target_region: string
  version: number
  content_hash: string | null
  created_by: string
  created_at: string
  updated_at: string
  source_resource_count: number
}

export interface PlanStep {
  step_number: number
  title: string
  description: string
  pre_check: string
  action: string
  post_check: string
  compensation: string
  estimated_duration_minutes: number
  is_manual: boolean
}

export interface SourceResource {
  id: string
  name: string | null
  kind: string
  provider: string
  region: string
}

export interface PlanDocument {
  plan_id: string
  workspace_id: string
  name: string
  version: number
  source_resources: SourceResource[]
  target_provider: string
  target_region: string
  sizing_choices: unknown[]
  assessment_findings: unknown[]
  acknowledged_findings: string[]
  prerequisites: string[]
  steps: PlanStep[]
  downtime_estimate_minutes: number
  downtime_basis: string
  rollback_plan: string
  assumptions: string[]
  snapshot_id: string
  snapshot_time: string
  catalog_version: string
  catalog_date: string
  created_at: string
  content_hash: string | null
}

export interface PlanDetail {
  id: string
  name: string
  description: string | null
  status: string
  target_provider: string
  target_region: string
  version: number
  content_hash: string | null
  plan_document: PlanDocument
  ai_narrative: string | null
  ai_narrative_generated_at: string | null
  ai_narrative_model: string | null
  approved_by: string | null
  approved_at: string | null
  approval_expires_at: string | null
  parent_id: string | null
  created_by: string
  created_at: string
  updated_at: string
}

export interface PlanCreateRequest {
  resource_ids: string[]
  name?: string
  description?: string
  target_provider: string
  target_region: string
  sizing_strategy?: string
  assumed_bandwidth_mbps?: number
  dual_run_days?: number
}

export interface ApproveResponse {
  plan_id: string
  status: string
  approved_by: string
  approved_at: string
  approval_expires_at: string
  plan_hash: string
}

export interface PlanDiff {
  plan_a_id: string
  plan_b_id: string
  changed_fields: Record<string, unknown[]>
  added_steps: number[]
  removed_steps: number[]
  changed_steps: number[]
}

export interface TofuValidationResult {
  valid: boolean
  errors: string[]
  warnings: string[]
}

// ---------------------------------------------------------------------------
// API calls
// ---------------------------------------------------------------------------

const plansApi = {
  list: () => apiClient.get<PlanSummary[]>('/api/v1/plans'),

  get: (id: string) => apiClient.get<PlanDetail>(`/api/v1/plans/${id}`),

  create: (body: PlanCreateRequest) =>
    apiClient.post<PlanSummary>('/api/v1/plans', body),

  approve: (id: string, note?: string) =>
    apiClient.post<ApproveResponse>(`/api/v1/plans/${id}/approve`, { note }),

  generateNarrative: (id: string) =>
    apiClient.post<PlanDetail>(`/api/v1/plans/${id}/generate-narrative`, {}),

  diff: (planId: string, otherId: string) =>
    apiClient.get<PlanDiff>(`/api/v1/plans/${planId}/diff/${otherId}`),

  exportJsonUrl: (id: string) => `/api/v1/plans/${id}/export/json`,
  exportMarkdownUrl: (id: string) => `/api/v1/plans/${id}/export/markdown`,
  exportTofuUrl: (id: string) => `/api/v1/plans/${id}/export/tofu`,

  validateTofu: (id: string) =>
    apiClient.post<TofuValidationResult>(`/api/v1/plans/${id}/validate-tofu`, {}),
}

export default plansApi
