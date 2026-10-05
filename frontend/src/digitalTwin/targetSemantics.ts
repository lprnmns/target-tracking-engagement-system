import type { BodyDetection } from '../types/vision'

export type DigitalTwinTargetClass = 'balloon' | 'ballistic_missile' | 'helicopter' | 'f16' | 'mini_micro_uav' | 'unknown_target'

export type TargetVisualSpec = {
  className: DigitalTwinTargetClass
  label: string
  assetPath: string | null
  referenceSpanM: number
  dimensionsM: readonly [number, number, number]
  color: number
  modelRotation: readonly [number, number, number]
  modelYawRad: number
  uncertaintyRatio: number
}

export type TargetRangeEstimate = {
  rangeM: number
  uncertaintyM: number
  source: 'class_bbox_pinhole_estimate'
}

export const BALLOON_DIAMETER_M = 0.14

export const TARGET_VISUAL_SPECS: Record<DigitalTwinTargetClass, TargetVisualSpec> = {
  balloon: {
    className: 'balloon',
    label: 'Balon',
    assetPath: null,
    referenceSpanM: BALLOON_DIAMETER_M,
    dimensionsM: [BALLOON_DIAMETER_M, BALLOON_DIAMETER_M, BALLOON_DIAMETER_M],
    color: 0xf97316,
    modelRotation: [0, 0, 0],
    modelYawRad: 0,
    uncertaintyRatio: 0.25,
  },
  ballistic_missile: {
    className: 'ballistic_missile',
    label: 'Balistik Füze',
    assetPath: '/assets/targets/ballistic_missile.glb',
    referenceSpanM: 0.5,
    dimensionsM: [0.103, 0.217, 0.5],
    color: 0xf97316,
    // This source object is already horizontal in the Three.js frame.
    modelRotation: [0, 0, 0],
    modelYawRad: 0,
    uncertaintyRatio: 0.34,
  },
  helicopter: {
    className: 'helicopter',
    label: 'Helikopter',
    assetPath: '/assets/targets/helicopter.glb',
    referenceSpanM: 0.583,
    dimensionsM: [0.392, 0.583, 0.177],
    color: 0x60a5fa,
    // These printable aircraft assets are Z-up. Three.js is Y-up, so rotate
    // their source frame once to keep them level with the turret floor.
    modelRotation: [-Math.PI / 2, 0, 0],
    modelYawRad: 0,
    uncertaintyRatio: 0.38,
  },
  f16: {
    className: 'f16',
    label: 'Savaş Uçağı',
    assetPath: '/assets/targets/f16.glb',
    referenceSpanM: 0.5,
    dimensionsM: [0.3, 0.5, 0.138],
    color: 0xa78bfa,
    modelRotation: [-Math.PI / 2, 0, 0],
    // The F-16 source forward axis is opposite to the other levelled assets.
    modelYawRad: Math.PI,
    uncertaintyRatio: 0.35,
  },
  mini_micro_uav: {
    className: 'mini_micro_uav',
    label: 'Mini/Mikro İHA',
    assetPath: '/assets/targets/mini_micro_uav.glb',
    referenceSpanM: 0.375,
    dimensionsM: [0.286, 0.375, 0.195],
    color: 0x34d399,
    modelRotation: [-Math.PI / 2, 0, 0],
    modelYawRad: 0,
    uncertaintyRatio: 0.4,
  },
  unknown_target: {
    className: 'unknown_target',
    label: 'Bilinmeyen hedef',
    assetPath: null,
    referenceSpanM: 0.45,
    dimensionsM: [0.45, 0.45, 0.45],
    color: 0xfacc15,
    modelRotation: [0, 0, 0],
    modelYawRad: 0,
    uncertaintyRatio: 0.5,
  },
}

export function canonicalTargetClass(value: string | null | undefined): DigitalTwinTargetClass {
  const normalized = String(value ?? '').trim().toLowerCase().replace(/[\s\-\/]+/g, '_')
  if (normalized === 'balloon' || normalized === 'balon') return 'balloon'
  if (normalized === 'ballistic_missile' || normalized === 'ballistik_fuze' || normalized === 'fuze') return 'ballistic_missile'
  if (normalized === 'helicopter' || normalized === 'helikopter') return 'helicopter'
  if (normalized === 'f16' || normalized === 'f_16') return 'f16'
  if (normalized === 'mini_micro_uav' || normalized === 'mini_micro_iha' || normalized === 'drone' || normalized === 'uav') return 'mini_micro_uav'
  return 'unknown_target'
}

export function visualSpecForTarget(value: string | null | undefined): TargetVisualSpec {
  return TARGET_VISUAL_SPECS[canonicalTargetClass(value)]
}

export function rangeEstimateFromBbox(
  className: string | null | undefined,
  bbox: { w: number, h: number },
  frameWidth: number,
  frameHeight: number,
  fovHorizontalDeg: number,
  fovVerticalDeg: number,
  referenceSpanM?: number | null,
): TargetRangeEstimate {
  const spec = visualSpecForTarget(className)
  const focalX = Math.max(1, frameWidth) / (2 * Math.tan((Math.max(1, fovHorizontalDeg) * Math.PI / 180) / 2))
  const focalY = Math.max(1, frameHeight) / (2 * Math.tan((Math.max(1, fovVerticalDeg) * Math.PI / 180) / 2))
  const physicalSpanM = Math.max(0.001, referenceSpanM ?? spec.referenceSpanM)
  const depthFromWidth = (focalX * physicalSpanM) / Math.max(1, bbox.w)
  const depthFromHeight = (focalY * physicalSpanM) / Math.max(1, bbox.h)
  // A balloon is approximately circular, so both bbox axes describe the same
  // physical diameter. The geometric mean is stable when YOLO pads one edge
  // of the box and avoids mixing horizontal pixels with vertical FOV.
  const unconstrained = canonicalTargetClass(className) === 'balloon'
    ? Math.sqrt(depthFromWidth * depthFromHeight)
    : Math.min(depthFromWidth, depthFromHeight)
  const rangeM = Math.max(0.3, Math.min(40, unconstrained))
  return {
    rangeM: Number(rangeM.toFixed(2)),
    uncertaintyM: Number(Math.max(0.15, rangeM * spec.uncertaintyRatio).toFixed(2)),
    source: 'class_bbox_pinhole_estimate',
  }
}

export function bodyTargetKey(body: BodyDetection): string {
  return `body:${body.id}:${canonicalTargetClass(body.class_name)}`
}
