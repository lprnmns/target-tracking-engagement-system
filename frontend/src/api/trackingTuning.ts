export interface TrackingPreset {
  preset_id: string
  name: string
  algorithm: string
  description: string
  config: Record<string, number | boolean>
}

export interface TrackingTrialResult {
  trial_id: string
  preset_id: string
  preset_name: string
  algorithm: string
  duration_s: number
  samples: number
  target_frames: number
  lost_frames: number
  locked_frames: number
  reacquisitions: number
  reversals: number
  mean_error_px: number
  p95_error_px: number
  loss_ratio: number
  mean_command: number
  technical_score: number
  operator_rating: number | null
  operator_note: string
}

export interface TrackingTuningStatus {
  presets: TrackingPreset[]
  active_trial: null | {
    trial_id: string
    preset_id: string
    preset_name: string
    algorithm: string
    elapsed_s: number
    samples: number
    target_frames: number
    lost_frames: number
    locked_frames: number
    reacquisitions: number
    reversals: number
  }
  results: TrackingTrialResult[]
}

function apiBaseUrl(): string {
  const configured = import.meta.env.VITE_BACKEND_API_URL as string | undefined
  if (configured) return configured.replace(/\/$/, '')
  if (window.location.port && window.location.port !== '5173') return window.location.origin
  return `${window.location.protocol}//${window.location.hostname}:8000`
}

async function request(path: string, init?: RequestInit): Promise<TrackingTuningStatus> {
  const response = await fetch(`${apiBaseUrl()}${path}`, { headers: { 'Content-Type': 'application/json' }, ...init })
  const body = await response.json()
  if (!response.ok) throw new Error(body.detail ?? `HTTP ${response.status}`)
  return body as TrackingTuningStatus
}

export const fetchTrackingTuning = () => request('/api/motion/tracking/tuning')
export const startTrackingTrial = (presetId: string) => request('/api/motion/tracking/tuning/start', { method: 'POST', body: JSON.stringify({ preset_id: presetId }) })
export const stopTrackingTrial = () => request('/api/motion/tracking/tuning/stop', { method: 'POST' })
export const rateTrackingTrial = (trialId: string, rating: number) => request('/api/motion/tracking/tuning/rate', { method: 'POST', body: JSON.stringify({ trial_id: trialId, rating }) })
export const applyTrackingPreset = (presetId: string) => request(`/api/motion/tracking/tuning/apply/${encodeURIComponent(presetId)}`, { method: 'POST' })
