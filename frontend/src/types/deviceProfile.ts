import type { CameraRuntimeProfile, VisionRuntimeProfile } from './deviceRuntime'
import type { VisionConfig } from './vision'

export interface DeviceProfile {
  profile_id: string
  display_name: string
  schema_version: number
  created_at: number
  updated_at: number
  command_profile: 'DRY_RUN' | 'LIVE_TEST' | 'VIDEO_DEMO' | 'COMPETITION'
  selected_camera_id: string | null
  selected_camera_stable_path: string | null
  selected_camera_name: string | null
  selected_camera_backend: string
  selected_pico_port: string | null
  selected_pico_baudrate: number
  selected_pico_usb_vid_pid: string | null
  selected_pico_serial_number: string | null
  selected_model_id: string | null
  selected_runtime_profile: string | null
  camera_profile: CameraRuntimeProfile | null
  vision_config: VisionConfig | null
  vision_runtime_profile: VisionRuntimeProfile | null
  servo_release_deg: number
  servo_fire_deg: number
  servo_pulse_s: number
  last_verified_at: number | null
  verification_status:
    | 'not_verified'
    | 'mock_verified'
    | 'demo_verified'
    | 'hardware_readonly_verified'
    | 'hardware_pending'
    | 'camera_pending'
    | 'pico_pending'
    | 'model_pending'
    | 'competition_not_verified'
    | 'mismatch'
  verification_level: string
  camera_binding_status: string
  pico_binding_status: string
  model_binding_status: string
  competition_status: string
  warnings: string[]
  no_physical_command_generated: boolean
}

export interface DeviceProfileResult {
  accepted: boolean
  profile: DeviceProfile
  warnings: string[]
  mismatch_warnings: string[]
  similar_candidates: Record<string, unknown>[]
  reason: string
  no_physical_command_generated: boolean
}

export interface DeviceProfilesList {
  profiles: DeviceProfile[]
  active_profile_id: string
  generated_at: number
  no_physical_command_generated: boolean
}
