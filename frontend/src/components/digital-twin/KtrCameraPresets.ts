export type KtrCameraPresetId =
  | 'freecad'
  | 'operator'
  | 'front'
  | 'side'
  | 'top'
  | 'rear'
  | 'weapon'
  | 'weaponCloseup'
  | 'chase'
  | 'camera'
  | 'target'

export const KTR_CAMERA_PRESETS: Array<{ id: KtrCameraPresetId, label: string, purpose: string }> = [
  { id: 'freecad', label: 'FreeCAD Match', purpose: 'CAD-like orthographic full silhouette' },
  { id: 'operator', label: 'Operator (Düz)', purpose: 'Namlu ucu / kamera doğrultusunda düz komuta açısı' },
  { id: 'front', label: 'Front', purpose: 'front weapon and launcher visibility' },
  { id: 'side', label: 'Side', purpose: 'pitch axis and offset inspection' },
  { id: 'top', label: 'Top-down', purpose: 'yaw and projection geometry' },
  { id: 'rear', label: 'Rear', purpose: 'base and rear assembly inspection' },
  { id: 'weapon', label: 'Weapon Focus', purpose: 'front weapon/camera area focus' },
  { id: 'weaponCloseup', label: 'Front Weapon Closeup', purpose: 'launcher and camera close inspection' },
  { id: 'chase', label: 'Launcher Axis POV', purpose: 'launcher-axis aligned inspection' },
  { id: 'camera', label: 'Camera POV', purpose: 'camera-axis aligned inspection' },
  { id: 'target', label: 'Target POV', purpose: 'target looking back to system' },
]
