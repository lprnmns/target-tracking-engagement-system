export interface BallisticsStation {
  distance_m: number
  offset_y_px: number
  offset_x_px: number
  flight_time_ms: number
  reference_balloon_w_px: number
  notes: string
}

export interface BallisticsProfile {
  enabled: boolean
  start_distance_m: number
  approach_speed_m_s: number
  enable_hybrid_vision: boolean
  stations: Record<string, BallisticsStation>
  updated_at: number
}

export interface BallisticsLiveState {
  active: boolean
  elapsed_s: number
  estimated_distance_m: number
  time_distance_m: number
  vision_distance_m: number | null
  offset_x_px: number
  offset_y_px: number
  lead_x_px: number
  total_offset_x_px: number
  total_offset_y_px: number
  flight_time_ms: number
  station_nearest: number
  updated_at: number
}

export interface StationUpdateRequest {
  distance_m: number
  offset_x_px: number
  offset_y_px: number
  flight_time_ms?: number
  reference_balloon_w_px?: number
  notes?: string
}

export interface Stage3SimulationRequest {
  start_distance_m?: number
  approach_speed_m_s?: number
  enable_hybrid_vision?: boolean
}
