export type DepthBand = 'near' | 'mid' | 'far'
export type EngagementStatus = 'INSIDE_FOV_FIRE_BLOCKED' | 'OUTSIDE_FOV_HOLD'

export interface EngagementGeometryInput {
  x_norm: number
  y_norm: number
  bbox_area_relative: number
  fov_horizontal_deg: number
  fov_vertical_deg: number
  camera_to_launcher_offset_z_mm: number
}

export interface EngagementGeometryProjection {
  normalized_x: number
  normalized_y: number
  bbox_area_relative: number
  target_scene_x: number
  target_scene_y: number
  target_scene_depth: number
  target_inside_fov: boolean
  launcher_axis_error_x: number
  launcher_axis_error_y: number
  engagement_status: EngagementStatus
  depth_band: DepthBand
  bearing_label: 'LEFT' | 'MID' | 'RIGHT'
  elevation_label: 'UP' | 'MID' | 'DOWN'
  camera_to_launcher_offset_z_mm: number
  no_physical_command_generated: true
}

export function mapDetectionToEngagementGeometry(input: EngagementGeometryInput): EngagementGeometryProjection {
  const normalizedX = clamp(input.x_norm, 0, 1)
  const normalizedY = clamp(input.y_norm, 0, 1)
  const area = clamp(input.bbox_area_relative, 0.000001, 1)
  const screenX = clamp((normalizedX - 0.5) * 2, -1, 1)
  const screenY = clamp((0.5 - normalizedY) * 2, -1, 1)
  const linearSize = Math.sqrt(area)
  const farLinearSize = 0.025
  const nearLinearSize = 0.32
  const closeness = clamp((linearSize - farLinearSize) / (nearLinearSize - farLinearSize), 0, 1)
  const relativeDepth = 1 - closeness
  const targetSceneDepth = clamp(0.28 + relativeDepth * 0.62, 0.22, 0.92)
  const fovWidthAtDepth = 0.16 + targetSceneDepth * 0.82
  const fovHeightAtDepth = 0.1 + targetSceneDepth * 0.54
  const targetSceneX = screenX * fovWidthAtDepth
  const targetSceneY = screenY * fovHeightAtDepth
  const verticalOffset = input.camera_to_launcher_offset_z_mm / 1000
  const targetInsideFov = Math.abs(screenX) <= 1 && Math.abs(screenY) <= 1

  return {
    normalized_x: round(normalizedX, 4),
    normalized_y: round(normalizedY, 4),
    bbox_area_relative: round(area, 6),
    target_scene_x: round(targetSceneX, 4),
    target_scene_y: round(targetSceneY, 4),
    target_scene_depth: round(targetSceneDepth, 4),
    target_inside_fov: targetInsideFov,
    launcher_axis_error_x: round(targetSceneX, 4),
    launcher_axis_error_y: round(targetSceneY - verticalOffset, 4),
    engagement_status: targetInsideFov ? 'INSIDE_FOV_FIRE_BLOCKED' : 'OUTSIDE_FOV_HOLD',
    depth_band: rangeBand(relativeDepth),
    bearing_label: normalizedX > 0.62 ? 'RIGHT' : normalizedX < 0.38 ? 'LEFT' : 'MID',
    elevation_label: normalizedY < 0.42 ? 'UP' : normalizedY > 0.58 ? 'DOWN' : 'MID',
    camera_to_launcher_offset_z_mm: input.camera_to_launcher_offset_z_mm,
    no_physical_command_generated: true,
  }
}

function rangeBand(relativeDepth: number): DepthBand {
  if (relativeDepth <= 0.33) return 'near'
  if (relativeDepth <= 0.66) return 'mid'
  return 'far'
}

function clamp(value: number, lower: number, upper: number): number {
  if (!Number.isFinite(value)) return lower
  return Math.max(lower, Math.min(upper, value))
}

function round(value: number, digits: number): number {
  const scale = 10 ** digits
  return Math.round(value * scale) / scale
}
