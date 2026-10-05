import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { fetchDigitalTwinAssets, fetchDigitalTwinState, fetchLatestDigitalTwinReplay, generateDigitalTwinReplay, logDigitalTwinPanelRendered } from '../api/digitalTwin'
import { fetchEngagementDigitalTwinReplay, fetchEngagementEvidenceRecords, fetchEngagementEvidenceStatus } from '../api/engagementEvidence'
import type { DigitalTwinAssetsResponse, DigitalTwinReplayGenerateResult, DigitalTwinReplaySummary, DigitalTwinState } from '../types/digitalTwin'
import type { EngagementEvidenceStatus, EngagementEvidenceSummary } from '../types/engagementEvidence'

export const useDigitalTwinStore = defineStore('digitalTwin', () => {
  const state = ref<DigitalTwinState | null>(null)
  const assets = ref<DigitalTwinAssetsResponse | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)
  const lastUpdatedAt = ref<number | null>(null)
  const lastReplay = ref<DigitalTwinReplayGenerateResult | null>(null)
  const replay = ref<DigitalTwinReplaySummary | null>(null)
  const engagementEvidence = ref<EngagementEvidenceStatus | null>(null)
  const engagementRecords = ref<EngagementEvidenceSummary[]>([])
  let poseRefreshPromise: Promise<void> | null = null
  let evidenceStatusPromise: Promise<void> | null = null
  let evidenceRecordsPromise: Promise<void> | null = null
  let evidenceRecordsFetchedAt = 0
  // /records lists persisted engagements; it is a history view, not a live
  // signal, so it is coalesced and rate-limited on the client as well.
  const EVIDENCE_RECORDS_MIN_INTERVAL_MS = 5000

  const readOnlyHealthy = computed(() => Boolean(
    state.value?.no_physical_command_generated
      && state.value.safety.digital_twin_read_only
      && !state.value.safety.digital_twin_command_authority,
  ))

  function refreshPose(): Promise<void> {
    // Pose is latency-sensitive. Never make it wait for assets or evidence and
    // never build up a queue when the backend/browser is briefly busy.
    if (poseRefreshPromise) return poseRefreshPromise
    poseRefreshPromise = fetchDigitalTwinState()
      .then((nextState) => {
        state.value = nextState
        lastUpdatedAt.value = Date.now()
        error.value = null
      })
      .catch((exc) => {
        error.value = exc instanceof Error ? exc.message : String(exc)
      })
      .finally(() => {
        poseRefreshPromise = null
      })
    return poseRefreshPromise
  }

  function refreshEngagementStatus(): Promise<void> {
    if (evidenceStatusPromise) return evidenceStatusPromise
    evidenceStatusPromise = fetchEngagementEvidenceStatus()
      .then((nextEvidence) => { engagementEvidence.value = nextEvidence })
      .finally(() => { evidenceStatusPromise = null })
    return evidenceStatusPromise
  }

  function refreshEngagementRecords(force = false): Promise<void> {
    if (evidenceRecordsPromise) return evidenceRecordsPromise
    if (!force && Date.now() - evidenceRecordsFetchedAt < EVIDENCE_RECORDS_MIN_INTERVAL_MS) return Promise.resolve()
    evidenceRecordsPromise = fetchEngagementEvidenceRecords()
      .then((nextEvidenceRecords) => {
        engagementRecords.value = nextEvidenceRecords.records
        evidenceRecordsFetchedAt = Date.now()
      })
      .finally(() => { evidenceRecordsPromise = null })
    return evidenceRecordsPromise
  }

  async function refreshEngagementEvidence(): Promise<void> {
    await Promise.all([refreshEngagementStatus(), refreshEngagementRecords()])
  }

  async function refresh(): Promise<void> {
    loading.value = true
    const posePromise = refreshPose()
    const metadataPromise = Promise.all([
      assets.value ? Promise.resolve(assets.value) : fetchDigitalTwinAssets(),
      refreshEngagementEvidence(),
    ]).then(([nextAssets]) => {
      assets.value = nextAssets
    })
    try {
      await Promise.all([posePromise, metadataPromise])
      error.value = null
    } catch (exc) {
      error.value = exc instanceof Error ? exc.message : String(exc)
    } finally {
      loading.value = false
    }
  }

  async function generateReplay(): Promise<void> {
    try {
      lastReplay.value = await generateDigitalTwinReplay()
      error.value = null
    } catch (exc) {
      error.value = exc instanceof Error ? exc.message : String(exc)
    }
  }

  async function loadReplay(): Promise<void> {
    try {
      replay.value = await fetchLatestDigitalTwinReplay()
      error.value = null
    } catch (exc) {
      error.value = exc instanceof Error ? exc.message : String(exc)
    }
  }

  async function loadEngagementReplay(engagementId: string): Promise<void> {
    try {
      replay.value = await fetchEngagementDigitalTwinReplay(engagementId)
      error.value = null
    } catch (exc) {
      error.value = exc instanceof Error ? exc.message : String(exc)
    }
  }

  async function panelRendered(): Promise<void> {
    try {
      await logDigitalTwinPanelRendered()
    } catch {
      // Rendering evidence logging must never affect cockpit behavior.
    }
  }

  return {
    assets,
    error,
    engagementEvidence,
    engagementRecords,
    generateReplay,
    lastReplay,
    lastUpdatedAt,
    loadReplay,
    loadEngagementReplay,
    loading,
    panelRendered,
    readOnlyHealthy,
    refresh,
    refreshEngagementEvidence,
    refreshEngagementRecords,
    refreshEngagementStatus,
    refreshPose,
    replay,
    state,
  }
})
