export type ReportExportKind = 'ktr_summary' | 'demo_pack' | 'readiness_pack'
export type ReportExportStatus = 'idle' | 'running' | 'completed' | 'failed'

export interface ReportExportRequest {
  title?: string | null
  include_screenshots?: boolean | null
  notes?: string | null
}

export interface ReportExportRecord {
  export_id: string
  kind: ReportExportKind
  status: ReportExportStatus
  created_at: number
  output_dir: string
  files: string[]
  summary: Record<string, unknown>
  no_physical_command_generated: boolean
  error: string | null
}

export interface ReportsStatus {
  exports_count: number
  latest_export: ReportExportRecord | null
  root_dir: string
  no_physical_command_generated: boolean
}
