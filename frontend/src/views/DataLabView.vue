<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import DashboardCard from '../components/dashboard/DashboardCard.vue'
import MetricRow from '../components/dashboard/MetricRow.vue'
import StatusBadge from '../components/shared/StatusBadge.vue'
import { useDataLabStore } from '../stores/dataLabStore'
import { useDemoStore } from '../stores/demoStore'
import { useVisionStore } from '../stores/visionStore'

const dataLab = useDataLabStore()
const demo = useDemoStore()
const vision = useVisionStore()
const route = useRoute()
const tabs = ['Models', 'Capture', 'Sessions', 'Replay', 'Annotation Review', 'YOLO Export', 'Dataset Health', 'Demo Timeline']
const tabAliases: Record<string, string> = {
  models: 'Models',
  capture: 'Capture',
  sessions: 'Sessions',
  replay: 'Replay',
  annotation: 'Annotation Review',
  annotations: 'Annotation Review',
  export: 'YOLO Export',
  yolo: 'YOLO Export',
  health: 'Dataset Health',
  demo: 'Demo Timeline',
  timeline: 'Demo Timeline',
}
const initialTab = typeof route.query.tab === 'string' ? tabAliases[route.query.tab] ?? route.query.tab : 'Models'
const activeTab = ref(tabs.includes(initialTab) ? initialTab : 'Models')
const selectedModelId = ref<string | null>(null)
const selectedSessionId = ref<string | null>(null)
const selectedFileName = ref('model_file.pt')

const modelForm = reactive({
  name: 'Vision Team Model',
  version: '0.1.0',
  model_type: 'combined_detector',
  framework: 'ultralytics',
  input_size: 960,
  class_names: 'f16,helicopter,ballistic_missile,mini_micro_uav,balloon',
  confidence_threshold: 0.35,
  iou_threshold: 0.5,
  provided_by: 'vision_team',
  notes: 'Awaiting production adapter details from vision team.',
})

const sessionForm = reactive({
  name: 'field_capture',
  operator: 'operator',
  target_type: 'unknown',
  team: 'unknown',
  distance_m: '10',
  lane: 'center',
  angle: 'front',
  lens_profile: '8mm',
  lighting: 'indoor_led',
  notes: '',
})

const exportForm = reactive({
  dataset_name: 'istiklal_dataset',
  version: 'v1',
  export_mode: 'combined_body_balloon',
  train_val_split: 0.8,
  include_unverified_annotations: false,
  include_model_predictions: false,
})

const selectedModel = computed(() => dataLab.models.find((model) => model.model_id === selectedModelId.value) ?? dataLab.models[0] ?? null)
const selectedSession = computed(() => dataLab.sessions.find((session) => session.session_id === selectedSessionId.value) ?? dataLab.activeSession ?? dataLab.sessions[0] ?? null)
const activeModelSummary = computed(() => [
  dataLab.activeModels.active_body_model_id,
  dataLab.activeModels.active_balloon_model_id,
  dataLab.activeModels.active_combined_model_id,
  dataLab.activeModels.active_test_adapter,
].filter(Boolean).join(', ') || 'none')
const latestExport = computed(() => dataLab.exportResult ?? (dataLab.exports[0] ? {
  dataset_id: dataLab.exports[0].dataset_id,
  output_path: dataLab.exports[0].path,
  data_yaml_path: dataLab.exports[0].data_yaml_path,
  image_count: dataLab.exports[0].image_count,
  label_count: dataLab.exports[0].label_count,
  train_count: 0,
  val_count: 0,
  warnings: [],
  no_physical_command_generated: true,
} : null))
const latestLabSession = computed(() => dataLab.dataLabSessions[0] ?? null)
const latestLabDetection = computed(() => dataLab.dataLabStatus?.latest_detection ?? latestLabSession.value?.latest_detection ?? null)
const latestFiveLabSessions = computed(() => dataLab.dataLabSessions.slice(0, 5))
const pendingCandidates = computed(() => dataLab.annotationCandidates.filter((candidate) => candidate.review_status === 'pending'))
const acceptedCandidates = computed(() => dataLab.annotationCandidates.filter((candidate) => candidate.review_status === 'accepted'))
const rejectedCandidates = computed(() => dataLab.annotationCandidates.filter((candidate) => candidate.review_status === 'rejected'))
const yoloYamlPreview = computed(() => `path: ${exportForm.dataset_name}-${exportForm.version}
train: images/train
val: images/val
names:
  0: f16
  1: helicopter
  2: ballistic_missile
  3: mini_micro_uav
  4: balloon`)

function onFileChange(event: Event): void {
  const input = event.target as HTMLInputElement
  selectedFileName.value = input.files?.[0]?.name ?? selectedFileName.value
}

async function uploadModel(): Promise<void> {
  await dataLab.createModel({
    ...modelForm,
    file_name: selectedFileName.value,
    file_size_bytes: 0,
    class_names: modelForm.class_names.split(',').map((item) => item.trim()).filter(Boolean),
  })
  selectedModelId.value = dataLab.models[0]?.model_id ?? null
}

async function startCapture(): Promise<void> {
  await dataLab.beginSession({
    name: sessionForm.name,
    operator: sessionForm.operator,
    mode: 'capture',
    scenario: {
      target_type: sessionForm.target_type,
      team: sessionForm.team,
      distance_m: sessionForm.distance_m,
      lane: sessionForm.lane,
      angle: sessionForm.angle,
      lens_profile: sessionForm.lens_profile,
      lighting: sessionForm.lighting,
      camera_resolution: '640x360',
      yolo_imgsz: 960,
      active_model_ids: [
        dataLab.activeModels.active_body_model_id,
        dataLab.activeModels.active_balloon_model_id,
        dataLab.activeModels.active_combined_model_id,
      ].filter(Boolean),
      notes: sessionForm.notes,
    },
  })
  selectedSessionId.value = dataLab.activeSession?.session_id ?? null
}

async function snapshot(): Promise<void> {
  if (selectedSession.value) await dataLab.takeSnapshot(selectedSession.value.session_id)
}

async function saveAnnotation(): Promise<void> {
  if (!selectedSession.value) return
  await dataLab.saveMockAnnotation(
    selectedSession.value.session_id,
    dataLab.latestSnapshot?.image_path ?? `${selectedSession.value.session_id}/snapshots/frame-manual.jpg`,
  )
}

async function convertPrediction(): Promise<void> {
  if (!selectedSession.value) return
  await dataLab.convertPrediction(
    selectedSession.value.session_id,
    dataLab.latestSnapshot?.image_path ?? `${selectedSession.value.session_id}/snapshots/frame-prediction.jpg`,
  )
}

async function runExport(): Promise<void> {
  await dataLab.runExport({
    ...exportForm,
    selected_sessions: selectedSession.value ? [selectedSession.value.session_id] : [],
  })
}

async function runValidation(): Promise<void> {
  await dataLab.runValidation({
    ...exportForm,
    selected_sessions: selectedSession.value ? [selectedSession.value.session_id] : [],
  })
}

onMounted(async () => {
  await dataLab.refresh()
  await demo.refresh()
  await vision.refreshLegacyEvidence()
  selectedModelId.value = dataLab.models[0]?.model_id ?? null
  selectedSessionId.value = typeof route.query.session === 'string'
    ? route.query.session
    : dataLab.sessions[0]?.session_id ?? null
  if (activeTab.value === 'Annotation Review' && selectedSession.value) {
    await dataLab.loadAnnotations(selectedSession.value.session_id)
  }
})

watch(activeTab, async (tab) => {
  if (tab === 'Annotation Review' && selectedSession.value) {
    await dataLab.loadAnnotations(selectedSession.value.session_id)
  }
})
</script>

<template>
  <div class="grid gap-4">
    <div class="rounded-md border border-amber-400/30 bg-amber-400/10 px-4 py-3 text-sm text-amber-100">
      Model, dataset, session evidence and replay outputs are advisory data tools only. No physical command is generated.
    </div>

    <section class="grid gap-4 xl:grid-cols-[1fr_1fr_1.1fr]">
      <DashboardCard title="Data Lab Foundation" subtitle="Session-level evidence from mock/surrogate vision metadata">
        <div class="mb-3 flex flex-wrap gap-2">
          <StatusBadge label="ADVISORY ONLY" tone="warn" />
          <StatusBadge label="NO PHYSICAL COMMAND" tone="bad" />
        </div>
        <MetricRow label="Sessions" :value="dataLab.dataLabStatus?.sessions_count ?? dataLab.sessions.length" />
        <MetricRow label="Latest session" :value="dataLab.dataLabStatus?.latest_session_id ?? 'none'" />
        <MetricRow label="Replay readiness" :value="dataLab.dataLabStatus?.replay_status ?? 'replay_execution_not_implemented'" />
        <MetricRow label="Export root" :value="dataLab.dataLabStatus?.export_root ?? 'exports/data_lab'" />
        <div class="mt-3 flex flex-wrap gap-2">
          <button class="rounded-md bg-cyan-500 px-3 py-2 text-sm font-semibold text-slate-950" @click="dataLab.recordLatestEvidence">Record latest vision evidence</button>
          <button class="rounded-md bg-emerald-500 px-3 py-2 text-sm font-semibold text-slate-950" @click="dataLab.exportEvidence">Export Data Lab evidence</button>
        </div>
      </DashboardCard>

      <DashboardCard title="Latest Detection Evidence" subtitle="Mock/surrogate metadata persisted to JSONL">
        <MetricRow label="Source" :value="latestLabDetection?.source ?? 'not_recorded'" />
        <MetricRow label="Camera source" :value="latestLabDetection?.camera_source_kind ?? 'not_available'" />
        <MetricRow label="Frame origin" :value="latestLabDetection?.frame_origin ?? 'not_available'" />
        <MetricRow label="Detector" :value="latestLabDetection?.detector_kind ?? 'not_available'" />
        <MetricRow label="Bodies / circles" :value="`${latestLabDetection?.body_count ?? 0} / ${latestLabDetection?.balloon_count ?? 0}`" />
        <MetricRow label="Detector FPS" :value="latestLabDetection?.detector_fps ?? 'not_measured'" />
        <p class="mt-3 text-xs text-slate-400">
          Mock camera evidence is synthetic release/demo evidence. Real camera evidence must show real_capture and real_camera.
        </p>
      </DashboardCard>

      <DashboardCard title="Evidence Export" subtitle="Reports/KTR files produced by Data Lab">
        <MetricRow label="Latest export" :value="dataLab.dataLabExport?.export_id ?? 'not_exported'" />
        <MetricRow label="Timestamp" :value="dataLab.dataLabExport?.created_at ? new Date(dataLab.dataLabExport.created_at * 1000).toLocaleString() : 'not_exported'" />
        <MetricRow label="Output" :value="dataLab.dataLabExport?.output_dir ?? 'not_exported'" />
        <MetricRow label="Sessions exported" :value="dataLab.dataLabExport?.sessions_count ?? 0" />
        <MetricRow label="Detection JSONL events" :value="dataLab.dataLabExport?.detection_events_count ?? 0" />
        <MetricRow label="Physical command" :value="dataLab.dataLabExport?.no_physical_command_generated === false ? 'UNSAFE' : 'NO'" />
        <div v-if="dataLab.dataLabExport" class="mt-3 rounded-md border border-white/10 bg-black/20 p-3 text-xs text-slate-300">
          <p v-for="file in dataLab.dataLabExport.files" :key="file" class="break-all font-mono">{{ file }}</p>
        </div>
      </DashboardCard>

      <DashboardCard title="Real Camera Evidence" subtitle="Legacy perception evidence-only capture">
        <MetricRow label="Status" :value="vision.realCameraEvidenceStatus.status" />
        <MetricRow label="Camera source" :value="vision.realCameraEvidenceStatus.camera_source" />
        <MetricRow label="Frame origin" :value="vision.realCameraEvidenceStatus.frame_origin" />
        <MetricRow label="Latest evidence" :value="vision.realCameraEvidenceStatus.latest_evidence_id ?? 'not_recorded'" />
        <MetricRow label="Detection count" :value="vision.realCameraEvidenceStatus.detections_count" />
        <MetricRow label="no_physical_command_generated" :value="vision.realCameraEvidenceStatus.no_physical_command_generated" />
        <p class="mt-3 break-words text-xs text-slate-400">
          Real camera evidence does not use mock fallback. If no real camera is configured, this panel records not_available evidence.
        </p>
      </DashboardCard>

      <DashboardCard title="Camera Host Diagnostic Evidence" subtitle="Host OS camera discovery boundary">
        <MetricRow label="Acceptance status" :value="vision.cameraHostDiagnostic.camera_acceptance_status" />
        <MetricRow label="Host devices detected" :value="vision.cameraHostDiagnostic.host_camera_devices_detected" />
        <MetricRow label="/dev/video entries" :value="vision.cameraHostDiagnostic.dev_video_entries.length ? vision.cameraHostDiagnostic.dev_video_entries.join(', ') : 'none'" />
        <MetricRow label="v4l2 available" :value="vision.cameraHostDiagnostic.v4l2_available" />
        <MetricRow label="Blocker reason" :value="vision.cameraHostDiagnostic.blocker_reason" />
        <MetricRow label="no_physical_command_generated" :value="vision.cameraHostDiagnostic.no_physical_command_generated" />
        <p class="mt-3 break-words text-xs text-slate-400">
          Camera host diagnostics are read-only. Mock/surrogate evidence does not count as real camera acceptance.
        </p>
      </DashboardCard>
    </section>

    <DashboardCard title="Recent Session Evidence" subtitle="Last 5 Data Lab sessions">
      <div class="overflow-x-auto">
        <table class="w-full min-w-[1080px] table-fixed text-left text-sm">
          <colgroup>
            <col class="w-[230px]" />
            <col class="w-[190px]" />
            <col class="w-[150px]" />
            <col class="w-[170px]" />
            <col class="w-[120px]" />
            <col class="w-[110px]" />
            <col class="w-[170px]" />
            <col class="w-[120px]" />
            <col class="w-[160px]" />
          </colgroup>
          <thead class="text-xs uppercase tracking-[0.14em] text-slate-500">
            <tr>
              <th class="py-2 pr-3">Session ID</th>
              <th class="pr-3">Source</th>
              <th class="pr-3">Frame origin</th>
              <th class="pr-3">Detector</th>
              <th class="pr-3">Bodies / circles</th>
              <th class="pr-3">Detector FPS</th>
              <th class="pr-3">Created</th>
              <th class="pr-3">Advisory</th>
              <th>No physical command</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="session in latestFiveLabSessions" :key="session.session_id" class="border-t border-white/8">
              <td class="truncate py-2 pr-3 font-mono text-xs text-cyan-200" :title="session.session_id">{{ session.session_id }}</td>
              <td class="truncate pr-3" :title="session.latest_detection?.source ?? 'not_recorded'">{{ session.latest_detection?.source ?? 'not_recorded' }}</td>
              <td class="truncate pr-3">{{ session.latest_detection?.frame_origin ?? 'not_available' }}</td>
              <td class="truncate pr-3">{{ session.latest_detection?.detector_kind ?? 'not_available' }}</td>
              <td class="pr-3">{{ session.latest_detection?.body_count ?? 0 }} / {{ session.latest_detection?.balloon_count ?? 0 }}</td>
              <td class="pr-3">{{ session.latest_detection?.detector_fps ?? 'n/a' }}</td>
              <td class="truncate pr-3">{{ new Date(session.created_at * 1000).toLocaleString() }}</td>
              <td class="pr-3"><StatusBadge :label="session.advisory_only ? 'true' : 'false'" :tone="session.advisory_only ? 'warn' : 'bad'" /></td>
              <td><StatusBadge :label="session.no_physical_command_generated ? 'true' : 'false'" :tone="session.no_physical_command_generated ? 'good' : 'bad'" /></td>
            </tr>
          </tbody>
        </table>
        <p v-if="latestFiveLabSessions.length === 0" class="px-3 py-6 text-sm text-slate-400">No Data Lab session evidence recorded yet.</p>
      </div>
    </DashboardCard>

    <div class="flex flex-wrap gap-2">
      <button
        v-for="tab in tabs"
        :key="tab"
        class="focus-ring rounded-md px-3 py-2 text-sm font-semibold"
        :class="activeTab === tab ? 'bg-cyan-400 text-slate-950' : 'bg-white/8 text-slate-200 hover:bg-white/12'"
        @click="activeTab = tab"
      >
        {{ tab }}
      </button>
    </div>

    <p v-if="dataLab.error" class="rounded-md border border-red-400/30 bg-red-500/10 px-4 py-3 text-sm text-red-100">
      {{ dataLab.error }}
    </p>

    <section v-if="activeTab === 'Models'" class="grid gap-4 xl:grid-cols-[0.9fr_1.1fr]">
      <DashboardCard title="Model Upload" subtitle=".pt / .onnx / .yaml metadata registration">
        <div class="grid gap-3">
          <input v-model="modelForm.name" class="rounded-md border border-white/10 bg-black/20 px-3 py-2 text-sm" placeholder="Model name" />
          <div class="grid gap-3 md:grid-cols-2">
            <input v-model="modelForm.version" class="rounded-md border border-white/10 bg-black/20 px-3 py-2 text-sm" placeholder="Version" />
            <input type="file" accept=".pt,.onnx,.yaml" class="rounded-md border border-white/10 bg-black/20 px-3 py-2 text-sm" @change="onFileChange" />
          </div>
          <div class="grid gap-3 md:grid-cols-2">
            <select v-model="modelForm.model_type" class="rounded-md border border-white/10 bg-black/20 px-3 py-2 text-sm">
              <option value="body_detector">body_detector</option>
              <option value="balloon_detector">balloon_detector</option>
              <option value="combined_detector">combined_detector</option>
              <option value="color_classifier_adapter">color_classifier_adapter</option>
              <option value="test_stub">test_stub</option>
            </select>
            <select v-model="modelForm.framework" class="rounded-md border border-white/10 bg-black/20 px-3 py-2 text-sm">
              <option value="ultralytics">ultralytics</option>
              <option value="onnx">onnx</option>
              <option value="opencv_stub">opencv_stub</option>
              <option value="external_adapter">external_adapter</option>
            </select>
          </div>
          <textarea v-model="modelForm.class_names" class="min-h-20 rounded-md border border-white/10 bg-black/20 px-3 py-2 text-sm" />
          <div class="grid gap-3 md:grid-cols-3">
            <input v-model.number="modelForm.input_size" type="number" class="rounded-md border border-white/10 bg-black/20 px-3 py-2 text-sm" />
            <input v-model.number="modelForm.confidence_threshold" type="number" step="0.01" class="rounded-md border border-white/10 bg-black/20 px-3 py-2 text-sm" />
            <input v-model.number="modelForm.iou_threshold" type="number" step="0.01" class="rounded-md border border-white/10 bg-black/20 px-3 py-2 text-sm" />
          </div>
          <textarea v-model="modelForm.notes" class="min-h-20 rounded-md border border-white/10 bg-black/20 px-3 py-2 text-sm" />
          <button class="focus-ring rounded-md bg-cyan-500 px-3 py-2 text-sm font-semibold text-slate-950" @click="uploadModel">Register model metadata</button>
        </div>
      </DashboardCard>

      <DashboardCard title="Model Registry" subtitle="Vision team delivery surface">
        <div class="mb-3 flex flex-wrap gap-2">
          <StatusBadge label="OpenCV daire algılayıcı yalnızca test adaptörüdür" tone="warn" />
          <StatusBadge label="NO PHYSICAL COMMAND" tone="bad" />
        </div>
        <div class="overflow-x-auto">
          <table class="w-full min-w-[780px] text-left text-sm">
            <thead class="text-xs uppercase tracking-[0.14em] text-slate-500">
              <tr><th class="py-2">Name</th><th>Type</th><th>Framework</th><th>Status</th><th>File</th><th>Warnings</th><th>Actions</th></tr>
            </thead>
            <tbody>
              <tr v-for="model in dataLab.models" :key="model.model_id" class="border-t border-white/8">
                <td class="py-2">
                  <button class="text-left text-cyan-200" @click="selectedModelId = model.model_id">{{ model.name }} v{{ model.version }}</button>
                  <p class="font-mono text-xs text-slate-500">{{ model.model_id }}</p>
                </td>
                <td>{{ model.model_type }}</td>
                <td>{{ model.framework }}</td>
                <td><StatusBadge :label="model.status" :tone="model.status === 'active' || model.status === 'validated' ? 'good' : 'warn'" /></td>
                <td class="font-mono text-xs">{{ model.file_name ?? 'adapter' }}</td>
                <td class="max-w-[220px] text-xs text-amber-100">{{ model.warnings.join(', ') || 'none' }}</td>
                <td>
                  <div class="flex flex-wrap gap-1">
                    <button class="rounded bg-white/10 px-2 py-1 text-xs" @click="dataLab.validateSelectedModel(model.model_id)">Validate</button>
                    <button class="rounded bg-cyan-500 px-2 py-1 text-xs font-semibold text-slate-950" @click="dataLab.activateSelectedModel(model.model_id, model.model_type === 'test_stub' ? 'test_adapter' : model.model_type.startsWith('body') ? 'body' : model.model_type.startsWith('balloon') ? 'balloon' : 'combined')">Activate</button>
                    <button class="rounded bg-emerald-500 px-2 py-1 text-xs font-semibold text-slate-950" @click="dataLab.testModel(model.model_id)">Test</button>
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </DashboardCard>

      <DashboardCard title="Active Model Selection" subtitle="Separate detector slots">
        <MetricRow label="Body" :value="dataLab.activeModels.active_body_model_id ?? 'none'" />
        <MetricRow label="Balloon" :value="dataLab.activeModels.active_balloon_model_id ?? 'none'" />
        <MetricRow label="Combined" :value="dataLab.activeModels.active_combined_model_id ?? 'none'" />
        <MetricRow label="Test adapter" :value="dataLab.activeModels.active_test_adapter ?? 'none'" />
      </DashboardCard>

      <DashboardCard title="Test Inference" subtitle="Mock/replay/snapshot adapter validation">
        <div class="flex flex-wrap gap-2">
          <button class="rounded-md bg-emerald-500 px-3 py-2 text-sm font-semibold text-slate-950" @click="dataLab.testModel(selectedModel?.model_id ?? null)">Run on mock frame</button>
          <button class="rounded-md bg-amber-400 px-3 py-2 text-sm font-semibold text-slate-950" @click="dataLab.testCircleAdapter">OpenCV circle test</button>
        </div>
        <div v-if="dataLab.inferenceResult" class="mt-4 grid gap-2 text-sm">
          <MetricRow label="Adapter" :value="dataLab.inferenceResult.adapter" />
          <MetricRow label="Latency" :value="`${dataLab.inferenceResult.latency_ms} ms`" />
          <MetricRow label="Detections" :value="dataLab.inferenceResult.detections.length" />
          <MetricRow label="Physical command" :value="dataLab.inferenceResult.no_physical_command_generated ? 'NO' : 'UNSAFE'" />
          <p class="text-amber-100">{{ dataLab.inferenceResult.warnings.join(', ') }}</p>
        </div>
      </DashboardCard>
    </section>

    <section v-if="activeTab === 'Capture'" class="grid gap-4 xl:grid-cols-3">
      <DashboardCard title="Capture Session" subtitle="Scenario metadata">
        <div class="grid gap-3">
          <input v-model="sessionForm.name" class="rounded-md border border-white/10 bg-black/20 px-3 py-2 text-sm" />
          <input v-model="sessionForm.operator" class="rounded-md border border-white/10 bg-black/20 px-3 py-2 text-sm" />
          <div class="grid gap-3 md:grid-cols-2">
            <select v-model="sessionForm.target_type" class="rounded-md border border-white/10 bg-black/20 px-3 py-2 text-sm"><option>f16</option><option>helicopter</option><option>ballistic_missile</option><option>mini_micro_uav</option><option>unknown</option></select>
            <select v-model="sessionForm.team" class="rounded-md border border-white/10 bg-black/20 px-3 py-2 text-sm"><option>enemy</option><option>friend</option><option>unknown</option></select>
            <select v-model="sessionForm.distance_m" class="rounded-md border border-white/10 bg-black/20 px-3 py-2 text-sm"><option>5</option><option>10</option><option>15</option><option>custom</option></select>
            <select v-model="sessionForm.lens_profile" class="rounded-md border border-white/10 bg-black/20 px-3 py-2 text-sm"><option>3.6mm</option><option>8mm</option><option>12mm</option><option>varifocal_custom</option><option>unknown</option></select>
          </div>
          <textarea v-model="sessionForm.notes" class="min-h-20 rounded-md border border-white/10 bg-black/20 px-3 py-2 text-sm" />
          <div class="flex flex-wrap gap-2">
            <button class="rounded-md bg-cyan-500 px-3 py-2 text-sm font-semibold text-slate-950" @click="startCapture">Start session</button>
            <button class="rounded-md bg-slate-700 px-3 py-2 text-sm font-semibold text-white" @click="dataLab.endSession">Stop</button>
            <button class="rounded-md bg-emerald-500 px-3 py-2 text-sm font-semibold text-slate-950" :disabled="!selectedSession" @click="snapshot">Snapshot</button>
          </div>
        </div>
      </DashboardCard>

      <DashboardCard title="Active Session" subtitle="Data capture only">
        <MetricRow label="Session" :value="dataLab.activeSession?.session_id ?? 'none'" />
        <MetricRow label="Active models" :value="activeModelSummary" />
        <MetricRow label="Snapshots" :value="dataLab.activeSession?.stats.snapshot_count ?? 0" />
        <MetricRow label="Latest snapshot" :value="dataLab.latestSnapshot?.frame_id ?? 'none'" />
        <StatusBadge label="Data capture only. No physical command generated." tone="warn" />
      </DashboardCard>

      <DashboardCard title="Safety Metadata" subtitle="Invariant recorded with sessions">
        <MetricRow label="dry_run" :value="dataLab.activeSession?.safety.dry_run ?? true" />
        <MetricRow label="hardware_enabled" :value="dataLab.activeSession?.safety.hardware_enabled ?? false" />
        <MetricRow label="physical command" :value="dataLab.activeSession?.safety.no_physical_command_generated ? 'NO' : 'NO'" />
      </DashboardCard>
    </section>

    <section v-if="activeTab === 'Sessions'" class="grid gap-4">
      <DashboardCard title="Sessions" subtitle="Capture metadata and review quality">
        <div class="overflow-x-auto">
          <table class="w-full min-w-[820px] text-left text-sm">
            <thead class="text-xs uppercase tracking-[0.14em] text-slate-500">
              <tr><th class="py-2">Session</th><th>Target</th><th>Team</th><th>Distance</th><th>Lens</th><th>Stats</th><th>Quality</th></tr>
            </thead>
            <tbody>
              <tr v-for="session in dataLab.sessions" :key="session.session_id" class="border-t border-white/8">
                <td class="py-2"><button class="text-cyan-200" @click="selectedSessionId = session.session_id">{{ session.name }}</button><p class="font-mono text-xs text-slate-500">{{ session.session_id }}</p></td>
                <td>{{ session.scenario.target_type }}</td>
                <td>{{ session.scenario.team }}</td>
                <td>{{ session.scenario.distance_m }}m</td>
                <td>{{ session.scenario.lens_profile }}</td>
                <td>{{ session.stats.snapshot_count }} snapshots / {{ session.stats.annotation_count }} annotations</td>
                <td><StatusBadge :label="session.quality" tone="neutral" /></td>
              </tr>
            </tbody>
          </table>
        </div>
      </DashboardCard>
      <DashboardCard title="Data Lab Session Evidence" subtitle="Latest session-level detection metadata">
        <div v-if="latestLabSession" class="grid gap-2 text-sm">
          <MetricRow label="Session" :value="latestLabSession.session_id" />
          <MetricRow label="Mode" :value="latestLabSession.mode" />
          <MetricRow label="Detections" :value="latestLabSession.stats.detection_count ?? 0" />
          <MetricRow label="Advisory only" :value="latestLabSession.advisory_only" />
          <MetricRow label="No physical command" :value="latestLabSession.no_physical_command_generated" />
          <pre class="max-h-80 overflow-auto rounded-md bg-black/30 p-3 text-xs">{{ JSON.stringify(latestLabSession.latest_detection, null, 2) }}</pre>
        </div>
        <p v-else class="text-sm text-slate-400">No Data Lab session evidence recorded yet.</p>
      </DashboardCard>
    </section>

    <section v-if="activeTab === 'Replay'" class="grid gap-4 xl:grid-cols-2">
      <DashboardCard title="Data Lab Replay" subtitle="Recorded metadata replay, no live camera required">
        <div class="mb-3 flex flex-wrap gap-2">
          <StatusBadge label="ADVISORY ONLY" tone="warn" />
          <StatusBadge label="REPLAY IS NOT PHYSICAL" tone="bad" />
        </div>
        <MetricRow label="Latest replay" :value="dataLab.dataLabReplay?.replay_id ?? 'not_run'" />
        <MetricRow label="Source session" :value="dataLab.dataLabReplay?.source_session_id ?? latestLabSession?.session_id ?? 'none'" />
        <MetricRow label="Replay status" :value="dataLab.dataLabReplay?.replay_status ?? 'not_run'" />
        <MetricRow label="Frame origin" :value="dataLab.dataLabReplay?.frame_origin ?? 'not_available'" />
        <MetricRow label="Detector" :value="dataLab.dataLabReplay?.detector ?? 'not_available'" />
        <MetricRow label="Events replayed" :value="dataLab.dataLabReplay?.events_replayed ?? 0" />
        <MetricRow label="Detections replayed" :value="dataLab.dataLabReplay?.detections_replayed ?? 0" />
        <MetricRow label="No physical command" :value="dataLab.dataLabReplay?.no_physical_command_generated === false ? 'UNSAFE' : 'true'" />
        <button class="mt-3 rounded-md bg-cyan-500 px-3 py-2 text-sm font-semibold text-slate-950" @click="dataLab.runDataLabReplayFromLatest">
          Run replay from latest session
        </button>
        <p class="mt-3 text-xs text-amber-100">Replay is advisory only; it replays saved detection metadata and does not move hardware.</p>
      </DashboardCard>

      <DashboardCard title="Legacy Replay Controls" subtitle="Session replay API smoke controls">
        <select v-model="selectedSessionId" class="mb-3 rounded-md border border-white/10 bg-black/20 px-3 py-2 text-sm">
          <option v-for="session in dataLab.sessions" :key="session.session_id" :value="session.session_id">{{ session.name }} / {{ session.session_id }}</option>
        </select>
        <div class="flex flex-wrap gap-2">
          <button class="rounded-md bg-cyan-500 px-3 py-2 text-sm font-semibold text-slate-950" :disabled="!selectedSessionId" @click="dataLab.loadReplay(selectedSessionId!)">Load</button>
          <button class="rounded-md bg-emerald-500 px-3 py-2 text-sm font-semibold text-slate-950" @click="dataLab.controlReplay('play')">Play</button>
          <button class="rounded-md bg-slate-700 px-3 py-2 text-sm font-semibold text-white" @click="dataLab.controlReplay('pause')">Pause</button>
          <button class="rounded-md bg-slate-700 px-3 py-2 text-sm font-semibold text-white" @click="dataLab.controlReplay('step')">Step</button>
          <button class="rounded-md bg-red-500 px-3 py-2 text-sm font-semibold text-white" @click="dataLab.controlReplay('stop')">Stop</button>
        </div>
        <div class="mt-3 flex flex-wrap gap-2">
          <button v-for="speed in [0.25, 0.5, 1, 2]" :key="speed" class="rounded bg-white/10 px-2 py-1 text-xs" @click="dataLab.changeReplaySpeed(speed)">{{ speed }}x</button>
        </div>
      </DashboardCard>
      <DashboardCard title="Replay Safety Evidence" subtitle="Metadata replay output">
        <MetricRow label="State" :value="dataLab.replay.state" />
        <MetricRow label="Frame" :value="`${dataLab.replay.frame_index} / ${dataLab.replay.frame_count}`" />
        <MetricRow label="Speed" :value="`${dataLab.replay.speed}x`" />
        <MetricRow label="Source" :value="dataLab.replay.source" />
        <StatusBadge label="Replay source, no physical command" tone="warn" />
      </DashboardCard>
    </section>

    <section v-if="activeTab === 'Annotation Review'" class="grid gap-4 xl:grid-cols-2">
      <DashboardCard title="Annotation Review Foundation" subtitle="Candidates generated from recorded detection metadata">
        <div class="mb-3 grid gap-2 text-sm md:grid-cols-3">
          <MetricRow label="Pending candidates" :value="pendingCandidates.length" />
          <MetricRow label="Accepted" :value="acceptedCandidates.length" />
          <MetricRow label="Rejected" :value="rejectedCandidates.length" />
        </div>
        <div class="max-h-[520px] overflow-auto rounded-md border border-white/10">
          <table class="w-full min-w-[920px] text-left text-sm">
            <thead class="text-xs uppercase tracking-[0.14em] text-slate-500">
              <tr><th class="px-3 py-2">Candidate</th><th>Session</th><th>Target class</th><th>Confidence</th><th>Source</th><th>Status</th><th>Safety</th><th>Actions</th></tr>
            </thead>
            <tbody>
              <tr v-for="candidate in dataLab.annotationCandidates" :key="candidate.candidate_id" class="border-t border-white/8">
                <td class="px-3 py-2">
                  <p class="max-w-[220px] break-all font-mono text-xs text-cyan-200">{{ candidate.candidate_id }}</p>
                  <p class="text-xs text-slate-500">{{ candidate.target_group }} / {{ candidate.detector }}</p>
                </td>
                <td class="max-w-[180px] truncate font-mono text-xs" :title="candidate.session_id">{{ candidate.session_id }}</td>
                <td>{{ candidate.class_name }}</td>
                <td>{{ candidate.confidence ?? 'not_available' }}</td>
                <td><StatusBadge :label="candidate.source" tone="warn" /></td>
                <td><StatusBadge :label="candidate.review_status" :tone="candidate.review_status === 'accepted' ? 'good' : candidate.review_status === 'rejected' ? 'bad' : 'warn'" /></td>
                <td><StatusBadge :label="candidate.no_physical_command_generated ? 'NO PHYSICAL COMMAND' : 'UNSAFE'" :tone="candidate.no_physical_command_generated ? 'good' : 'bad'" /></td>
                <td>
                  <div class="flex flex-wrap gap-1">
                    <button class="rounded bg-emerald-500 px-2 py-1 text-xs font-semibold text-slate-950" @click="dataLab.reviewCandidate(candidate.candidate_id, 'accepted')">Accept</button>
                    <button class="rounded bg-red-500 px-2 py-1 text-xs font-semibold text-white" @click="dataLab.reviewCandidate(candidate.candidate_id, 'rejected')">Reject</button>
                    <button class="rounded bg-amber-400 px-2 py-1 text-xs font-semibold text-slate-950" @click="dataLab.reviewCandidate(candidate.candidate_id, 'uncertain')">Mark uncertain</button>
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
          <p v-if="dataLab.annotationCandidates.length === 0" class="px-3 py-8 text-sm text-slate-400">No annotation candidates generated from Data Lab detection metadata yet.</p>
        </div>
      </DashboardCard>

      <DashboardCard title="Legacy Annotation Tools" subtitle="Minimum session annotation table">
        <div class="mb-3 flex flex-wrap gap-2">
          <button class="rounded-md bg-cyan-500 px-3 py-2 text-sm font-semibold text-slate-950" :disabled="!selectedSession" @click="selectedSession && dataLab.loadAnnotations(selectedSession.session_id)">Load annotations</button>
          <button class="rounded-md bg-emerald-500 px-3 py-2 text-sm font-semibold text-slate-950" :disabled="!selectedSession" @click="saveAnnotation">Save verified mock annotation</button>
          <button class="rounded-md bg-amber-400 px-3 py-2 text-sm font-semibold text-slate-950" :disabled="!selectedSession || !dataLab.inferenceResult" @click="convertPrediction">Prediction to annotation</button>
        </div>
        <div class="rounded-md border border-white/10 bg-black/20 p-6 text-center text-sm text-slate-400">Frame/snapshot preview placeholder</div>
      </DashboardCard>
      <DashboardCard title="Objects" subtitle="BBox and class rows">
        <div v-for="annotation in dataLab.annotations" :key="annotation.annotation_id" class="border-t border-white/8 py-3 first:border-t-0">
          <p class="font-mono text-xs text-cyan-200">{{ annotation.annotation_id }} / {{ annotation.frame_id }}</p>
          <div v-for="object in annotation.objects" :key="object.object_id" class="mt-2 grid gap-2 rounded-md bg-black/20 p-2 text-sm md:grid-cols-4">
            <span>{{ object.class_name }}</span>
            <span>{{ object.bbox_format }}</span>
            <span class="font-mono text-xs">{{ object.bbox.join(', ') }}</span>
            <StatusBadge :label="object.verified_by_operator ? 'verified' : 'prediction'" :tone="object.verified_by_operator ? 'good' : 'warn'" />
          </div>
        </div>
      </DashboardCard>
    </section>

    <section v-if="activeTab === 'YOLO Export'" class="grid gap-4 xl:grid-cols-2">
      <DashboardCard title="YOLO Export" subtitle="Ultralytics dataset format">
        <div class="grid gap-3">
          <input v-model="exportForm.dataset_name" class="rounded-md border border-white/10 bg-black/20 px-3 py-2 text-sm" />
          <input v-model="exportForm.version" class="rounded-md border border-white/10 bg-black/20 px-3 py-2 text-sm" />
          <select v-model="exportForm.export_mode" class="rounded-md border border-white/10 bg-black/20 px-3 py-2 text-sm">
            <option value="body_multiclass">body_multiclass</option>
            <option value="balloon_singleclass">balloon_singleclass</option>
            <option value="combined_body_balloon">combined_body_balloon</option>
            <option value="target_singleclass">target_singleclass</option>
          </select>
          <label class="text-sm">Train split <input v-model.number="exportForm.train_val_split" type="number" min="0.1" max="0.95" step="0.05" class="ml-2 rounded-md border border-white/10 bg-black/20 px-3 py-2" /></label>
          <label class="text-sm"><input v-model="exportForm.include_unverified_annotations" type="checkbox" /> include unverified annotations</label>
          <label class="text-sm"><input v-model="exportForm.include_model_predictions" type="checkbox" /> include model predictions</label>
          <div class="flex flex-wrap gap-2">
            <button class="rounded-md bg-cyan-500 px-3 py-2 text-sm font-semibold text-slate-950" @click="runValidation">Validate</button>
            <button class="rounded-md bg-emerald-500 px-3 py-2 text-sm font-semibold text-slate-950" @click="runExport">Export</button>
          </div>
        </div>
      </DashboardCard>
      <DashboardCard title="Export Result" subtitle="data.yaml preview">
        <pre class="overflow-x-auto rounded-md bg-black/30 p-3 text-xs text-slate-200">{{ yoloYamlPreview }}</pre>
        <div v-if="dataLab.validationResult" class="mt-3">
          <MetricRow label="Validation" :value="dataLab.validationResult.valid ? 'valid' : 'invalid'" />
          <MetricRow label="Checked" :value="dataLab.validationResult.checked_items" />
          <p class="text-xs text-amber-100">{{ dataLab.validationResult.warnings.join(', ') }}</p>
        </div>
        <div v-if="latestExport" class="mt-3">
          <MetricRow label="Latest export" :value="latestExport.dataset_id" />
          <MetricRow label="Output" :value="latestExport.output_path" />
          <MetricRow label="Images / labels" :value="`${latestExport.image_count} / ${latestExport.label_count}`" />
          <MetricRow label="Physical command" :value="latestExport.no_physical_command_generated ? 'NO' : 'UNSAFE'" />
        </div>
      </DashboardCard>
    </section>

    <section v-if="activeTab === 'Dataset Health'" class="grid gap-4 xl:grid-cols-3">
      <DashboardCard title="Data Lab Dataset Health" subtitle="Replay/annotation foundation metrics">
        <MetricRow label="Sessions" :value="dataLab.dataLabDatasetHealth?.sessions_count ?? 0" />
        <MetricRow label="Detection events" :value="dataLab.dataLabDatasetHealth?.detection_events_count ?? 0" />
        <MetricRow label="Annotation candidates" :value="dataLab.dataLabDatasetHealth?.annotation_candidates ?? 0" />
        <MetricRow label="Accepted annotations" :value="dataLab.dataLabDatasetHealth?.accepted_annotations ?? 0" />
        <MetricRow label="Rejected annotations" :value="dataLab.dataLabDatasetHealth?.rejected_annotations ?? 0" />
        <MetricRow label="Dataset ready for training" :value="dataLab.dataLabDatasetHealth?.dataset_ready_for_training ?? false" />
        <p class="mt-3 rounded-md border border-amber-400/25 bg-amber-400/10 p-2 text-sm text-amber-100">{{ dataLab.dataLabDatasetHealth?.reason ?? 'only mock/surrogate evidence or insufficient real data' }}</p>
      </DashboardCard>
      <DashboardCard title="Data Lab Distributions" subtitle="Class and source balance">
        <pre class="overflow-x-auto rounded-md bg-black/30 p-3 text-xs">{{ JSON.stringify({ class: dataLab.dataLabDatasetHealth?.class_distribution, source: dataLab.dataLabDatasetHealth?.source_distribution }, null, 2) }}</pre>
      </DashboardCard>
      <DashboardCard title="Recommended Next Collection" subtitle="Coverage warnings">
        <div class="grid gap-2">
          <p v-for="item in dataLab.health?.recommendations ?? []" :key="item" class="rounded-md border border-amber-400/25 bg-amber-400/10 p-2 text-sm text-amber-100">{{ item }}</p>
          <p v-if="(dataLab.health?.recommendations.length ?? 0) === 0" class="text-sm text-slate-400">No collection gaps detected yet.</p>
        </div>
      </DashboardCard>
    </section>

    <section v-if="activeTab === 'Demo Timeline'" class="grid gap-4 xl:grid-cols-2">
      <DashboardCard title="Demo Timeline Export" subtitle="End-to-end demo evidence generated for reports">
        <MetricRow label="Latest demo run" :value="demo.timeline.run_id" />
        <MetricRow label="Timeline steps" :value="demo.timeline.events.length" />
        <MetricRow label="Release demo ready" :value="demo.timeline.verdict.release_demo_ready" />
        <MetricRow label="Release blockers" :value="demo.timeline.verdict.release_demo_blockers.length" />
        <MetricRow label="Competition ready" :value="demo.timeline.verdict.competition_ready" />
        <MetricRow label="Competition blockers" :value="demo.timeline.verdict.competition_blockers.length" />
        <MetricRow label="Dataset ready for training" :value="demo.timeline.verdict.dataset_ready_for_training" />
        <MetricRow label="Dataset blockers" :value="demo.timeline.verdict.dataset_blockers.length" />
        <MetricRow label="No physical command" :value="demo.timeline.no_physical_command_generated" />
        <button class="mt-3 rounded-md bg-cyan-500 px-3 py-2 text-sm font-semibold text-slate-950" @click="demo.run">
          Run demo evidence timeline
        </button>
      </DashboardCard>
      <DashboardCard title="Timeline Files" subtitle="Reports/KTR export files">
        <MetricRow label="demo_timeline.json" value="Reports/KTR export" />
        <MetricRow label="demo_timeline.md" value="Reports/KTR export" />
        <MetricRow label="demo_readiness_summary.md" value="Reports/KTR export" />
        <MetricRow label="demo_runbook.md" value="Reports/KTR export" />
        <p class="mt-3 rounded-md border border-amber-400/25 bg-amber-400/10 p-2 text-sm text-amber-100">
          Demo timeline is evidence-only. It does not authorize competition readiness or physical commands.
        </p>
      </DashboardCard>
    </section>
  </div>
</template>
