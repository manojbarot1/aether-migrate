/**
 * Assessment API client.
 */

import { apiClient } from './client'

export interface ReadinessScore {
  status: 'blocked' | 'ready_with_warnings' | 'ready'
  score: number
  blockers: number
  warnings: number
  info: number
}

export interface Finding {
  rule_id: string
  rule_version: string
  severity: 'blocker' | 'warning' | 'info'
  applies_to: string
  title: string
  message: string
  evidence: Record<string, unknown>
  remediation: string
  docs_url: string | null
  acknowledged: boolean
  acknowledged_reason: string | null
}

export interface AssessmentResult {
  resource_id: string
  target_provider: string
  target_region: string
  readiness: ReadinessScore
  findings: Finding[]
  snapshot_id: string
  snapshot_time: string
  catalog_version: string
}

export interface AssessmentHistoryItem {
  id: string
  target_provider: string
  target_region: string
  readiness: string
  readiness_score: number
  blocker_count: number
  warning_count: number
  info_count: number
  created_at: string
  completed_at: string | null
}

export interface AcknowledgeRequest {
  reason: string
  expires_at: string | null
}

const assessmentApi = {
  async runAssessment(
    resourceId: string,
    targetProvider: string,
    targetRegion: string,
  ): Promise<AssessmentResult> {
    return apiClient.post<AssessmentResult>('/api/v1/assessment/run', {
      resource_id: resourceId,
      target_provider: targetProvider,
      target_region: targetRegion,
    })
  },

  async getResults(resourceId: string): Promise<AssessmentHistoryItem[]> {
    return apiClient.get<AssessmentHistoryItem[]>(
      `/api/v1/assessment/results/${resourceId}`,
    )
  },

  async acknowledge(
    resourceId: string,
    ruleId: string,
    req: AcknowledgeRequest,
  ): Promise<void> {
    await apiClient.post(
      `/api/v1/assessment/findings/${resourceId}/${ruleId}/acknowledge`,
      req,
    )
  },

  async deleteAcknowledgement(resourceId: string, ruleId: string): Promise<void> {
    await apiClient.delete(
      `/api/v1/assessment/findings/${resourceId}/${ruleId}/acknowledge`,
    )
  },
}

export default assessmentApi
