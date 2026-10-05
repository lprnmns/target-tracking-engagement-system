import type { ArmDisarmResult, DecisionState, DecisionStateValue, FireEvaluationResult, SafetyGate } from '../types/decision'

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
  if (!response.ok && response.status !== 403) throw new Error(JSON.stringify(body))
  return body as T
}

export function fetchDecisionState(): Promise<DecisionState> {
  return request<DecisionState>('/api/decision/state')
}

export function armSafety(): Promise<ArmDisarmResult> {
  return request<ArmDisarmResult>('/api/safety/arm', { method: 'POST' })
}

export function disarmSafety(): Promise<ArmDisarmResult> {
  return request<ArmDisarmResult>('/api/safety/disarm', { method: 'POST' })
}

/**
 * Coerce whatever the backend (or a proxy) returned for /fire-request into a
 * fully-populated FireEvaluationResult.  A 403 from FastAPI's own
 * HTTPException, a 5xx HTML page or an empty body has no `blocking_reasons`;
 * reading `.includes/.join` on it used to throw inside the cockpit.
 */
export function normalizeFireEvaluation(raw: unknown): FireEvaluationResult {
  const body = (raw !== null && typeof raw === 'object' ? raw : {}) as Record<string, unknown>
  const detail = typeof body.detail === 'string' ? body.detail : undefined
  return {
    accepted: body.accepted === true,
    dry_run: body.dry_run === true,
    decision_state: (typeof body.decision_state === 'string' ? body.decision_state : 'NO_FIRE') as DecisionStateValue,
    blocking_reasons: Array.isArray(body.blocking_reasons)
      ? body.blocking_reasons.filter((item): item is string => typeof item === 'string')
      : [],
    gates: Array.isArray(body.gates) ? (body.gates as SafetyGate[]) : [],
    reason: typeof body.reason === 'string' && body.reason ? body.reason : (detail ?? 'FIRE isteği reddedildi'),
  }
}

export async function evaluateFireRequest(operatorConfirmed: boolean): Promise<FireEvaluationResult> {
  const response = await fetch(`${apiBaseUrl()}/api/safety/fire-request`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ operator_confirmed: operatorConfirmed }),
  })
  // Never let a non-JSON body (proxy error page, empty 204/502) throw a SyntaxError.
  const body: unknown = await response.json().catch(() => null)
  const result = normalizeFireEvaluation(body)
  // 200 and 403 carry a FireEvaluationResult.  Any other status is a transport
  // or server fault: it must surface as an error, never as an accepted shot.
  if (!response.ok && response.status !== 403) {
    throw new Error(result.reason !== 'FIRE isteği reddedildi' ? result.reason : `FIRE isteği başarısız (HTTP ${response.status})`)
  }
  if (!response.ok) result.accepted = false
  return result
}
