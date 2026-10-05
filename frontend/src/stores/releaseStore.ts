import { ref } from 'vue'
import { defineStore } from 'pinia'
import { buildReleasePackage, fetchLatestCleanroom, fetchLatestReleasePackage, fetchReleaseStatus, runCleanroom, runColdStartCheck, runReleaseCheck } from '../api/release'
import type { CleanroomVerificationRecord, ReleasePackageRecord, ReleaseStatus } from '../types/release'

const defaultStatus: ReleaseStatus = {
  launcher_available: false,
  frontend_static_available: false,
  writable_runtime_dirs: false,
  offline_readiness: 'unknown',
  field_profile_saved: false,
  status: 'warning',
  platform: null,
  python_version: null,
  app_root: null,
  writable_logs: false,
  writable_exports: false,
  config_loaded: false,
  model_dir_present: false,
  active_model_loaded: false,
  camera_devices_detected: 0,
  serial_devices_detected: 0,
  pico_candidate_count: 0,
  hardware_command_enabled: false,
  dry_run: true,
  no_fire: true,
  safety_invariant_ok: true,
  release_manifest_path: null,
  cold_start_evidence: {},
  suggested_actions: [],
  checks: [],
  generated_at: 0,
  no_physical_command_generated: true,
}

export const useReleaseStore = defineStore('release', () => {
  const status = ref<ReleaseStatus>(defaultStatus)
  const latestPackage = ref<ReleasePackageRecord | null>(null)
  const latestCleanroom = ref<CleanroomVerificationRecord | null>(null)
  const isBuildingPackage = ref(false)
  const isRunningCleanroom = ref(false)
  const error = ref<string | null>(null)

  async function refresh(): Promise<void> {
    try {
      const [nextStatus, nextPackage, nextCleanroom] = await Promise.all([fetchReleaseStatus(), fetchLatestReleasePackage(), fetchLatestCleanroom()])
      status.value = nextStatus
      latestPackage.value = nextPackage
      latestCleanroom.value = nextCleanroom
    } catch (caught) {
      error.value = caught instanceof Error ? caught.message : 'Release status failed'
    }
  }

  async function check(): Promise<void> {
    status.value = await runReleaseCheck()
  }

  async function coldStartCheck(): Promise<void> {
    status.value = await runColdStartCheck()
  }

  async function buildPackage(): Promise<void> {
    isBuildingPackage.value = true
    error.value = null
    try {
      latestPackage.value = await buildReleasePackage()
      status.value = await fetchReleaseStatus()
    } catch (caught) {
      error.value = caught instanceof Error ? caught.message : 'Release package build failed'
    } finally {
      isBuildingPackage.value = false
    }
  }

  async function runCleanroomVerification(): Promise<void> {
    isRunningCleanroom.value = true
    error.value = null
    try {
      latestCleanroom.value = await runCleanroom()
    } catch (caught) {
      error.value = caught instanceof Error ? caught.message : 'Clean-room verification failed'
    } finally {
      isRunningCleanroom.value = false
    }
  }

  return { status, latestPackage, latestCleanroom, isBuildingPackage, isRunningCleanroom, error, refresh, check, coldStartCheck, buildPackage, runCleanroomVerification }
})
