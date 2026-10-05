export type FirstRunStepStatus = 'pending' | 'passed' | 'warning' | 'failed'
export type ReadinessProfileName = 'development_ready' | 'demo_ready' | 'field_dry_run_ready' | 'hardware_telemetry_ready' | 'competition_rehearsal_ready' | 'release_candidate_ready'
export type ReadinessProfileStatus = 'not_evaluated' | 'passed' | 'warning' | 'failed' | 'blocked'
export type CurrentFirstRunStatus = 'open' | 'passed' | 'warning' | 'failed'

export interface FirstRunStep {
  step_id: string
  title: string
  status: FirstRunStepStatus
  explanation: string
  suggested_fix: string | null
  blocking: boolean
  detail: Record<string, unknown>
}

export interface FirstRunReport {
  run_id: string
  created_at: number
  mode: string
  completed: boolean
  overall_status: FirstRunStepStatus
  steps: FirstRunStep[]
  summary: Record<string, unknown>
  profile_statuses: Record<ReadinessProfileName, ReadinessProfileStatus>
  profile_checklists: Record<ReadinessProfileName, FirstRunStep[]>
  report_path: string | null
  no_physical_command_generated: boolean
}

export interface FirstRunStatus {
  completed: boolean
  latest_report: FirstRunReport | null
  mode: string
  checks_count: number
  current_first_run_status: CurrentFirstRunStatus
  current_profile_id: ReadinessProfileName
  current_profile_evaluation_status: ReadinessProfileStatus
  last_successful_first_run: {
    run_id: string
    profile_id: ReadinessProfileName
    timestamp: number
    checks_count: number
  } | null
  stale_evidence: boolean
  no_physical_command_generated: boolean
}

export interface FirstRunActionResult {
  accepted: boolean
  reason: string
  status: FirstRunStatus
}
