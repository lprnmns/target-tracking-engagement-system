export interface ReleaseCheckItem {
  name: string
  status: string
  message: string
  blocking: boolean
  detail: Record<string, unknown>
}

export interface ReleaseStatus {
  launcher_available: boolean
  frontend_static_available: boolean
  writable_runtime_dirs: boolean
  offline_readiness: string
  field_profile_saved: boolean
  status: string
  platform: string | null
  python_version: string | null
  app_root: string | null
  writable_logs: boolean
  writable_exports: boolean
  config_loaded: boolean
  model_dir_present: boolean
  active_model_loaded: boolean
  camera_devices_detected: number
  serial_devices_detected: number
  pico_candidate_count: number
  hardware_command_enabled: boolean
  dry_run: boolean
  no_fire: boolean
  safety_invariant_ok: boolean
  release_manifest_path: string | null
  cold_start_evidence: Record<string, unknown>
  suggested_actions: string[]
  checks: ReleaseCheckItem[]
  generated_at: number
  no_physical_command_generated: boolean
}

export interface ReleasePackageRecord {
  package_id: string
  output_dir: string
  zip_path: string
  files_count: number
  checksums_path: string
  manifest_path: string
  commit_hash: string | null
  source_commit: string
  package_generated_commit: string
  package_workflow_commit: string
  report_commit: string
  checksum_status: string
  release_demo_ready: boolean
  competition_ready: boolean
  dataset_ready_for_training: boolean
  no_physical_command_generated: boolean
  safety_invariant: string
  created_at: number
}

export interface CleanroomVerificationRecord {
  run_id: string
  package_id: string
  zip_path: string
  extract_path: string
  launch_command: string
  smoke_status: string
  endpoints_passed: number
  endpoints_total: number
  frontend_dist_present: boolean
  backend_present: boolean
  forbidden_entries: string[]
  secrets_or_tokens: string[]
  launcher_hardcoded_repo_path: boolean
  release_demo_ready: boolean
  competition_ready: boolean
  no_physical_command_generated: boolean
  created_at: number
}
