export function humanizeId(value: string): string {
  return value.replace(/_/g, ' ').replace(/\b\w/g, (char) => char.toUpperCase())
}

const gateLabels: Record<string, string> = {
  system_disarmed_gate: 'System Mode',
  system_armed_gate: 'Armed for Dry-run',
  dry_run_gate: 'System Dry-Run',
  hardware_enabled_gate: 'Hardware Enabled',
  estop_gate: 'E-stop Released',
  pico_connected_gate: 'Pico Connection Available',
  pico_heartbeat_gate: 'Pico Heartbeat',
  serial_ok_gate: 'Serial Transport OK',
  vision_running_gate: 'Vision Inference Running',
  body_detected_gate: 'Body Target Detected',
  balloon_detected_gate: 'Balloon Detected',
  team_classified_gate: 'Team Classified',
  enemy_target_gate: 'Enemy Target Confirmed',
  friend_rejection_gate: 'Friend Target Rejected',
  range_valid_gate: 'Range Valid',
  stable_track_gate: 'Stable Track',
  forbidden_zone_gate: 'Forbidden Zone Clear',
  operator_confirm_gate: 'Operator Confirmed',
  motion_soft_limits_gate: 'Motion Soft Limits',
  motion_estop_gate: 'Motion E-stop',
  motion_fault_gate: 'Motion Fault Clear',
  motion_driver_gate: 'Motion Driver',
  motion_dry_run_gate: 'Motion Dry-Run',
}

const reasonLabels: Record<string, string> = {
  system_disarmed: 'System is disarmed',
  hardware_disabled: 'Real hardware is disabled',
  body_not_detected: 'No body target detected',
  balloon_not_detected: 'No balloon detected',
  team_unknown: 'Team classification unavailable',
  target_not_enemy: 'Target is not confirmed enemy',
  target_is_friend: 'Target classified as friend',
  range_invalid: 'Range is invalid or unavailable',
  track_not_stable: 'Track is not stable',
  operator_confirmation_missing: 'Operator confirmation missing',
  serial_fault: 'Serial transport fault',
  motion_soft_limit_fault: 'Motion soft limit fault',
  motion_estop_active: 'Motion E-stop active',
  motion_fault: 'Motion service fault',
  motion_driver_disabled: 'Motion driver disabled',
  motion_dry_run: 'Motion is dry-run only',
  backend_disconnected: 'Backend disconnected',
}

export function gateLabel(id: string): string {
  return gateLabels[id] ?? humanizeId(id)
}

export function reasonLabel(id: string): string {
  return reasonLabels[id] ?? humanizeId(id)
}

export function readableReasonText(text: string): string {
  let next = text
  for (const [id, label] of Object.entries(reasonLabels)) {
    next = next.replaceAll(id, label.toLowerCase())
  }
  return next
}

export function dedupe(values: string[]): string[] {
  return Array.from(new Set(values))
}

export function gateGroup(id: string): 'System Gates' | 'Target Gates' | 'Motion Gates' | 'Advisory/Mock Gates' {
  if (id.startsWith('motion_')) return 'Motion Gates'
  if (['body_detected_gate', 'balloon_detected_gate', 'team_classified_gate', 'enemy_target_gate', 'friend_rejection_gate', 'range_valid_gate', 'stable_track_gate', 'forbidden_zone_gate'].includes(id)) return 'Target Gates'
  if (['pico_connected_gate', 'pico_heartbeat_gate', 'vision_running_gate', 'dry_run_gate'].includes(id)) return 'Advisory/Mock Gates'
  return 'System Gates'
}

export function eventSeverity(type: string): 'info' | 'warning' | 'critical' {
  if (type.includes('rejected') || type.includes('fault') || type.includes('error')) return 'critical'
  if (type.includes('warning') || type.includes('timeout') || type.includes('nack')) return 'warning'
  return 'info'
}
