import type { DigitalTwinTargetProjectionEstimate } from '../types/digitalTwin'

export type KtrTargetSceneProjection = {
  x: number
  y: number
  depth: number
  bearing: 'LEFT' | 'MID' | 'RIGHT'
  elevation: 'UP' | 'MID' | 'DOWN'
  depthBand: 'near' | 'mid' | 'far'
}

export function projectTargetIntoKtrFov(target: DigitalTwinTargetProjectionEstimate | null): KtrTargetSceneProjection {
  const xNorm = target?.normalized_center_x ?? 0.76
  const yNorm = target?.normalized_center_y ?? 0.54
  const area = target?.bbox_area_ratio ?? 0.031
  const depthBand = area > 0.045 ? 'near' : area < 0.016 ? 'far' : 'mid'
  const depth = depthBand === 'near' ? 0.28 : depthBand === 'far' ? 0.82 : 0.52
  return {
    x: (xNorm - 0.5) * 2,
    y: (0.5 - yNorm) * 2,
    depth,
    bearing: xNorm > 0.62 ? 'RIGHT' : xNorm < 0.38 ? 'LEFT' : 'MID',
    elevation: yNorm < 0.42 ? 'UP' : yNorm > 0.58 ? 'DOWN' : 'MID',
    depthBand,
  }
}
