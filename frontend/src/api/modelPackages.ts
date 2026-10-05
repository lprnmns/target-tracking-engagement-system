import type {
  ModelPackageBenchmarkResult,
  ModelPackageRecord,
  ModelPackageTestResult,
  ModelPackageValidationResult,
} from '../types/modelPackage'

function apiBaseUrl(): string {
  const configured = import.meta.env.VITE_BACKEND_API_URL as string | undefined
  if (configured) return configured.replace(/\/$/, '')
  if (window.location.port && window.location.port !== '5173') return window.location.origin
  return `${window.location.protocol}//${window.location.hostname}:8000`
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${apiBaseUrl()}${path}`, {
    headers: { 'Content-Type': 'application/json', ...init?.headers },
    ...init,
  })
  const body = await response.json()
  if (!response.ok) throw new Error(JSON.stringify(body))
  return body as T
}

export const fetchModelPackages = () => request<ModelPackageRecord[]>('/api/models/packages')
export const importModelPackage = (sourcePath: string) => request<ModelPackageRecord>('/api/models/packages/import', { method: 'POST', body: JSON.stringify({ source_path: sourcePath }) })
export const validateModelPackage = (modelId: string) => request<ModelPackageValidationResult>(`/api/models/packages/${modelId}/validate`, { method: 'POST' })
export const activateModelPackage = (modelId: string) => request<Record<string, unknown>>(`/api/models/packages/${modelId}/activate`, { method: 'POST', body: JSON.stringify({ slot: 'combined' }) })
export const deactivateModelPackage = (modelId: string) => request<Record<string, unknown>>(`/api/models/packages/${modelId}/deactivate`, { method: 'POST' })
export const testModelPackage = (modelId: string) => request<ModelPackageTestResult>(`/api/models/packages/${modelId}/test`, { method: 'POST', body: JSON.stringify({ source: 'mock' }) })
export const benchmarkModelPackage = (modelId: string) => request<ModelPackageBenchmarkResult>(`/api/models/packages/${modelId}/benchmark`, { method: 'POST' })
export const applyRecommendedModelSettings = (modelId: string) => request<Record<string, unknown>>(`/api/models/packages/${modelId}/apply-recommended-settings`, { method: 'POST' })
