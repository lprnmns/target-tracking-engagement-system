export type SelfTestRunStatus = 'idle' | 'running' | 'passed' | 'warning' | 'failed' | 'cancelled'
export type SelfTestReadinessLevel = 'not_ready' | 'demo_ready' | 'hardware_readonly_ready' | 'field_test_ready' | 'hardware_blocked'
export type SelfTestStepStatus = 'pending' | 'running' | 'passed' | 'warning' | 'failed' | 'skipped'
export type SelfTestSeverity = 'info' | 'warning' | 'critical'

export interface SelfTestStep {
  step_id: string
  name: string
  category: string
  status: SelfTestStepStatus
  severity: SelfTestSeverity
  started_at: number | null
  ended_at: number | null
  duration_ms: number | null
  message: string
  details: Record<string, unknown>
  blocking: boolean
  suggested_action: string | null
}

export interface SelfTestRun {
  run_id: string
  started_at: number
  ended_at: number | null
  status: SelfTestRunStatus
  overall_ready: boolean
  readiness_level: SelfTestReadinessLevel
  dry_run: boolean
  hardware_enabled: boolean
  no_physical_command_generated: boolean
  steps: SelfTestStep[]
  summary: {
    passed?: number
    warning?: number
    failed?: number
    skipped?: number
    critical_failures?: number
    suggested_actions?: string[]
    git_hash?: string
  }
  report_path: string | null
}

export interface SelfTestStatus {
  latest_run: SelfTestRun | null
  running: boolean
  runs_count: number
}
