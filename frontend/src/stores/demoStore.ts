import { defineStore } from 'pinia'
import { ref } from 'vue'
import { fetchDemoReadiness, fetchDemoTimeline, fetchLatestJuryRehearsal, runDemoTimeline, runJuryRehearsal } from '../api/demo'
import type { DemoReadiness, DemoTimeline, JuryRehearsal } from '../types/demo'

const emptyTimeline: DemoTimeline = {
  run_id: 'not_run',
  created_at: 0,
  status: 'not_run',
  events: [],
  verdict: {
    release_demo_ready: false,
    release_demo_warnings: [],
    release_demo_blockers: [],
    competition_ready: false,
    competition_blockers: [],
    dataset_ready_for_training: false,
    dataset_blockers: [],
    reasons: [],
    advisory_only: true,
    no_physical_command_generated: true,
  },
  report_export_id: null,
  advisory_only: true,
  no_physical_command_generated: true,
}

export const useDemoStore = defineStore('demo', () => {
  const timeline = ref<DemoTimeline>(emptyTimeline)
  const readiness = ref<DemoReadiness | null>(null)
  const juryRehearsal = ref<JuryRehearsal | null>(null)
  const isRunning = ref(false)
  const isRunningJury = ref(false)
  const error = ref<string | null>(null)

  async function refresh(): Promise<void> {
    error.value = null
    try {
      const [nextTimeline, nextReadiness, nextJury] = await Promise.all([fetchDemoTimeline(), fetchDemoReadiness(), fetchLatestJuryRehearsal()])
      timeline.value = nextTimeline
      readiness.value = nextReadiness
      juryRehearsal.value = nextJury
    } catch (caught) {
      error.value = caught instanceof Error ? caught.message : 'Demo timeline refresh failed'
    }
  }

  async function runJury(): Promise<void> {
    isRunningJury.value = true
    error.value = null
    try {
      juryRehearsal.value = await runJuryRehearsal()
      await refresh()
    } catch (caught) {
      error.value = caught instanceof Error ? caught.message : 'Jury rehearsal failed'
    } finally {
      isRunningJury.value = false
    }
  }

  async function run(): Promise<void> {
    isRunning.value = true
    error.value = null
    try {
      timeline.value = await runDemoTimeline()
      readiness.value = await fetchDemoReadiness()
    } catch (caught) {
      error.value = caught instanceof Error ? caught.message : 'Demo run failed'
    } finally {
      isRunning.value = false
    }
  }

  function applyEvent(type: string, payload: unknown): void {
    if (type === 'demo.timeline_generated' || type === 'demo.run_completed') {
      timeline.value = payload as DemoTimeline
    }
  }

  return { timeline, readiness, juryRehearsal, isRunning, isRunningJury, error, refresh, run, runJury, applyEvent }
})
