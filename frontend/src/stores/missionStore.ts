import { defineStore } from 'pinia'
import { ref } from 'vue'
import { closeStage2Round, closeStage3Round, completeStage2Round, completeStage3Round, configureStage1Plan, fetchMissionStatus, lockStage1Plan, recordStage1Hit, recordStage1WrongTarget, resetMissionStatus, updateMissionStatus } from '../api/mission'
import type { MissionSnapshot, MissionState, MissionUpdate, Stage1RangeScore, Stage1Target, Stage3TargetClass } from '../types/mission'

const defaultState: MissionState = {
  active_stage: 'stage1',
  elapsed_s: 0,
  timer_running: false,
  stage1_hits: 0,
  stage1_wrong_hits: 0,
  stage1_order: ['Balistik Füze', 'Helikopter', 'F16', 'Mini/Micro İHA'],
  stage1_order_locked: false,
  stage1_completed_targets: [],
  stage1_raw_points: 0,
  stage1_penalty_points: 0,
  stage1_events: [],
  stage2_round: 1,
  stage2_hits: 0,
  stage2_completed_rounds: 0,
  stage2_round_events: [],
  stage2_zero_hit_streak: 0,
  stage2_failed: false,
  stage3_round: 1,
  stage3_hits: 0,
  stage3_friend_or_miss_penalties: 0,
  stage3_completed_rounds: 0,
  stage3_round_events: [],
  stage3_miss_streak: 0,
  stage3_failed: false,
  updated_at: 0,
}

const defaultSnapshot: MissionSnapshot = {
  state: defaultState,
  score: {
    stage1_score: 0,
    stage2_score: 0,
    stage3_score: 0,
    active_score: 0,
    remaining_s: 300,
    total_estimated_score: 0,
    stage1_raw_points: 0,
    stage1_penalty_points: 0,
    stage1_bonus_points: 0,
    stage1_next_target: 'Balistik Füze',
    stage1_plan_locked: false,
    stage2_round_scores: [],
    stage2_zero_hit_streak: 0,
    stage2_failed: false,
    stage2_passing_threshold_met: false,
    stage3_round_scores: [],
    stage3_miss_streak: 0,
    stage3_failed: false,
    stage3_award_threshold_met: false,
  },
  no_physical_command_generated: true,
}

export const useMissionStore = defineStore('mission', () => {
  const snapshot = ref<MissionSnapshot>(defaultSnapshot)
  const error = ref<string | null>(null)

  async function refresh(): Promise<void> {
    try {
      snapshot.value = await fetchMissionStatus()
      error.value = null
    } catch (caught) {
      error.value = caught instanceof Error ? caught.message : 'Mission refresh failed'
    }
  }

  async function update(update: MissionUpdate): Promise<void> {
    snapshot.value = await updateMissionStatus(update)
  }

  async function reset(): Promise<void> {
    snapshot.value = await resetMissionStatus()
  }

  async function setStage1Plan(order: Stage1Target[]): Promise<void> {
    snapshot.value = await configureStage1Plan(order)
  }

  async function lockStage1(): Promise<void> {
    snapshot.value = await lockStage1Plan()
  }

  async function recordStage1CorrectHit(target: Stage1Target, score: Stage1RangeScore): Promise<void> {
    snapshot.value = await recordStage1Hit(target, score)
  }

  async function recordStage1Wrong(target: Stage1Target): Promise<void> {
    snapshot.value = await recordStage1WrongTarget(target)
  }

  async function completeStage2(confirmedHits: number): Promise<void> {
    snapshot.value = await completeStage2Round(confirmedHits)
  }

  async function closeStage2(): Promise<void> {
    snapshot.value = await closeStage2Round()
  }

  async function completeStage3(payload: { enemy_class: Stage3TargetClass; enemy_hit: boolean; friend_hit: boolean }): Promise<void> {
    snapshot.value = await completeStage3Round(payload)
  }

  async function closeStage3(): Promise<void> {
    snapshot.value = await closeStage3Round()
  }

  function applySnapshot(next: MissionSnapshot): void {
    snapshot.value = next
  }

  return { snapshot, error, refresh, update, reset, setStage1Plan, lockStage1, recordStage1CorrectHit, recordStage1Wrong, completeStage2, closeStage2, completeStage3, closeStage3, applySnapshot }
})
