import { defineStore } from 'pinia'
import { ref } from 'vue'
import {
  activateModel,
  exportYolo,
  fetchDatasetExports,
  fetchActiveModels,
  exportDataLabEvidence,
  fetchDataLabAnnotationCandidates,
  fetchDataLabDatasetHealth,
  fetchDataLabReplayStatus,
  fetchDatasetHealth,
  fetchModels,
  fetchDataLabSessions,
  fetchDataLabStatus,
  fetchReplayStatus,
  fetchSessionAnnotations,
  fetchSessions,
  loadReplaySession,
  predictionToAnnotation,
  replayAction,
  recordLatestDataLabSession,
  reviewDataLabAnnotation,
  runDataLabReplay,
  runOpenCvCircleTest,
  runTestInference,
  saveAnnotation,
  setReplaySpeed,
  snapshotSession,
  startSession,
  stopSession,
  uploadModel,
  validateDataset,
  validateModel,
} from '../api/dataLab'
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

const emptyActive: ActiveModels = {
  active_body_model_id: null,
  active_balloon_model_id: null,
  active_combined_model_id: null,
  active_test_adapter: 'opencv-circle-test-adapter',
  updated_at: 0,
}

const emptyReplay: ReplayStatus = {
  state: 'idle',
  session_id: null,
  frame_index: 0,
  frame_count: 0,
  speed: 1,
  source: 'replay',
  current_frame_path: null,
  no_physical_command_generated: true,
  updated_at: 0,
}

export const useDataLabStore = defineStore('dataLab', () => {
  const models = ref<ModelMetadata[]>([])
  const activeModels = ref<ActiveModels>(emptyActive)
  const sessions = ref<SessionRecord[]>([])
  const activeSession = ref<SessionRecord | null>(null)
  const annotations = ref<AnnotationRecord[]>([])
  const replay = ref<ReplayStatus>(emptyReplay)
  const inferenceResult = ref<InferenceResult | null>(null)
  const exportResult = ref<DatasetExportResult | null>(null)
  const exports = ref<Array<{ dataset_id: string; path: string; data_yaml_path: string; image_count: number; label_count: number; no_physical_command_generated: boolean }>>([])
  const validationResult = ref<DatasetValidationResult | null>(null)
  const health = ref<DatasetHealth | null>(null)
  const latestSnapshot = ref<SnapshotResponse | null>(null)
  const dataLabStatus = ref<DataLabStatus | null>(null)
  const dataLabSessions = ref<DataLabSessionSummary[]>([])
  const latestDataLabRecord = ref<DataLabRecordResponse | null>(null)
  const dataLabExport = ref<DataLabExportResponse | null>(null)
  const dataLabReplay = ref<DataLabReplayResult | null>(null)
  const annotationCandidates = ref<DataLabAnnotationCandidate[]>([])
  const dataLabDatasetHealth = ref<DataLabDatasetHealth | null>(null)
  const error = ref<string | null>(null)

  async function refresh(): Promise<void> {
    error.value = null
    try {
      const [modelList, active, sessionList, replayStatus, datasetHealth, exportList, labStatus, labSessions, labReplay, labCandidates, labHealth] = await Promise.all([
        fetchModels(),
        fetchActiveModels(),
        fetchSessions(),
        fetchReplayStatus(),
        fetchDatasetHealth(),
        fetchDatasetExports(),
        fetchDataLabStatus(),
        fetchDataLabSessions(),
        fetchDataLabReplayStatus(),
        fetchDataLabAnnotationCandidates(),
        fetchDataLabDatasetHealth(),
      ])
      models.value = modelList
      activeModels.value = active
      sessions.value = sessionList
      activeSession.value = sessionList.find((session) => session.ended_at === null) ?? null
      replay.value = replayStatus
      health.value = datasetHealth
      exports.value = exportList
      dataLabStatus.value = labStatus
      dataLabSessions.value = labSessions
      dataLabReplay.value = labReplay
      annotationCandidates.value = labCandidates
      dataLabDatasetHealth.value = labHealth
    } catch (caught) {
      error.value = caught instanceof Error ? caught.message : 'Data Lab refresh failed'
    }
  }

  async function createModel(payload: Record<string, unknown>): Promise<void> {
    await uploadModel(payload)
    await refresh()
  }

  async function validateSelectedModel(modelId: string): Promise<void> {
    await validateModel(modelId)
    await refresh()
  }

  async function activateSelectedModel(modelId: string, slot: string): Promise<void> {
    activeModels.value = await activateModel(modelId, slot)
    await refresh()
  }

  async function testModel(modelId: string | null): Promise<void> {
    inferenceResult.value = await runTestInference(modelId)
  }

  async function testCircleAdapter(): Promise<void> {
    inferenceResult.value = await runOpenCvCircleTest()
  }

  async function beginSession(payload: Record<string, unknown>): Promise<void> {
    activeSession.value = await startSession(payload)
    await refresh()
  }

  async function endSession(): Promise<void> {
    activeSession.value = await stopSession()
    await refresh()
  }

  async function takeSnapshot(sessionId: string): Promise<void> {
    latestSnapshot.value = await snapshotSession(sessionId)
    await refresh()
  }

  async function loadAnnotations(sessionId: string): Promise<void> {
    annotations.value = await fetchSessionAnnotations(sessionId)
  }

  async function saveMockAnnotation(sessionId: string, imagePath: string): Promise<void> {
    const annotation = await saveAnnotation({
      session_id: sessionId,
      frame_id: latestSnapshot.value?.frame_id ?? 'frame-manual',
      image_path: imagePath,
      source: 'manual',
      objects: [
        {
          object_id: 'obj-1',
          class_name: 'balloon',
          class_id: 4,
          bbox_format: 'yolo_normalized',
          bbox: [0.5, 0.5, 0.2, 0.2],
          confidence: null,
          is_balloon: true,
          verified_by_operator: true,
        },
      ],
    })
    annotations.value = [annotation, ...annotations.value.filter((item) => item.annotation_id !== annotation.annotation_id)]
    await refresh()
  }

  async function convertPrediction(sessionId: string, imagePath: string): Promise<void> {
    if (!inferenceResult.value) return
    const annotation = await predictionToAnnotation({
      session_id: sessionId,
      frame_id: inferenceResult.value.frame_id,
      image_path: imagePath,
      detections: inferenceResult.value.detections,
    })
    annotations.value = [annotation, ...annotations.value]
    await refresh()
  }

  async function loadReplay(sessionId: string): Promise<void> {
    replay.value = await loadReplaySession(sessionId)
  }

  async function controlReplay(action: 'play' | 'pause' | 'stop' | 'step'): Promise<void> {
    replay.value = await replayAction(action)
  }

  async function changeReplaySpeed(speed: number): Promise<void> {
    replay.value = await setReplaySpeed(speed)
  }

  async function runExport(payload: Record<string, unknown>): Promise<void> {
    exportResult.value = await exportYolo(payload)
    await refresh()
  }

  async function runValidation(payload: Record<string, unknown>): Promise<void> {
    validationResult.value = await validateDataset(payload)
  }

  async function recordLatestEvidence(): Promise<void> {
    latestDataLabRecord.value = await recordLatestDataLabSession()
    await refresh()
  }

  async function exportEvidence(): Promise<void> {
    dataLabExport.value = await exportDataLabEvidence()
    await refresh()
  }

  async function runDataLabReplayFromLatest(): Promise<void> {
    dataLabReplay.value = await runDataLabReplay()
    await refresh()
  }

  async function reviewCandidate(candidateId: string, status: 'accepted' | 'rejected' | 'uncertain' | 'pending'): Promise<void> {
    const reviewed = await reviewDataLabAnnotation(candidateId, status)
    annotationCandidates.value = [reviewed, ...annotationCandidates.value.filter((candidate) => candidate.candidate_id !== candidateId)]
    await refresh()
  }

  function applyEvent(type: string, payload: unknown): void {
    if (type === 'model.test_completed') inferenceResult.value = payload as InferenceResult
    if (type === 'replay.frame' || type.startsWith('replay.')) replay.value = payload as ReplayStatus
    if (type === 'data_lab.replay_completed') dataLabReplay.value = payload as DataLabReplayResult
    if (type === 'data_lab.session_recorded' || type === 'data_lab.export_completed' || type === 'data_lab.annotation_reviewed' || type === 'data_lab.dataset_health_checked') {
      void refresh()
    }
  }

  return {
    models,
    activeModels,
    sessions,
    activeSession,
    annotations,
    replay,
    inferenceResult,
    exportResult,
    exports,
    validationResult,
    health,
    latestSnapshot,
    dataLabStatus,
    dataLabSessions,
    latestDataLabRecord,
    dataLabExport,
    dataLabReplay,
    annotationCandidates,
    dataLabDatasetHealth,
    error,
    refresh,
    createModel,
    validateSelectedModel,
    activateSelectedModel,
    testModel,
    testCircleAdapter,
    beginSession,
    endSession,
    takeSnapshot,
    loadAnnotations,
    saveMockAnnotation,
    convertPrediction,
    loadReplay,
    controlReplay,
    changeReplaySpeed,
    runExport,
    runValidation,
    recordLatestEvidence,
    exportEvidence,
    runDataLabReplayFromLatest,
    reviewCandidate,
    applyEvent,
  }
})
