import type { TruthTone } from '../../composables/useRuntimeTruth'

export interface CockpitBadge {
  label: string
  tone: TruthTone
}

export interface CockpitMetric {
  key: string
  label: string
  value: string
  tone: TruthTone
}

export interface CockpitEvent {
  id: string
  title: string
  detail: string
  tone: TruthTone
}

