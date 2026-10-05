import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { cancelSelfTest, fetchSelfTestRuns, fetchSelfTestStatus, runSelfTest, selfTestReportUrl } from '../api/selfTest'
import type { SelfTestRun, SelfTestStatus } from '../types/selfTest'

export const useSelfTestStore = defineStore('selfTest', () => {
  const status = ref<SelfTestStatus>({ latest_run: null, running: false, runs_count: 0 })
  const runs = ref<SelfTestRun[]>([])
  const error = ref<string | null>(null)
  const isRunning = computed(() => status.value.running || status.value.latest_run?.status === 'running')
  const latestRun = computed(() => status.value.latest_run)
  const progress = computed(() => {
    const run = latestRun.value
    if (!run || run.steps.length === 0) return 0
    const done = run.steps.filter((step) => !['pending', 'running'].includes(step.status)).length
    return Math.round((done / run.steps.length) * 100)
  })
  const categorySummary = computed(() => {
    const summary: Record<string, { passed: number; warning: number; failed: number; skipped: number }> = {}
    for (const step of latestRun.value?.steps ?? []) {
      summary[step.category] ??= { passed: 0, warning: 0, failed: 0, skipped: 0 }
      if (step.status in summary[step.category]) {
        summary[step.category][step.status as 'passed' | 'warning' | 'failed' | 'skipped'] += 1
      }
    }
    return summary
  })

  async function refresh(): Promise<void> {
    error.value = null
    try {
      const [nextStatus, nextRuns] = await Promise.all([fetchSelfTestStatus(), fetchSelfTestRuns()])
      status.value = nextStatus
      runs.value = nextRuns
    } catch (caught) {
      error.value = caught instanceof Error ? caught.message : 'Self-test refresh failed'
    }
  }

  async function run(): Promise<void> {
    error.value = null
    try {
      status.value = { ...status.value, running: true }
      const nextRun = await runSelfTest()
      status.value = { latest_run: nextRun, running: false, runs_count: Math.max(status.value.runs_count, 1) }
      await refresh()
    } catch (caught) {
      error.value = caught instanceof Error ? caught.message : 'Self-test run failed'
      status.value = { ...status.value, running: false }
    }
  }

  async function cancel(): Promise<void> {
    const result = await cancelSelfTest()
    if (result.run) status.value = { ...status.value, latest_run: result.run, running: false }
  }

  function applyEvent(type: string, payload: unknown): void {
    if (type.startsWith('self_test.')) {
      const run = payload as SelfTestRun
      if ('run_id' in run && 'steps' in run) {
        status.value = { latest_run: run, running: run.status === 'running', runs_count: Math.max(status.value.runs_count, 1) }
      }
    }
  }

  function reportUrl(runId: string): string {
    return selfTestReportUrl(runId)
  }

  return {
    status,
    runs,
    error,
    latestRun,
    isRunning,
    progress,
    categorySummary,
    refresh,
    run,
    cancel,
    applyEvent,
    reportUrl,
  }
})
