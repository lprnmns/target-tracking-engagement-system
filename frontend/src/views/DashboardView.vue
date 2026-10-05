<script setup lang="ts">
import { computed, onMounted } from 'vue'
import DashboardCard from '../components/dashboard/DashboardCard.vue'
import MetricRow from '../components/dashboard/MetricRow.vue'
import SafetyGatesPanel from '../components/safety/SafetyGatesPanel.vue'
import StatusBadge from '../components/shared/StatusBadge.vue'
import { useDecisionStore } from '../stores/decisionStore'
import { useDataLabStore } from '../stores/dataLabStore'
import { useMotionStore } from '../stores/motionStore'
import { useSelfTestStore } from '../stores/selfTestStore'
import { useSystemStore } from '../stores/systemStore'
import { useVisionStore } from '../stores/visionStore'
import { useReportsStore } from '../stores/reportsStore'
import { useHardwareStore } from '../stores/hardwareStore'
import { useDeviceRuntimeStore } from '../stores/deviceRuntimeStore'
import { useFirstRunStore } from '../stores/firstRunStore'
import { useInterfacesStore } from '../stores/interfacesStore'
import { useReleaseStore } from '../stores/releaseStore'
import { useDeviceProfileStore } from '../stores/deviceProfileStore'
import { useDemoStore } from '../stores/demoStore'
import { readableReasonText, reasonLabel } from '../utils/safetyLabels'

const store = useSystemStore()
const decision = useDecisionStore()
const dataLab = useDataLabStore()
const motion = useMotionStore()
const vision = useVisionStore()
const selfTest = useSelfTestStore()
const reports = useReportsStore()
const hardware = useHardwareStore()
const deviceRuntime = useDeviceRuntimeStore()
const firstRun = useFirstRunStore()
const interfaces = useInterfacesStore()
const release = useReleaseStore()
const deviceProfile = useDeviceProfileStore()
const demo = useDemoStore()

const effectiveBodyCount = computed(() => vision.visionStatus.running ? vision.visionStatus.body_count : 0)
const effectiveBalloonCount = computed(() => vision.visionStatus.running ? vision.visionStatus.balloon_count : 0)
const surrogateActive = computed(() => ['mock_camera_surrogate', 'live_camera_surrogate'].includes(deviceRuntime.visionStatus.effective_adapter))
const surrogateSourceKind = computed(() => deviceRuntime.visionStatus.surrogate_source_kind ?? vision.latestEvent?.camera_source_kind ?? null)
const targetSource = computed(() => surrogateActive.value ? (vision.latestEvent?.source ?? deviceRuntime.visionStatus.effective_adapter) : vision.visionStatus.vision_mode)
const mainBlocker = computed(() => decision.decision.blocking_reasons[0] ?? 'clear')
const selectedProfileStatus = computed(() => firstRun.currentProfileEvaluationStatus)
const competitionStatus = computed(() => firstRun.latestReport && firstRun.currentProfileEvaluationStatus !== 'not_evaluated' ? firstRun.latestReport.profile_statuses.competition_rehearsal_ready ?? 'not_evaluated' : 'not_evaluated')
const lastSuccessfulFirstRun = computed(() => firstRun.status.last_successful_first_run)
const releaseManifestLabel = computed(() => {
  if (!release.status.release_manifest_path) return 'not generated'
  return release.status.release_manifest_path.split('/').pop() ?? 'generated'
})
const topBlockers = computed(() => {
  const profile = firstRun.currentProfileId
  const hardBlocking = profile === 'competition_rehearsal_ready'
  const blockers: Array<{ text: string; blocking: boolean }> = []
  if (!store.systemState.armed) blockers.push({ text: 'System is disarmed', blocking: true })
  if (!selfTest.latestRun) blockers.push({ text: 'Self-test not run', blocking: true })
  if (!deviceRuntime.visionStatus.production_yolo_loaded) blockers.push({ text: 'Production YOLO model not loaded', blocking: hardBlocking })
  if (!hardware.status.pico_verified) blockers.push({ text: 'Pico telemetry not verified', blocking: hardBlocking })
  if (!hardware.status.physical_command_enabled) blockers.push({ text: 'Hardware command path disabled', blocking: false })
  return blockers.slice(0, 4)
})
const picoReadonlyActive = computed(() => hardware.status.transport_mode === 'real_readonly' && hardware.status.port_open)
const picoDashboardConnection = computed(() => {
  if (picoReadonlyActive.value) {
    if (hardware.status.telemetry_received || hardware.status.pico_verified) return 'READ-ONLY TELEMETRY'
    return 'READ-ONLY PORT OPEN / NO TELEMETRY'
  }
  return store.picoTelemetry.connection_status
})
const picoDashboardSubtitle = computed(() => (picoReadonlyActive.value ? 'Real serial RX-only state' : 'Mock telemetry from backend'))
const picoPortLabel = computed(() => hardware.status.telemetry.port ?? hardware.ports.find((port) => port.is_candidate_pico)?.device ?? store.picoTelemetry.port)
const missionReady = computed(() => (
  store.connectionStatus === 'connected'
  && store.systemState.armed
  && !store.systemState.hardware_enabled
  && store.systemState.dry_run
  && decision.decision.decision_state !== 'FAULT'
))
const healthItems = computed(() => [
  { label: 'backend', ok: store.connectionStatus === 'connected', value: store.connectionStatus },
  { label: 'camera', ok: vision.cameraStatus.running, value: vision.cameraStatus.running ? 'stream running' : 'stream stopped' },
  { label: 'vision', ok: vision.visionStatus.running, value: vision.visionStatus.running ? 'inference running' : 'inference stopped' },
  { label: 'pico', ok: picoReadonlyActive.value || store.picoTelemetry.connection_status !== 'DISCONNECTED', value: picoDashboardConnection.value },
  { label: 'serial', ok: true, value: hardware.status.transport_mode === 'real_readonly' ? 'real read-only' : 'mock transport' },
  { label: 'motion', ok: motion.state.dry_run, value: motion.state.motion_state },
])

onMounted(() => {
  void dataLab.refresh()
  void selfTest.refresh()
  void reports.refresh()
  void hardware.refresh()
  void deviceRuntime.refresh()
  void firstRun.refresh()
  void interfaces.refresh()
  void release.refresh()
  void release.coldStartCheck()
  void deviceProfile.refresh()
  void demo.refresh()
})
</script>

<template>
  <div class="grid gap-4">
    <div class="grid gap-4 xl:grid-cols-3">
      <DashboardCard title="Mission Readiness" subtitle="Safety authority summary">
        <div class="mb-4 flex flex-wrap gap-2">
          <StatusBadge :label="missionReady ? 'READY' : 'NOT READY'" :tone="missionReady ? 'good' : 'bad'" />
          <StatusBadge label="NO PHYSICAL COMMAND" tone="warn" />
          <StatusBadge :label="`PROFILE: ${firstRun.currentProfileId.replace('_ready', '').replaceAll('_', ' ').toUpperCase()}`" tone="neutral" />
          <StatusBadge :label="`PROFILE EVAL: ${String(selectedProfileStatus).replace('_', ' ').toUpperCase()}`" :tone="selectedProfileStatus === 'passed' ? 'good' : selectedProfileStatus === 'failed' || selectedProfileStatus === 'blocked' ? 'bad' : selectedProfileStatus === 'not_evaluated' ? 'neutral' : 'warn'" />
          <StatusBadge :label="`COMPETITION: ${String(competitionStatus).toUpperCase()}`" :tone="competitionStatus === 'passed' ? 'good' : competitionStatus === 'failed' || competitionStatus === 'blocked' ? 'bad' : 'warn'" />
          <StatusBadge label="MISSION READINESS BLOCKED" tone="bad" />
        </div>
        <div class="mb-3 grid gap-2">
          <p class="text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">Top blockers</p>
          <div v-for="blocker in topBlockers" :key="blocker.text" class="flex items-center justify-between gap-3 rounded-md border border-white/8 bg-black/18 px-3 py-2 text-sm">
            <span class="text-slate-200">{{ blocker.text }}</span>
            <StatusBadge :label="blocker.blocking ? 'BLOCKING' : 'DEMO LIMITATION'" :tone="blocker.blocking ? 'bad' : 'warn'" />
          </div>
        </div>
        <MetricRow label="Decision blocker" :value="reasonLabel(mainBlocker)" />
        <MetricRow label="Profile status" :value="selectedProfileStatus" />
        <MetricRow label="Last successful first-run" :value="lastSuccessfulFirstRun ? `${lastSuccessfulFirstRun.run_id} / ${new Date(lastSuccessfulFirstRun.timestamp * 1000).toLocaleString()}` : 'none'" />
        <MetricRow label="Competition readiness" :value="competitionStatus" />
        <MetricRow label="Decision" :value="decision.decision.decision_state" />
        <MetricRow label="Fire policy" :value="store.systemState.fire_policy" />
        <p class="mt-3 rounded-md border border-amber-400/30 bg-amber-400/10 px-3 py-2 text-sm text-amber-100">
          Release profile passed does not mean competition rehearsal ready.
        </p>
      </DashboardCard>

      <DashboardCard title="System Health" subtitle="Separated state channels">
        <div class="grid gap-2">
          <div v-for="item in healthItems" :key="item.label" class="flex items-center justify-between gap-3 border-t border-white/8 py-2 text-sm first:border-t-0">
            <span class="text-slate-400">{{ item.label }}</span>
            <StatusBadge :label="item.value" :tone="item.ok ? 'good' : 'warn'" />
          </div>
        </div>
      </DashboardCard>

      <DashboardCard title="Live Target Summary" subtitle="Advisory vision metadata">
        <MetricRow label="Body count" :value="effectiveBodyCount" />
        <MetricRow label="Balloon count" :value="effectiveBalloonCount" />
        <MetricRow label="Team/source" :value="`${decision.decision.target_team} / ${targetSource}`" />
        <MetricRow label="Frame origin" :value="vision.visionStatus.frame_origin ?? deviceRuntime.visionStatus.frame_origin ?? 'not_available'" />
        <MetricRow label="Detected circles" :value="surrogateActive ? effectiveBalloonCount : 'not surrogate'" />
        <MetricRow label="Target center" :value="surrogateActive ? `${vision.latestEvent?.aim_points?.[0]?.x ?? 'none'},${vision.latestEvent?.aim_points?.[0]?.y ?? 'none'}` : 'not surrogate'" />
        <div class="mt-3 flex flex-wrap gap-2">
          <StatusBadge label="ADVISORY ONLY" tone="warn" />
          <StatusBadge :label="surrogateActive ? String(targetSource).toUpperCase() : vision.visionStatus.vision_mode === 'mock' ? 'MOCK DATA' : 'MODEL DATA'" tone="warn" />
          <StatusBadge v-if="surrogateActive" :label="surrogateSourceKind === 'mock' ? 'MOCK/SYNTHETIC EVIDENCE' : 'REAL CAMERA FRAME EVIDENCE'" :tone="surrogateSourceKind === 'mock' ? 'warn' : 'good'" />
          <StatusBadge v-if="surrogateActive" label="NOT PRODUCTION YOLO" tone="bad" />
        </div>
      </DashboardCard>
    </div>

    <div class="grid gap-4 xl:grid-cols-3">
      <DashboardCard title="System State" subtitle="Backend authority state">
        <MetricRow label="Mode" :value="store.systemState.mode" />
        <MetricRow label="Fire policy" :value="store.systemState.fire_policy" />
        <MetricRow label="Armed" :value="store.systemState.armed" />
        <MetricRow label="Ready" :value="store.systemState.ready" />
        <MetricRow label="Uptime" :value="`${store.systemState.uptime_s}s`" />
      </DashboardCard>

      <DashboardCard title="Safety State" subtitle="Default reject-by-policy">
        <MetricRow label="Decision" :value="decision.decision.decision_state" />
        <MetricRow label="dry_run" :value="store.systemState.dry_run" />
        <MetricRow label="hardware_enabled" :value="store.systemState.hardware_enabled" />
        <MetricRow label="Reason" :value="readableReasonText(decision.decision.decision_reason)" />
      </DashboardCard>

      <DashboardCard title="WebSocket Connection" subtitle="No frontend mock fallback">
        <div class="flex flex-wrap gap-2">
          <StatusBadge
            :label="store.connectionStatus"
            :tone="store.connectionStatus === 'connected' ? 'good' : 'bad'"
          />
          <StatusBadge :label="store.lastError ?? 'no error'" :tone="store.lastError ? 'warn' : 'neutral'" />
        </div>
        <p class="mt-4 text-sm text-slate-400">
          If the backend is offline, the console keeps reconnecting and shows disconnected state only.
        </p>
      </DashboardCard>
    </div>

    <div class="grid gap-4 xl:grid-cols-2">
      <DashboardCard title="Pico Status" :subtitle="picoDashboardSubtitle">
        <MetricRow label="Connection" :value="picoDashboardConnection" />
        <MetricRow label="Port" :value="picoPortLabel" />
        <MetricRow label="Transport" :value="hardware.status.transport_mode" />
        <MetricRow label="Port open" :value="hardware.status.port_open" />
        <MetricRow label="RX-only" :value="hardware.status.readonly" />
        <MetricRow label="Telemetry received" :value="hardware.status.telemetry_received" />
        <MetricRow label="Heartbeat age" :value="hardware.status.telemetry.heartbeat_age_ms === null ? 'not available' : `${hardware.status.telemetry.heartbeat_age_ms} ms`" />
        <MetricRow label="Driver enabled" :value="hardware.status.telemetry.driver_enabled ?? store.picoTelemetry.driver_enabled" />
        <MetricRow label="Last error" :value="hardware.status.telemetry.last_error ?? store.picoTelemetry.last_error" />
        <div class="mt-3 flex flex-wrap gap-2">
          <StatusBadge :label="picoReadonlyActive ? 'REAL SERIAL RX-ONLY' : 'PICO TELEMETRY DISCONNECTED'" :tone="picoReadonlyActive ? 'warn' : 'bad'" />
          <StatusBadge label="NO PHYSICAL COMMAND" tone="bad" />
        </div>
      </DashboardCard>

      <DashboardCard title="Hardware Status" subtitle="Phase 12 read-only discovery">
        <MetricRow label="Physical Pico" :value="hardware.status.physical_pico" />
        <MetricRow label="Mock Pico" :value="hardware.status.mock_pico_active ? 'active' : 'inactive'" />
        <MetricRow label="Connection" :value="hardware.status.connection_state" />
        <MetricRow label="Pico verified" :value="hardware.status.pico_verified" />
        <MetricRow label="Telemetry received" :value="hardware.status.telemetry_received" />
        <MetricRow label="Physical commands" :value="hardware.status.physical_command_enabled ? 'enabled' : 'disabled'" />
        <MetricRow label="Telemetry age" :value="hardware.status.telemetry.heartbeat_age_ms === null ? 'not available' : `${hardware.status.telemetry.heartbeat_age_ms} ms`" />
        <MetricRow label="Safe state" :value="hardware.status.telemetry.safe_state === null ? 'unknown' : hardware.status.telemetry.safe_state" />
        <div class="mt-3 flex flex-wrap gap-2">
          <StatusBadge :label="hardware.status.transport_source === 'real_serial' ? 'REAL SERIAL READ-ONLY' : 'MOCK / DISCONNECTED'" tone="warn" />
          <StatusBadge label="NO PHYSICAL COMMAND" tone="bad" />
        </div>
      </DashboardCard>

      <DashboardCard title="Device / Runtime" subtitle="Phase 12 source and inference settings">
        <MetricRow label="Selected camera" :value="deviceRuntime.cameraStatus.selected_camera" />
        <MetricRow label="Actual resolution" :value="`${deviceRuntime.cameraStatus.actual_width}x${deviceRuntime.cameraStatus.actual_height}@${deviceRuntime.cameraStatus.actual_fps}`" />
        <MetricRow label="Inference adapter" :value="deviceRuntime.visionStatus.effective_adapter" />
        <MetricRow label="Active model" :value="deviceRuntime.visionStatus.active_model_summary.active_combined_model_id ?? deviceRuntime.visionStatus.active_model_summary.active_test_adapter ?? 'none'" />
        <MetricRow label="Production YOLO loaded" :value="deviceRuntime.visionStatus.production_yolo_loaded" />
        <MetricRow label="conf/iou/imgsz" :value="`${deviceRuntime.visionStatus.profile.conf}/${deviceRuntime.visionStatus.profile.iou}/${deviceRuntime.visionStatus.profile.imgsz}`" />
        <MetricRow label="Pico candidates" :value="deviceRuntime.inventory.pico_candidates.length" />
        <MetricRow label="Pico verified" :value="hardware.status.pico_verified" />
        <MetricRow label="Warnings" :value="deviceRuntime.cameraStatus.warnings.length + deviceRuntime.visionStatus.warnings.length + deviceRuntime.inventory.warnings.length" />
      </DashboardCard>

      <DashboardCard title="Vision Status" subtitle="Advisory telemetry only">
        <MetricRow label="Camera" :value="`${vision.cameraStatus.camera_mode} / ${vision.cameraStatus.connected}`" />
        <MetricRow label="Vision" :value="vision.visionStatus.running ? 'running' : 'stopped'" />
        <MetricRow label="FPS" :value="vision.visionStatus.fps" />
        <MetricRow label="Body count" :value="effectiveBodyCount" />
        <MetricRow label="Balloon count" :value="effectiveBalloonCount" />
        <MetricRow label="Latency" :value="`${vision.visionStatus.latest_latency_ms} ms`" />
        <MetricRow label="Warning" :value="vision.warning" />
      </DashboardCard>
    </div>

    <DashboardCard title="Data Collection" subtitle="Phase 9 session and model status">
      <div class="grid gap-4 md:grid-cols-2">
        <div>
          <MetricRow label="Active session" :value="dataLab.activeSession?.session_id ?? 'none'" />
          <MetricRow label="Sessions" :value="dataLab.sessions.length" />
          <MetricRow label="Snapshots" :value="dataLab.health?.total_images ?? 0" />
          <MetricRow label="Data Lab replay" :value="dataLab.dataLabReplay?.replay_status ?? dataLab.dataLabStatus?.replay_status ?? 'replay_execution_not_implemented'" />
          <MetricRow label="Annotation review" :value="(dataLab.dataLabDatasetHealth?.annotation_candidates ?? 0) > 0 ? 'annotation_review_foundation_ready' : 'annotation_candidates_pending'" />
        </div>
        <div>
          <MetricRow label="Active model" :value="dataLab.activeModels.active_combined_model_id ?? dataLab.activeModels.active_test_adapter ?? 'none'" />
          <MetricRow label="Latest export" :value="dataLab.exportResult?.dataset_id ?? 'none'" />
          <MetricRow label="Health warnings" :value="(dataLab.health?.missing_metadata_warnings.length ?? 0) + (dataLab.health?.recommendations.length ?? 0)" />
          <MetricRow label="Dataset ready for training" :value="dataLab.dataLabDatasetHealth?.dataset_ready_for_training ?? false" />
          <MetricRow label="Dataset health reason" :value="dataLab.dataLabDatasetHealth?.reason ?? 'only mock/surrogate evidence or insufficient real data'" />
        </div>
      </div>
      <div class="mt-3 flex flex-wrap gap-2">
        <StatusBadge label="DATA CAPTURE ONLY" tone="warn" />
        <StatusBadge label="NO PHYSICAL COMMAND" tone="bad" />
      </div>
    </DashboardCard>

    <DashboardCard title="Demo Evidence" subtitle="End-to-end jury/operator evidence timeline">
      <div class="grid gap-4 md:grid-cols-2">
        <div>
          <MetricRow label="Latest demo run" :value="demo.timeline.run_id" />
          <MetricRow label="Timeline steps" :value="demo.timeline.events.length" />
          <MetricRow label="Release demo ready" :value="demo.timeline.verdict.release_demo_ready" />
          <MetricRow label="Release blockers" :value="demo.timeline.verdict.release_demo_blockers.length" />
        </div>
        <div>
          <MetricRow label="Competition ready" :value="demo.timeline.verdict.competition_ready" />
          <MetricRow label="Competition blockers" :value="demo.timeline.verdict.competition_blockers.length" />
          <MetricRow label="Dataset ready for training" :value="demo.timeline.verdict.dataset_ready_for_training" />
          <MetricRow label="Dataset blockers" :value="demo.timeline.verdict.dataset_blockers.length" />
          <MetricRow label="No physical command" :value="demo.timeline.no_physical_command_generated" />
        </div>
      </div>
      <div class="mt-3 flex flex-wrap gap-2">
        <StatusBadge label="DEMO / EVIDENCE ONLY" tone="warn" />
        <StatusBadge label="NO PHYSICAL COMMAND" tone="bad" />
      </div>
    </DashboardCard>

    <DashboardCard title="Jury Demo Summary" subtitle="Clean-room and rehearsal evidence">
      <div class="grid gap-4 md:grid-cols-2">
        <div>
          <MetricRow label="Latest rehearsal" :value="demo.juryRehearsal?.rehearsal_id ?? 'not run'" />
          <MetricRow label="Release demo ready" :value="demo.juryRehearsal?.verdict?.release_demo_ready ?? demo.timeline.verdict.release_demo_ready" />
          <MetricRow label="Competition ready" :value="demo.juryRehearsal?.verdict?.competition_ready ?? false" />
          <MetricRow label="Clean-room verified" :value="release.latestCleanroom?.smoke_status === 'passed'" />
        </div>
        <div>
          <MetricRow label="Latest release package" :value="release.latestPackage?.package_id ?? 'not generated'" />
          <MetricRow label="Clean-room run" :value="release.latestCleanroom?.run_id ?? 'not run'" />
          <MetricRow label="Endpoints passed" :value="release.latestCleanroom ? `${release.latestCleanroom.endpoints_passed}/${release.latestCleanroom.endpoints_total}` : '0/0'" />
          <MetricRow label="no_physical_command_generated" :value="demo.juryRehearsal?.no_physical_command_generated ?? true" />
        </div>
      </div>
      <div class="mt-3 flex flex-wrap gap-2">
        <StatusBadge label="DEMO/RELEASE EVIDENCE" tone="warn" />
        <StatusBadge label="NO PHYSICAL COMMAND" tone="bad" />
      </div>
    </DashboardCard>

    <DashboardCard title="Self-Test / Readiness" subtitle="Phase 10 acceptance status">
      <div class="grid gap-4 md:grid-cols-2">
        <div>
          <MetricRow label="Latest status" :value="selfTest.latestRun?.status ?? 'not run'" />
          <MetricRow label="Readiness" :value="selfTest.latestRun?.readiness_level ?? 'not_ready'" />
          <MetricRow label="Last run" :value="selfTest.latestRun?.ended_at ? new Date(selfTest.latestRun.ended_at * 1000).toLocaleString() : 'not run'" />
        </div>
        <div>
          <MetricRow label="Critical failures" :value="selfTest.latestRun?.summary.critical_failures ?? 0" />
          <MetricRow label="Warnings" :value="selfTest.latestRun?.summary.warning ?? 0" />
          <MetricRow label="No physical command" :value="selfTest.latestRun?.no_physical_command_generated ?? true" />
        </div>
      </div>
      <div class="mt-3 flex flex-wrap gap-2">
        <StatusBadge label="SELF-TEST DOES NOT ARM FIRE" tone="bad" />
        <StatusBadge :label="selfTest.latestRun?.overall_ready ? 'DEMO READY' : 'NOT READY'" :tone="selfTest.latestRun?.overall_ready ? 'good' : 'warn'" />
      </div>
    </DashboardCard>

    <DashboardCard title="Reports / KTR" subtitle="Phase 11 export status">
      <div class="grid gap-4 md:grid-cols-2">
        <div>
          <MetricRow label="Exports" :value="reports.status.exports_count" />
          <MetricRow label="Latest export" :value="reports.latestExport?.export_id ?? 'none'" />
          <MetricRow label="Latest type" :value="reports.latestExport?.kind ?? 'none'" />
        </div>
        <div>
          <MetricRow label="Output root" :value="reports.status.root_dir" />
          <MetricRow label="Status" :value="reports.latestExport?.status ?? 'idle'" />
          <MetricRow label="No physical command" :value="reports.status.no_physical_command_generated" />
        </div>
      </div>
      <div class="mt-3 flex flex-wrap gap-2">
        <StatusBadge label="KTR EXPORT READY" tone="good" />
        <StatusBadge label="REPORTS DO NOT ARM FIRE" tone="bad" />
      </div>
    </DashboardCard>

    <DashboardCard title="Release Readiness" subtitle="Portable mode, first-run and KTR inventory">
      <div class="grid gap-4 md:grid-cols-2">
        <div>
          <MetricRow label="Environment" :value="firstRun.status.mode" />
          <MetricRow label="Readiness profile" :value="firstRun.currentProfileId" />
          <MetricRow label="First run" :value="firstRun.displayStatus" />
          <MetricRow label="Profile evaluation" :value="firstRun.currentProfileEvaluationStatus" />
          <MetricRow label="Stale evidence" :value="firstRun.status.stale_evidence" />
          <MetricRow label="Checks" :value="firstRun.status.checks_count" />
          <MetricRow label="KTR export readiness" :value="reports.status.exports_count > 0 ? 'export evidence available' : 'export not generated'" />
          <MetricRow label="Launcher available" :value="release.status.launcher_available" />
          <MetricRow label="Frontend static" :value="release.status.frontend_static_available" />
          <MetricRow label="Release candidate preflight" :value="release.status.status" />
          <MetricRow label="Cold-start status" :value="release.status.status" />
          <MetricRow label="Release manifest" :value="releaseManifestLabel" />
        </div>
        <div>
          <MetricRow label="Interface inventory" :value="`${interfaces.inventory.interfaces.length} interfaces`" />
          <MetricRow label="Selected camera" :value="deviceRuntime.cameraStatus.selected_camera" />
          <MetricRow label="Active adapter" :value="deviceRuntime.visionStatus.profile.inference_adapter" />
          <MetricRow label="Pico candidate / verified" :value="`${deviceRuntime.inventory.pico_candidates.length} / ${hardware.status.pico_verified}`" />
          <MetricRow label="Runtime dirs writable" :value="release.status.writable_runtime_dirs" />
          <MetricRow label="Pico candidates" :value="release.status.pico_candidate_count" />
          <MetricRow label="Camera devices" :value="release.status.camera_devices_detected" />
          <MetricRow label="Cold-start camera source" :value="String(release.status.cold_start_evidence.camera_source ?? 'unknown')" />
          <MetricRow label="Cold-start model kind" :value="String(release.status.cold_start_evidence.active_model_kind ?? 'unknown')" />
          <MetricRow label="Cold-start Pico state" :value="String(release.status.cold_start_evidence.pico_state ?? 'unknown')" />
          <MetricRow label="Field profile" :value="deviceProfile.active?.verification_status ?? 'not_verified'" />
        </div>
      </div>
      <p class="mt-3 rounded-md border border-cyan-400/30 bg-cyan-400/10 px-3 py-2 text-sm text-cyan-100">
        Release candidate can pass on a first-install computer without hardware. Competition rehearsal remains separate and requires production YOLO, verified Pico telemetry and a real camera profile.
      </p>
      <div class="mt-3 flex flex-wrap gap-2">
        <StatusBadge :label="`RELEASE PREFLIGHT: ${release.status.status.toUpperCase()}`" :tone="release.status.status === 'passed' ? 'good' : release.status.status === 'failed' ? 'bad' : 'warn'" />
        <StatusBadge :label="`COMPETITION: ${String(competitionStatus).toUpperCase()}`" :tone="competitionStatus === 'passed' ? 'good' : competitionStatus === 'blocked' || competitionStatus === 'failed' ? 'bad' : 'warn'" />
        <StatusBadge label="REPORTS DO NOT ENABLE HARDWARE" tone="bad" />
      </div>
    </DashboardCard>

    <DashboardCard title="Motion Status" subtitle="Phase 7 dry-run only">
      <div class="grid gap-4 md:grid-cols-2">
        <div>
          <MetricRow label="State" :value="motion.state.motion_state" />
          <MetricRow label="Pan / Tilt" :value="`${motion.state.pan_position_deg.toFixed(1)} / ${motion.state.tilt_position_deg.toFixed(1)} deg`" />
          <MetricRow label="dry_run" :value="motion.state.dry_run" />
        </div>
        <div>
          <MetricRow label="Last command" :value="motion.state.last_command" />
          <MetricRow label="Last error" :value="motion.state.last_error" />
          <MetricRow label="No physical movement" value="true" />
        </div>
      </div>
    </DashboardCard>

    <SafetyGatesPanel :system="store.systemState" :safety="store.safetyState" />

    <DashboardCard title="Recent Events" subtitle="Latest WebSocket envelopes">
      <div class="grid gap-2">
        <div
          v-for="event in store.latestEvents"
          :key="`${event.seq}-${event.type}`"
          class="grid gap-2 rounded-md border border-white/8 bg-black/18 p-3 text-sm md:grid-cols-[120px_1fr_90px]"
        >
          <span class="font-mono text-cyan-200">{{ event.type }}</span>
          <span class="text-slate-200">{{ event.summary }}</span>
          <span class="text-right font-mono text-slate-500">#{{ event.seq }}</span>
        </div>
        <p v-if="store.latestEvents.length === 0" class="text-sm text-slate-400">
          Waiting for backend telemetry.
        </p>
      </div>
    </DashboardCard>
  </div>
</template>
