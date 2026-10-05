import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { fetchFirstRunStatus, markFirstRunComplete, resetFirstRun, runFirstRunCheck } from '../api/firstRun'
import type { FirstRunReport, FirstRunStatus, ReadinessProfileName } from '../types/firstRun'

export const useFirstRunStore = defineStore('firstRun', () => {
  const status = ref<FirstRunStatus>({
    completed: false,
    latest_report: null,
    mode: 'development',
    checks_count: 0,
    current_first_run_status: 'open',
    current_profile_id: 'release_candidate_ready',
    current_profile_evaluation_status: 'not_evaluated',
    last_successful_first_run: null,
    stale_evidence: false,
    no_physical_command_generated: true,
  })
  const latestReport = ref<FirstRunReport | null>(null)
  const selectedProfile = ref<ReadinessProfileName>('release_candidate_ready')
  const isChecking = ref(false)
  const error = ref<string | null>(null)

  const currentReport = computed(() => status.value.current_profile_evaluation_status === 'not_evaluated' ? null : latestReport.value)
  const passedCount = computed(() => currentReport.value?.steps.filter((step) => step.status === 'passed').length ?? 0)
  const warningCount = computed(() => currentReport.value?.steps.filter((step) => step.status === 'warning').length ?? 0)
  const failedCount = computed(() => currentReport.value?.steps.filter((step) => step.status === 'failed').length ?? 0)
  const displayStatus = computed<'OPEN' | 'PASSED' | 'WARNING' | 'FAILED'>(() => status.value.current_first_run_status.toUpperCase() as 'OPEN' | 'PASSED' | 'WARNING' | 'FAILED')
  const displayBadge = computed(() => `FIRST RUN: ${displayStatus.value}`)
  const currentProfileId = computed(() => status.value.current_profile_id)
  const currentProfileEvaluationStatus = computed(() => status.value.current_profile_evaluation_status)
  const currentProfileEvaluationBadge = computed(() => `PROFILE EVAL: ${currentProfileEvaluationStatus.value.replace('_', ' ').toUpperCase()}`)

  async function refresh(): Promise<void> {
    error.value = null
    try {
      status.value = await fetchFirstRunStatus()
      latestReport.value = status.value.latest_report
      selectedProfile.value = status.value.current_profile_id
    } catch (caught) {
      error.value = caught instanceof Error ? caught.message : 'First-run refresh failed'
    }
  }

  async function check(): Promise<void> {
    error.value = null
    isChecking.value = true
    try {
      latestReport.value = await runFirstRunCheck()
      status.value = { ...status.value, latest_report: latestReport.value, checks_count: latestReport.value.steps.length }
      await refresh()
    } catch (caught) {
      error.value = caught instanceof Error ? caught.message : 'First-run check failed'
    } finally {
      isChecking.value = false
    }
  }

  async function complete(): Promise<void> {
    const result = await markFirstRunComplete()
    status.value = result.status
    latestReport.value = result.status.latest_report
  }

  async function reset(): Promise<void> {
    const result = await resetFirstRun()
    status.value = result.status
    latestReport.value = null
  }

  return {
    status,
    latestReport,
    currentReport,
    selectedProfile,
    currentProfileId,
    currentProfileEvaluationStatus,
    currentProfileEvaluationBadge,
    displayStatus,
    displayBadge,
    isChecking,
    error,
    passedCount,
    warningCount,
    failedCount,
    refresh,
    check,
    complete,
    reset,
  }
})
