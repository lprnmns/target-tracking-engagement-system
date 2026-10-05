export type Phase56Vector3 = [number, number, number]

export type Phase56DeviceFrame = {
  schema: 'phase56_device_frame'
  status: string
  visualizationOnly: boolean
  sourceCad: {
    units: string
    up: string
    front: string
    right: string
    frontDecision: string
  }
  runtimeWorld: {
    units: string
    up: string
    front: string
    right: string
    transform: string
  }
  canonicalViews: Record<string, { lookDirectionRuntime: Phase56Vector3, description?: string }>
  safety: {
    physical_command_enabled: boolean
    serial_tx_enabled: boolean
    no_physical_command_generated: boolean
  }
}

export type Phase56MechanicalGroups = {
  schema: 'phase56_mechanical_groups'
  status: string
  groupingMethod: string
  groups: Record<string, string[]>
  counts: Record<string, number>
  validationRequired: string[]
  safety: {
    physical_command_enabled: boolean
    serial_tx_enabled: boolean
    no_physical_command_generated: boolean
  }
}

export type Phase56JointCalibration = {
  schema: 'phase56_joint_calibration'
  status: string
  visualizationOnly: boolean
  joints: Record<string, {
    physicalMotor: string
    pivot: Phase56Vector3
    axisRuntime: Phase56Vector3
    limitsDeg: [number, number]
    stepToDegree: number | null
    source: string
  }>
  anchors: Record<string, {
    position: Phase56Vector3
    axisRuntime?: Phase56Vector3
    source: string
  }>
  offsets: Record<string, unknown>
  safety: {
    physical_command_enabled: boolean
    serial_tx_enabled: boolean
    no_physical_command_generated: boolean
  }
}

export const PHASE56_DEVICE_FRAME_PATH = '/assets/digital-twin/ktr1_device_frame.json'
export const PHASE56_MECHANICAL_GROUPS_PATH = '/assets/digital-twin/ktr1_mechanical_groups.json'
export const PHASE56_JOINT_CALIBRATION_PATH = '/assets/digital-twin/ktr1_joint_calibration.json'

async function loadSafetyCheckedJson<T extends { safety?: Record<string, unknown>, visualizationOnly?: boolean }>(path: string): Promise<T | null> {
  // These contracts are immutable frontend assets; cache them so a scene
  // rebuild does not perform four extra network round trips.
  const response = await fetch(path, { cache: 'force-cache' })
  if (!response.ok) return null
  const payload = await response.json() as T
  if (payload.safety?.physical_command_enabled !== false) return null
  if (payload.safety?.serial_tx_enabled !== false) return null
  if (payload.safety?.no_physical_command_generated !== true) return null
  if ('visualizationOnly' in payload && payload.visualizationOnly !== true) return null
  return payload
}

export async function loadPhase56DeviceFrame(): Promise<Phase56DeviceFrame | null> {
  return loadSafetyCheckedJson<Phase56DeviceFrame>(PHASE56_DEVICE_FRAME_PATH)
}

export async function loadPhase56MechanicalGroups(): Promise<Phase56MechanicalGroups | null> {
  return loadSafetyCheckedJson<Phase56MechanicalGroups>(PHASE56_MECHANICAL_GROUPS_PATH)
}

export async function loadPhase56JointCalibration(): Promise<Phase56JointCalibration | null> {
  return loadSafetyCheckedJson<Phase56JointCalibration>(PHASE56_JOINT_CALIBRATION_PATH)
}

export function phase56Vector(
  calibration: Phase56JointCalibration | null,
  key: string,
  fallback: { x: number, y: number, z: number },
): { x: number, y: number, z: number } {
  const raw = calibration?.anchors?.[key]?.position
  if (!raw || raw.length < 3) return fallback
  return { x: Number(raw[0]), y: Number(raw[1]), z: Number(raw[2]) }
}
