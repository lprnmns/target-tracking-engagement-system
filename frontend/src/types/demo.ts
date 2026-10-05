export type DemoStepStatus = 'pending' | 'completed' | 'warning' | 'blocked'
export type DemoStepSource = 'system' | 'first_run' | 'vision' | 'data_lab' | 'report' | 'safety'

export interface DemoTimelineEvent {
  event_id: string
  step: string
  title: string
  status: DemoStepStatus
  source: DemoStepSource
  timestamp: number
  summary: string
  evidence_ref: string | null
  advisory_only: boolean
  no_physical_command_generated: boolean
}

export interface DemoVerdict {
  release_demo_ready: boolean
  release_demo_warnings: string[]
  release_demo_blockers: string[]
  competition_ready: boolean
  competition_blockers: string[]
  dataset_ready_for_training: boolean
  dataset_blockers: string[]
  reasons: string[]
  advisory_only: boolean
  no_physical_command_generated: boolean
}

export interface DemoTimeline {
  run_id: string
  created_at: number
  status: 'not_run' | 'completed' | 'warning' | 'blocked'
  events: DemoTimelineEvent[]
  verdict: DemoVerdict
  report_export_id: string | null
  advisory_only: boolean
  no_physical_command_generated: boolean
}

export interface DemoReadiness {
  release_demo_ready: boolean
  release_demo_warnings: string[]
  release_demo_blockers: string[]
  competition_ready: boolean
  competition_blockers: string[]
  dataset_ready_for_training: boolean
  dataset_blockers: string[]
  no_physical_command_generated: boolean
}

export interface JuryRehearsal {
  rehearsal_id?: string
  status?: string
  timeline_id?: string
  report_export_id?: string
  cleanroom_run_id?: string
  cleanroom_verified?: boolean
  latest_release_package?: string
  verdict?: DemoVerdict
  no_physical_command_generated: boolean
}
