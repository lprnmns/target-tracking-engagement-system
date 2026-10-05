export type KtrVector3 = [number, number, number]

export type KtrKinematicGroupName =
  | 'static_root'
  | 'yaw_group'
  | 'pitch_group'
  | 'camera_group'
  | 'launcher_group'
  | 'decorative_static_covers'

export type KtrKinematics = {
  assetVersion: string
  visualizationOnly: boolean
  safety: {
    physical_command_enabled: boolean
    serial_tx_enabled: boolean
    no_physical_command_generated: boolean
  }
  source: {
    cadPath: string
    glbPath: string
    kinematicsPath: string
    unitsSource: string
    unitsRuntime: string
  }
  nodes: Record<string, string>
  groups: Record<KtrKinematicGroupName, string[]>
  manualOverrideMap?: Record<string, unknown>
  pivots: Record<string, { position: KtrVector3, axis: KtrVector3, source: string }>
  anchors: Record<string, { position?: KtrVector3, direction?: KtrVector3, source?: string, originNode?: string }>
  joints: Record<string, {
    type: 'revolute'
    pivotNode: string
    axis: KtrVector3
    limitsDeg: [number, number]
    defaultDeg: number
    previewRangeDeg: [number, number]
    visualizationOnly: boolean
  }>
  offsets: {
    camera_to_launcher_mm: KtrVector3
    source?: string
  }
  validation: Record<string, unknown>
}

export const KTR_PHASE55_KINEMATICS_PATH = '/assets/digital-twin/ktr1_kinematics.json'
export const KTR_PHASE55_GLB_PATH = '/assets/digital-twin/ktr1_kinematic_world_phase55.glb'

export async function loadKtrKinematics(path = KTR_PHASE55_KINEMATICS_PATH): Promise<KtrKinematics | null> {
  // Kinematics is a read-only build asset. Let the browser reuse it across
  // landing preloads, cockpit mounts, and view-preset rebuilds.
  const response = await fetch(path, { cache: 'force-cache' })
  if (!response.ok) return null
  const payload = await response.json() as KtrKinematics
  if (payload.visualizationOnly !== true) return null
  if (payload.safety?.physical_command_enabled !== false) return null
  if (payload.safety?.serial_tx_enabled !== false) return null
  if (payload.safety?.no_physical_command_generated !== true) return null
  return payload
}

export function kinematicGroupForNode(kinematics: KtrKinematics | null, nodeName: string): KtrKinematicGroupName {
  if (!kinematics) return 'yaw_group'
  const exact = nodeName.trim()
  const lower = exact.toLowerCase()
  for (const group of ['camera_group', 'launcher_group', 'pitch_group', 'static_root', 'decorative_static_covers', 'yaw_group'] as KtrKinematicGroupName[]) {
    if (kinematics.groups[group]?.some((candidate) => candidate === exact || candidate.toLowerCase() === lower)) {
      return group
    }
  }
  const overrides = kinematics.manualOverrideMap as Record<string, unknown> | undefined
  const cameraKeywords = Array.isArray(overrides?.cameraKeywords) ? overrides.cameraKeywords.map(String) : ['kamera', 'camera']
  const launcherKeywords = Array.isArray(overrides?.launcherKeywords) ? overrides.launcherKeywords.map(String) : ['namlu', 'launcher', 'barrel', 'bileşen13', 'bilesen13']
  const staticKeywords = Array.isArray(overrides?.staticKeywords) ? overrides.staticKeywords.map(String) : ['tabla', 'base', 'alt gövde', 'alt govde']
  if (cameraKeywords.some((keyword) => lower.includes(keyword.toLowerCase()))) return 'camera_group'
  if (launcherKeywords.some((keyword) => lower.includes(keyword.toLowerCase()))) return 'launcher_group'
  if (staticKeywords.some((keyword) => lower.includes(keyword.toLowerCase()))) return 'static_root'
  return 'yaw_group'
}

export function isPitchChildGroup(group: KtrKinematicGroupName): boolean {
  return group === 'pitch_group' || group === 'camera_group' || group === 'launcher_group'
}

export function vectorFromKinematics(
  kinematics: KtrKinematics | null,
  section: 'anchors' | 'pivots',
  key: string,
  fallback: { x: number, y: number, z: number },
): { x: number, y: number, z: number } {
  const raw = section === 'anchors'
    ? kinematics?.anchors?.[key]?.position
    : kinematics?.pivots?.[key]?.position
  if (!raw || raw.length < 3) return fallback
  return { x: Number(raw[0]), y: Number(raw[1]), z: Number(raw[2]) }
}

export function clampPreviewDeg(value: number, limits: [number, number]): number {
  return Math.max(limits[0], Math.min(limits[1], value))
}
