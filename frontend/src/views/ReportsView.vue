<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import DashboardCard from '../components/dashboard/DashboardCard.vue'
import MetricRow from '../components/dashboard/MetricRow.vue'
import StatusBadge from '../components/shared/StatusBadge.vue'
import { useReportsStore } from '../stores/reportsStore'
import { useSelfTestStore } from '../stores/selfTestStore'
import { useReleaseStore } from '../stores/releaseStore'

const reports = useReportsStore()
const selfTest = useSelfTestStore()
const release = useReleaseStore()
const notes = ref('Demo/KTR export generated from Phase 11 console.')
const includeScreenshots = ref(true)

const latest = computed(() => reports.latestExport)
const selectedFiles = computed(() => reports.selectedExport?.files ?? [])
const selectedSummary = computed(() => JSON.stringify(reports.selectedExport?.summary ?? {}, null, 2))
const selectedModelSummary = computed(() => (reports.selectedExport?.summary.active_model ?? {}) as Record<string, unknown>)
const activeModelLabel = computed(() => {
  const kind = String(selectedModelSummary.value.package_kind ?? 'none')
  if (kind === 'fixture' || kind === 'test_adapter') return 'fixture/test adapter'
  if (kind === 'production') return 'production model'
  return 'none'
})
const selectedColdStartFile = computed(() => selectedFiles.value.find((file) => file.endsWith('cold_start_summary.md')) ?? 'not generated')
const selectedLauncherInspectionFile = computed(() => selectedFiles.value.find((file) => file.endsWith('launcher_inspection.md')) ?? 'not generated')
const selectedDataLabFiles = computed(() => ({
  summary: selectedFiles.value.find((file) => file.endsWith('data_lab_summary.md')) ?? 'not generated',
  sessions: selectedFiles.value.find((file) => file.endsWith('data_lab_sessions.json')) ?? 'not generated',
  detections: selectedFiles.value.find((file) => file.endsWith('detection_events_sample.jsonl')) ?? 'not generated',
  replay: selectedFiles.value.find((file) => file.endsWith('replay_readiness.md')) ?? 'not generated',
  replaySummary: selectedFiles.value.find((file) => file.endsWith('replay_summary.md')) ?? 'not generated',
  replayLatest: selectedFiles.value.find((file) => file.endsWith('replay_latest.json')) ?? 'not generated',
  annotationCandidates: selectedFiles.value.find((file) => file.endsWith('annotation_candidates.json')) ?? 'not generated',
  annotationSummary: selectedFiles.value.find((file) => file.endsWith('annotation_review_summary.md')) ?? 'not generated',
  datasetHealth: selectedFiles.value.find((file) => file.endsWith('dataset_health_summary.md')) ?? 'not generated',
  demoTimelineJson: selectedFiles.value.find((file) => file.endsWith('demo_timeline.json')) ?? 'not generated',
  demoTimelineMd: selectedFiles.value.find((file) => file.endsWith('demo_timeline.md')) ?? 'not generated',
  demoReadiness: selectedFiles.value.find((file) => file.endsWith('demo_readiness_summary.md')) ?? 'not generated',
  demoRunbook: selectedFiles.value.find((file) => file.endsWith('demo_runbook.md')) ?? 'not generated',
  juryDemoSummary: selectedFiles.value.find((file) => file.endsWith('jury_demo_summary.md')) ?? 'not generated',
  releaseDemoVerdict: selectedFiles.value.find((file) => file.endsWith('release_demo_verdict.json')) ?? 'not generated',
  evidenceIndex: selectedFiles.value.find((file) => file.endsWith('evidence_index.md')) ?? 'not generated',
  knownLimitations: selectedFiles.value.find((file) => file.endsWith('known_limitations.md')) ?? 'not generated',
  operatorScript: selectedFiles.value.find((file) => file.endsWith('demo_operator_script.md')) ?? 'not generated',
  releasePortabilityAudit: selectedFiles.value.find((file) => file.endsWith('release_portability_audit.md')) ?? 'not generated',
  cleanroomSmoke: selectedFiles.value.find((file) => file.endsWith('cleanroom_smoke_results.json')) ?? 'not generated',
  cleanroomLaunchNotes: selectedFiles.value.find((file) => file.endsWith('cleanroom_launch_notes.md')) ?? 'not generated',
  portableRuntimeRequirements: selectedFiles.value.find((file) => file.endsWith('portable_runtime_requirements.md')) ?? 'not generated',
  juryRehearsalSummary: selectedFiles.value.find((file) => file.endsWith('jury_rehearsal_summary.md')) ?? 'not generated',
  juryRehearsalVerdict: selectedFiles.value.find((file) => file.endsWith('jury_rehearsal_verdict.json')) ?? 'not generated',
  juryRehearsalOperatorScript: selectedFiles.value.find((file) => file.endsWith('jury_rehearsal_operator_script.md')) ?? 'not generated',
}))

function generate(kind: 'ktr' | 'demo' | 'readiness'): void {
  void reports.generate(kind, { notes: notes.value, include_screenshots: includeScreenshots.value })
}

onMounted(() => {
  void reports.refresh()
  void selfTest.refresh()
  void release.refresh()
})
</script>

<template>
  <div class="grid gap-4">
    <div class="rounded-md border border-red-400/30 bg-red-500/8 px-4 py-3">
      <div class="flex flex-wrap items-center gap-2">
        <StatusBadge label="REPORTS DO NOT ENABLE PHYSICAL COMMANDS" tone="bad" />
        <StatusBadge label="NO PHYSICAL COMMAND" tone="warn" />
        <StatusBadge label="KTR/DEMO PACK ONLY" tone="neutral" />
      </div>
      <p class="mt-2 text-sm text-slate-300">
        KTR export, readiness pack and demo runbook are documentation outputs only. They do not change safety state, hardware enable state or fire policy.
      </p>
    </div>

    <div class="grid gap-4 xl:grid-cols-3">
      <DashboardCard title="Generate Reports" subtitle="KTR, demo and readiness packs">
        <label class="grid gap-1 text-sm text-slate-300">
          Notes
          <textarea v-model="notes" class="min-h-24 rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm text-white outline-none focus:border-cyan-300" />
        </label>
        <label class="mt-3 flex items-center gap-2 text-sm text-slate-300">
          <input v-model="includeScreenshots" type="checkbox" class="h-4 w-4 accent-cyan-400" />
          Include screenshots manifest when available
        </label>
        <div class="mt-4 grid gap-2 sm:grid-cols-3">
          <button class="focus-ring rounded-md border border-cyan-400/40 bg-cyan-400/12 px-3 py-2 text-sm font-semibold text-cyan-100 disabled:cursor-not-allowed disabled:opacity-50" :disabled="reports.isGenerating" @click="generate('ktr')">
            Generate KTR Summary
          </button>
          <button class="focus-ring rounded-md border border-emerald-400/40 bg-emerald-400/12 px-3 py-2 text-sm font-semibold text-emerald-100 disabled:cursor-not-allowed disabled:opacity-50" :disabled="reports.isGenerating" @click="generate('demo')">
            Generate Demo Pack
          </button>
          <button class="focus-ring rounded-md border border-amber-400/40 bg-amber-400/12 px-3 py-2 text-sm font-semibold text-amber-100 disabled:cursor-not-allowed disabled:opacity-50" :disabled="reports.isGenerating" @click="generate('readiness')">
            Generate Readiness Pack
          </button>
        </div>
        <p v-if="reports.error" class="mt-3 rounded-md border border-red-400/30 bg-red-400/10 px-3 py-2 text-sm text-red-100">
          {{ reports.error }}
        </p>
      </DashboardCard>

      <DashboardCard title="Latest Self-Test" subtitle="Included in readiness summaries">
        <MetricRow label="Status" :value="selfTest.latestRun?.status ?? 'not run'" />
        <MetricRow label="Readiness" :value="selfTest.latestRun?.readiness_level ?? 'not_ready'" />
        <MetricRow label="Critical failures" :value="selfTest.latestRun?.summary.critical_failures ?? 0" />
        <MetricRow label="Warnings" :value="selfTest.latestRun?.summary.warning ?? 0" />
        <MetricRow label="No physical command" :value="selfTest.latestRun?.no_physical_command_generated ?? true" />
      </DashboardCard>

      <DashboardCard title="Report Status" subtitle="Export service state">
        <MetricRow label="Root dir" :value="reports.status.root_dir" />
        <MetricRow label="Exports" :value="reports.status.exports_count" />
        <MetricRow label="Latest" :value="latest?.export_id ?? 'none'" />
        <MetricRow label="No physical command" :value="reports.status.no_physical_command_generated" />
        <div class="mt-3 flex flex-wrap gap-2">
          <StatusBadge :label="reports.isGenerating ? 'EXPORT RUNNING' : 'IDLE'" :tone="reports.isGenerating ? 'warn' : 'good'" />
          <StatusBadge label="DRY-RUN SAFE" tone="warn" />
        </div>
      </DashboardCard>
    </div>

    <DashboardCard title="Portable Release Package" subtitle="Latest ZIP/package evidence">
      <div class="grid gap-3 md:grid-cols-3">
        <MetricRow label="Package ID" :value="release.latestPackage?.package_id ?? 'not generated'" />
        <MetricRow label="Output dir" :value="release.latestPackage?.output_dir ?? 'not generated'" />
        <MetricRow label="ZIP path" :value="release.latestPackage?.zip_path ?? 'not generated'" />
        <MetricRow label="Source commit" :value="release.latestPackage?.source_commit ?? 'not generated'" />
        <MetricRow label="Package workflow commit" :value="release.latestPackage?.package_workflow_commit ?? 'not generated'" />
        <MetricRow label="Report/docs commit" :value="release.latestPackage?.report_commit ?? 'not generated'" />
        <MetricRow label="Files count" :value="release.latestPackage?.files_count ?? 0" />
        <MetricRow label="Checksum status" :value="release.latestPackage?.checksum_status ?? 'not generated'" />
        <MetricRow label="Release demo ready" :value="release.latestPackage?.release_demo_ready ?? false" />
        <MetricRow label="Competition ready" :value="release.latestPackage?.competition_ready ?? false" />
        <MetricRow label="no_physical_command_generated" :value="release.latestPackage?.no_physical_command_generated ?? true" />
      </div>
      <div class="mt-3 flex flex-wrap gap-2">
        <button class="rounded-md bg-cyan-500 px-3 py-2 text-sm font-semibold text-slate-950 disabled:cursor-not-allowed disabled:opacity-60" :disabled="release.isBuildingPackage" @click="release.buildPackage">
          {{ release.isBuildingPackage ? 'Building package...' : 'Build Portable Release ZIP' }}
        </button>
        <StatusBadge label="DEMO/EVIDENCE PACKAGE" tone="warn" />
        <StatusBadge label="NO PHYSICAL COMMAND" tone="bad" />
      </div>
    </DashboardCard>

    <DashboardCard title="Clean-room Verification" subtitle="Extracted ZIP smoke evidence">
      <div class="grid gap-3 md:grid-cols-3">
        <MetricRow label="Latest clean-room run" :value="release.latestCleanroom?.run_id ?? 'not run'" />
        <MetricRow label="Extract path" :value="release.latestCleanroom?.extract_path ?? 'not available'" />
        <MetricRow label="Smoke status" :value="release.latestCleanroom?.smoke_status ?? 'not run'" />
        <MetricRow label="Endpoints passed" :value="release.latestCleanroom ? `${release.latestCleanroom.endpoints_passed}/${release.latestCleanroom.endpoints_total}` : '0/0'" />
        <MetricRow label="Competition ready" :value="release.latestCleanroom?.competition_ready ?? false" />
        <MetricRow label="no_physical_command_generated" :value="release.latestCleanroom?.no_physical_command_generated ?? true" />
      </div>
      <div class="mt-3 flex flex-wrap gap-2">
        <button class="rounded-md bg-cyan-500 px-3 py-2 text-sm font-semibold text-slate-950 disabled:cursor-not-allowed disabled:opacity-60" :disabled="release.isRunningCleanroom" @click="release.runCleanroomVerification">
          {{ release.isRunningCleanroom ? 'Running clean-room...' : 'Run Clean-room Verification' }}
        </button>
        <StatusBadge label="CLEAN-ROOM EVIDENCE" tone="good" />
        <StatusBadge label="NO PHYSICAL COMMAND" tone="bad" />
      </div>
    </DashboardCard>

    <DashboardCard title="Latest Demo Package" subtitle="Jury demo timeline, verdict and evidence files">
      <div class="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        <MetricRow label="Latest demo timeline" :value="String(latest?.summary?.latest_demo_timeline ?? selectedDataLabFiles.demoTimelineMd)" />
        <MetricRow label="Latest readiness verdict" :value="selectedDataLabFiles.releaseDemoVerdict" />
        <MetricRow label="Latest Data Lab evidence" :value="selectedDataLabFiles.summary" />
        <MetricRow label="Latest report files" :value="latest?.files.length ?? 0" />
        <MetricRow label="Jury demo summary" :value="selectedDataLabFiles.juryDemoSummary" />
        <MetricRow label="Evidence index" :value="selectedDataLabFiles.evidenceIndex" />
        <MetricRow label="Known limitations" :value="selectedDataLabFiles.knownLimitations" />
        <MetricRow label="Operator script" :value="selectedDataLabFiles.operatorScript" />
        <MetricRow label="Clean-room audit" :value="selectedDataLabFiles.releasePortabilityAudit" />
        <MetricRow label="Clean-room smoke" :value="selectedDataLabFiles.cleanroomSmoke" />
        <MetricRow label="Jury rehearsal summary" :value="selectedDataLabFiles.juryRehearsalSummary" />
        <MetricRow label="Jury rehearsal verdict" :value="selectedDataLabFiles.juryRehearsalVerdict" />
        <MetricRow label="no_physical_command_generated" :value="latest?.no_physical_command_generated ?? true" />
      </div>
      <div class="mt-3 flex flex-wrap gap-2">
        <StatusBadge label="DEMO PACKAGE ONLY" tone="warn" />
        <StatusBadge label="COMPETITION READINESS SEPARATE" tone="bad" />
        <StatusBadge label="NO PHYSICAL COMMAND" tone="bad" />
      </div>
    </DashboardCard>

    <div class="grid gap-4 xl:grid-cols-[1fr_1.3fr]">
      <DashboardCard title="Exports" subtitle="Generated KTR/demo/readiness packs">
        <div class="max-h-[560px] overflow-auto rounded-md border border-white/8">
          <button
            v-for="item in reports.exportsList"
            :key="item.export_id"
            class="grid w-full gap-2 border-b border-white/8 px-3 py-3 text-left text-sm transition hover:bg-white/5"
            :class="{ 'bg-cyan-400/10': reports.selectedExport?.export_id === item.export_id }"
            @click="reports.selectedExport = item"
          >
            <div class="flex flex-wrap items-center justify-between gap-2">
              <span class="font-mono text-cyan-200">{{ item.export_id }}</span>
              <StatusBadge :label="item.status" :tone="item.status === 'completed' ? 'good' : item.status === 'failed' ? 'bad' : 'warn'" />
            </div>
            <div class="flex flex-wrap gap-2 text-xs text-slate-400">
              <span>{{ item.kind }}</span>
              <span>{{ new Date(item.created_at * 1000).toLocaleString() }}</span>
              <span>{{ item.files.length }} files</span>
            </div>
          </button>
          <p v-if="reports.exportsList.length === 0" class="px-3 py-8 text-sm text-slate-400">
            No report exports yet.
          </p>
        </div>
      </DashboardCard>

      <DashboardCard title="Export Detail / Preview" subtitle="Output paths and metadata">
        <template v-if="reports.selectedExport">
          <div class="mb-4 grid gap-3 md:grid-cols-2">
            <MetricRow label="Export ID" :value="reports.selectedExport.export_id" />
            <MetricRow label="Kind" :value="reports.selectedExport.kind" />
            <MetricRow label="Output dir" :value="reports.selectedExport.output_dir" />
            <MetricRow label="No physical command" :value="reports.selectedExport.no_physical_command_generated" />
            <MetricRow label="Active model" :value="activeModelLabel" />
            <MetricRow label="Active model id" :value="String(selectedModelSummary.active_model_id ?? 'none')" />
            <MetricRow label="Production model" :value="selectedModelSummary.production_model === true ? 'loaded' : 'not loaded'" />
            <MetricRow label="Production ready" :value="String(selectedModelSummary.production_ready ?? false)" />
            <MetricRow label="Competition readiness" :value="selectedModelSummary.competition_ready === true ? 'ready' : String(selectedModelSummary.competition_status ?? 'blocked')" />
            <MetricRow label="Package kind" :value="String(selectedModelSummary.package_kind ?? 'not_available')" />
            <MetricRow label="No physical command generated" :value="String(selectedModelSummary.no_physical_command_generated ?? reports.selectedExport.no_physical_command_generated)" />
            <MetricRow label="Cold-start summary" :value="selectedColdStartFile" />
            <MetricRow label="Current first-run" :value="String(reports.selectedExport.summary.current_first_run_status ?? 'open')" />
            <MetricRow label="Current profile" :value="String(reports.selectedExport.summary.current_profile_id ?? 'release_candidate_ready')" />
            <MetricRow label="Profile evaluation" :value="String(reports.selectedExport.summary.current_profile_evaluation_status ?? 'not_evaluated')" />
            <MetricRow label="Stale evidence" :value="String(reports.selectedExport.summary.stale_evidence ?? false)" />
            <MetricRow label="Previous first-run" :value="String(reports.selectedExport.summary.last_successful_first_run_run_id ?? 'none')" />
            <MetricRow label="Launcher inspection" :value="selectedLauncherInspectionFile" />
            <MetricRow label="Data Lab summary" :value="selectedDataLabFiles.summary" />
            <MetricRow label="Data Lab sessions" :value="selectedDataLabFiles.sessions" />
            <MetricRow label="Detection sample JSONL" :value="selectedDataLabFiles.detections" />
            <MetricRow label="Replay readiness" :value="selectedDataLabFiles.replay" />
            <MetricRow label="Replay summary" :value="selectedDataLabFiles.replaySummary" />
            <MetricRow label="Replay latest JSON" :value="selectedDataLabFiles.replayLatest" />
            <MetricRow label="Annotation candidates" :value="selectedDataLabFiles.annotationCandidates" />
            <MetricRow label="Annotation review summary" :value="selectedDataLabFiles.annotationSummary" />
            <MetricRow label="Dataset health summary" :value="selectedDataLabFiles.datasetHealth" />
            <MetricRow label="Demo timeline JSON" :value="selectedDataLabFiles.demoTimelineJson" />
            <MetricRow label="Demo timeline MD" :value="selectedDataLabFiles.demoTimelineMd" />
            <MetricRow label="Demo readiness" :value="selectedDataLabFiles.demoReadiness" />
            <MetricRow label="Demo runbook" :value="selectedDataLabFiles.demoRunbook" />
            <MetricRow label="Jury demo summary" :value="selectedDataLabFiles.juryDemoSummary" />
            <MetricRow label="Release demo verdict" :value="selectedDataLabFiles.releaseDemoVerdict" />
            <MetricRow label="Evidence index" :value="selectedDataLabFiles.evidenceIndex" />
            <MetricRow label="Known limitations" :value="selectedDataLabFiles.knownLimitations" />
            <MetricRow label="Demo operator script" :value="selectedDataLabFiles.operatorScript" />
            <MetricRow label="Release portability audit" :value="selectedDataLabFiles.releasePortabilityAudit" />
            <MetricRow label="Clean-room smoke results" :value="selectedDataLabFiles.cleanroomSmoke" />
            <MetricRow label="Clean-room launch notes" :value="selectedDataLabFiles.cleanroomLaunchNotes" />
            <MetricRow label="Portable runtime requirements" :value="selectedDataLabFiles.portableRuntimeRequirements" />
            <MetricRow label="Jury rehearsal summary" :value="selectedDataLabFiles.juryRehearsalSummary" />
            <MetricRow label="Jury rehearsal verdict" :value="selectedDataLabFiles.juryRehearsalVerdict" />
            <MetricRow label="Jury rehearsal operator script" :value="selectedDataLabFiles.juryRehearsalOperatorScript" />
            <MetricRow label="Model validation" :value="String(reports.selectedExport.summary.model_validation_status ?? 'unavailable')" />
            <MetricRow label="Class mapping" :value="String(reports.selectedExport.summary.class_mapping_status ?? 'unavailable')" />
            <MetricRow label="Advisory only" :value="String(reports.selectedExport.summary.advisory_only ?? true)" />
          </div>
          <div class="grid gap-4 lg:grid-cols-2">
            <div>
              <h4 class="mb-2 text-sm font-semibold text-white">Generated files</h4>
              <div class="grid gap-1 rounded-md border border-white/8 bg-black/20 p-3">
                <p v-for="file in selectedFiles" :key="file" class="break-all font-mono text-xs text-slate-300">
                  {{ file }}
                </p>
              </div>
            </div>
            <div>
              <h4 class="mb-2 text-sm font-semibold text-white">Summary JSON</h4>
              <pre class="max-h-80 overflow-auto rounded-md border border-white/8 bg-black/30 p-3 text-xs text-slate-300">{{ selectedSummary }}</pre>
            </div>
          </div>
        </template>
        <p v-else class="rounded-md border border-white/8 bg-black/20 px-3 py-8 text-center text-sm text-slate-400">
          Select or generate an export to inspect details.
        </p>
      </DashboardCard>
    </div>
  </div>
</template>
