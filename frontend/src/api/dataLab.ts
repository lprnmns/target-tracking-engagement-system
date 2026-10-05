import type {
  ActiveModels,
  AnnotationRecord,
  DatasetExportResult,
  DatasetHealth,
  DatasetValidationResult,
  DataLabExportResponse,
  DataLabAnnotationCandidate,
  DataLabDatasetHealth,
  DataLabRecordResponse,
  DataLabReplayResult,
  DataLabSessionSummary,
  DataLabStatus,
  InferenceResult,
  ModelMetadata,
  ReplayStatus,
  SessionRecord,
  SnapshotResponse,
} from '../types/dataLab'

function apiBaseUrl(): string {
  const configured = import.meta.env.VITE_BACKEND_API_URL as string | undefined
  if (configured) return configured.replace(/\/$/, '')
  if (window.location.port && window.location.port !== '5173') return window.location.origin
  return `${window.location.protocol}//${window.location.hostname}:8000`
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${apiBaseUrl()}${path}`, {
    headers: {
      'Content-Type': 'application/json',
      ...init?.headers,
    },
    ...init,
  })
  const body = await response.json()
  if (!response.ok) throw new Error(JSON.stringify(body))
  return body as T
}

export function fetchModels(): Promise<ModelMetadata[]> {
  return request<ModelMetadata[]>('/api/models')
}

export function uploadModel(payload: Record<string, unknown>): Promise<ModelMetadata> {
  return request<ModelMetadata>('/api/models/upload', { method: 'POST', body: JSON.stringify(payload) })
}

export function validateModel(modelId: string): Promise<Record<string, unknown>> {
  return request<Record<string, unknown>>(`/api/models/${modelId}/validate`, { method: 'POST' })
}

export function activateModel(modelId: string, slot: string): Promise<ActiveModels> {
  return request<ActiveModels>(`/api/models/${modelId}/activate`, { method: 'POST', body: JSON.stringify({ slot }) })
}

export function fetchActiveModels(): Promise<ActiveModels> {
  return request<ActiveModels>('/api/models/active')
}

export function runTestInference(modelId: string | null): Promise<InferenceResult> {
  return request<InferenceResult>('/api/models/test-inference', {
    method: 'POST',
    body: JSON.stringify({ model_id: modelId, source: 'mock', frame_id: 'ui-test-frame', use_test_adapter: modelId === null }),
  })
}

export function runOpenCvCircleTest(): Promise<InferenceResult> {
  return request<InferenceResult>('/api/models/opencv-circle-test', { method: 'POST', body: JSON.stringify({ source: 'mock' }) })
}

export function fetchSessions(): Promise<SessionRecord[]> {
  return request<SessionRecord[]>('/api/sessions')
}

export function startSession(payload: Record<string, unknown>): Promise<SessionRecord> {
  return request<SessionRecord>('/api/sessions/start', { method: 'POST', body: JSON.stringify(payload) })
}

export function stopSession(): Promise<SessionRecord> {
  return request<SessionRecord>('/api/sessions/stop', { method: 'POST' })
}

export function snapshotSession(sessionId: string): Promise<SnapshotResponse> {
  return request<SnapshotResponse>(`/api/sessions/${sessionId}/snapshot`, { method: 'POST' })
}

export function fetchSessionAnnotations(sessionId: string): Promise<AnnotationRecord[]> {
  return request<AnnotationRecord[]>(`/api/sessions/${sessionId}/annotations`)
}

export function saveAnnotation(payload: Record<string, unknown>): Promise<AnnotationRecord> {
  return request<AnnotationRecord>('/api/annotations', { method: 'POST', body: JSON.stringify(payload) })
}

export function predictionToAnnotation(payload: Record<string, unknown>): Promise<AnnotationRecord> {
  return request<AnnotationRecord>('/api/annotations/from-prediction', { method: 'POST', body: JSON.stringify(payload) })
}

export function fetchReplayStatus(): Promise<ReplayStatus> {
  return request<ReplayStatus>('/api/replay/status')
}

export function loadReplaySession(sessionId: string): Promise<ReplayStatus> {
  return request<ReplayStatus>('/api/replay/load-session', { method: 'POST', body: JSON.stringify({ session_id: sessionId }) })
}

export function replayAction(action: 'play' | 'pause' | 'stop' | 'step'): Promise<ReplayStatus> {
  return request<ReplayStatus>(`/api/replay/${action}`, { method: 'POST' })
}

export function setReplaySpeed(speed: number): Promise<ReplayStatus> {
  return request<ReplayStatus>('/api/replay/speed', { method: 'PUT', body: JSON.stringify({ speed }) })
}

export function exportYolo(payload: Record<string, unknown>): Promise<DatasetExportResult> {
  return request<DatasetExportResult>('/api/datasets/export-yolo', { method: 'POST', body: JSON.stringify(payload) })
}

export function validateDataset(payload: Record<string, unknown>): Promise<DatasetValidationResult> {
  return request<DatasetValidationResult>('/api/datasets/validate', { method: 'POST', body: JSON.stringify(payload) })
}

export function fetchDatasetHealth(): Promise<DatasetHealth> {
  return request<DatasetHealth>('/api/datasets/health')
}

export function fetchDatasetExports(): Promise<Array<{ dataset_id: string; path: string; data_yaml_path: string; image_count: number; label_count: number; no_physical_command_generated: boolean }>> {
  return request<Array<{ dataset_id: string; path: string; data_yaml_path: string; image_count: number; label_count: number; no_physical_command_generated: boolean }>>('/api/datasets/exports')
}

export function fetchDataLabStatus(): Promise<DataLabStatus> {
  return request<DataLabStatus>('/api/data-lab/status')
}

export function fetchDataLabSessions(): Promise<DataLabSessionSummary[]> {
  return request<DataLabSessionSummary[]>('/api/data-lab/sessions')
}

export function fetchLatestDataLabSession(): Promise<DataLabSessionSummary | null> {
  return request<DataLabSessionSummary | null>('/api/data-lab/sessions/latest')
}

export function recordLatestDataLabSession(): Promise<DataLabRecordResponse> {
  return request<DataLabRecordResponse>('/api/data-lab/sessions/record-latest', { method: 'POST' })
}

export function exportDataLabEvidence(): Promise<DataLabExportResponse> {
  return request<DataLabExportResponse>('/api/data-lab/export', { method: 'POST' })
}

export function fetchDataLabReplayStatus(): Promise<DataLabReplayResult> {
  return request<DataLabReplayResult>('/api/data-lab/replay/status')
}

export function runDataLabReplay(): Promise<DataLabReplayResult> {
  return request<DataLabReplayResult>('/api/data-lab/replay/run', { method: 'POST' })
}

export function fetchDataLabReplayLatest(): Promise<DataLabReplayResult> {
  return request<DataLabReplayResult>('/api/data-lab/replay/latest')
}

export function fetchDataLabAnnotationCandidates(): Promise<DataLabAnnotationCandidate[]> {
  return request<DataLabAnnotationCandidate[]>('/api/data-lab/annotations/candidates')
}

export function reviewDataLabAnnotation(candidateId: string, status: 'accepted' | 'rejected' | 'uncertain' | 'pending', reviewerNote?: string): Promise<DataLabAnnotationCandidate> {
  return request<DataLabAnnotationCandidate>('/api/data-lab/annotations/review', {
    method: 'POST',
    body: JSON.stringify({ candidate_id: candidateId, status, reviewer_note: reviewerNote ?? null }),
  })
}

export function fetchDataLabDatasetHealth(): Promise<DataLabDatasetHealth> {
  return request<DataLabDatasetHealth>('/api/data-lab/dataset-health')
}
