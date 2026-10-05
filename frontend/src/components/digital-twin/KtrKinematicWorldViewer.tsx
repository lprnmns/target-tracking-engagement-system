export const KTR_KINEMATIC_WORLD_VIEWER_CONTRACT = {
  glbPath: '/assets/digital-twin/ktr1_kinematic_world_phase55.glb',
  kinematicsPath: '/assets/digital-twin/ktr1_kinematics.json',
  sourceCadPath: 'work/ktr1.step',
  visualizationOnly: true,
  previewControls: ['yaw', 'pitch', 'reset_pose'],
  forbiddenRuntimeEffects: ['serial_tx', 'pico_command', 'motor_command', 'fire_command'],
} as const

export type KtrKinematicPreviewPose = {
  yawDeg: number
  pitchDeg: number
}
