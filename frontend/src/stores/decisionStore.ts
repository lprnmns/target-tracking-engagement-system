import { defineStore } from 'pinia'
import { ref } from 'vue'
import { armSafety, disarmSafety, evaluateFireRequest, fetchDecisionState } from '../api/decision'
import type { ArmDisarmResult, DecisionState, FireEvaluationResult } from '../types/decision'
import { dedupe } from '../utils/safetyLabels'

const defaultDecision: DecisionState = {
  decision_state: 'NO_TARGET',
  fire_policy: 'NO_FIRE_DEFAULT',
  active_target_id: null,
  logical_target_id: null,
  logical_target_name: null,
  logical_target_state: null,
  logical_balloon_target_id: null,
  selected_body_detection_id: null,
  selected_balloon_detection_id: null,
  target_class: null,
  target_team: 'unknown',
  range_m: null,
  stable_frames: 0,
  required_stable_frames: 5,
  gates: [],
  blocking_reasons: ['backend_disconnected'],
  decision_reason: 'Backend disconnected.',
  updated_at: 0,
  aim_point: null,
  person_safety: null,
}

export const useDecisionStore = defineStore('decision', () => {
  const decision = ref<DecisionState>(defaultDecision)
  const latestFireResult = ref<FireEvaluationResult | null>(null)
  const latestFireResultAt = ref<number | null>(null)
  const latestArmResult = ref<ArmDisarmResult | null>(null)
  const events = ref<Array<{ type: string; summary: string }>>([])
  const error = ref<string | null>(null)

  async function refresh(): Promise<void> {
    try {
      applyDecision(await fetchDecisionState())
    } catch (caught) {
      error.value = caught instanceof Error ? caught.message : 'Decision refresh failed'
    }
  }

  function applyDecision(next: DecisionState): void {
    decision.value = { ...next, blocking_reasons: dedupe(next.blocking_reasons) }
  }

  function addEvent(type: string, payload: unknown): void {
    events.value = [{ type, summary: JSON.stringify(payload).slice(0, 180) }, ...events.value].slice(0, 20)
  }

  async function arm(): Promise<void> {
    latestArmResult.value = await armSafety()
    if (latestArmResult.value.decision) applyDecision(latestArmResult.value.decision)
  }

  async function disarm(): Promise<void> {
    latestArmResult.value = await disarmSafety()
    if (latestArmResult.value.decision) applyDecision(latestArmResult.value.decision)
  }

  async function fireDryRun(): Promise<void> {
    latestFireResult.value = await evaluateFireRequest(true)
    latestFireResultAt.value = Date.now()
  }

  return { decision, latestFireResult, latestFireResultAt, latestArmResult, events, error, refresh, applyDecision, addEvent, arm, disarm, fireDryRun }
})
