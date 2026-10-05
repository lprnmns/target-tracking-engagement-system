export const KTR_TACTICAL_OVERLAY_LAYER_NAMES = {
  cameraFov: 'camera_fov',
  cameraAxis: 'camera_axis',
  launcherAxis: 'launcher_axis',
  targetProjection: 'target_projection_marker',
  offsetBracket: 'camera_launcher_30mm_offset',
  noGoZone: 'no_go_person_safety_volume',
  rangeMarkers: 'range_depth_floor_markers',
} as const

export const KTR_TACTICAL_OVERLAY_SAFETY = {
  visualizationOnly: true,
  physical_command_enabled: false,
  serial_tx_enabled: false,
  no_physical_command_generated: true,
} as const
