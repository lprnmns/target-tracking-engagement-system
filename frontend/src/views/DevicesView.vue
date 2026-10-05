<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import DashboardCard from '../components/dashboard/DashboardCard.vue'
import MetricRow from '../components/dashboard/MetricRow.vue'
import StatusBadge from '../components/shared/StatusBadge.vue'
import { useDeviceRuntimeStore } from '../stores/deviceRuntimeStore'
import { useDeviceProfileStore } from '../stores/deviceProfileStore'
import { useReleaseStore } from '../stores/releaseStore'
import { useModelPackageStore } from '../stores/modelPackageStore'

const devices = useDeviceRuntimeStore()
const profile = useDeviceProfileStore()
const release = useReleaseStore()
const modelPackages = useModelPackageStore()
const showLowRelevancePorts = ref(false)
const warningsCount = computed(() => devices.inventory.devices.reduce((total, item) => total + item.warnings.length, 0))
const visibleSerial = computed(() => devices.inventory.serial.filter((item) => {
  if (showLowRelevancePorts.value) return true
  const lowScore = item.candidate_score < 25
  const ttyS = item.device_path.includes('/dev/ttyS')
  return !(lowScore || ttyS)
}))

onMounted(() => {
  void devices.refresh()
  void profile.refresh()
  void release.refresh()
  void modelPackages.refresh()
})

function toneForPermission(ok: boolean): 'good' | 'bad' {
  return ok ? 'good' : 'bad'
}

function cameraBadge(camera: { device_path: string; candidate_score: number; permissions_ok: boolean; busy: boolean; warnings: string[] }): string {
  if (!camera.permissions_ok) return 'permission denied'
  if (camera.busy) return 'busy'
  if (camera.device_path.includes('/dev/video0')) return 'laptop'
  if (camera.candidate_score >= 40) return 'recommended'
  return 'usb'
}

function humanStatus(value: string | null | undefined): string {
  if (!value) return 'not saved'
  const labels: Record<string, string> = {
    not_verified: 'Not verified',
    mock_verified: 'Mock verified',
    demo_verified: 'Demo verified',
    hardware_readonly_verified: 'Hardware read-only verified',
    hardware_pending: 'Hardware pending',
    camera_pending: 'Camera pending',
    pico_pending: 'Pico pending',
    model_pending: 'Model pending',
    competition_not_verified: 'Competition not verified',
    mismatch: 'Profile mismatch',
    'mock/demo only': 'Mock/demo only',
    test_adapter_only: 'Test adapter only',
    production_model_loaded: 'Production model loaded',
    fixture_test_adapter: 'Fixture/test adapter only',
    model_imported: 'Model imported',
    model_validated: 'Model validated',
    model_active: 'Model active',
    model_test_passed: 'Model test passed',
    production_model_verified: 'Production model verified',
    pico_readonly_verified: 'Pico read-only verified',
    camera_hardware_candidate: 'Camera hardware candidate',
    'demo adapter': 'Demo adapter',
  }
  return labels[value] ?? value.replaceAll('_', ' ')
}

function statusTone(value: string | null | undefined): 'good' | 'warn' | 'bad' | 'neutral' {
  if (!value) return 'neutral'
  if (['demo_verified', 'mock_verified', 'hardware_readonly_verified', 'production_model_loaded', 'pico_readonly_verified'].includes(value)) return 'good'
  if (value === 'fixture_test_adapter') return 'warn'
  if (['model_active', 'model_test_passed', 'model_validated'].includes(value)) return 'good'
  if (['mismatch', 'competition_not_verified'].includes(value)) return 'bad'
  return 'warn'
}

function probeStatus(): string {
  const status = devices.cameraStatus.last_probe_result?.status
  if (typeof status === 'string') return status
  return devices.cameraStatus.profile.source_type === 'mock' ? 'mock/demo only' : 'not probed'
}
</script>

<template>
  <div class="grid gap-4">
    <div class="rounded-md border border-red-400/30 bg-red-500/10 px-4 py-3 text-sm text-red-100">
      Device Manager is discovery-only. It does not enable GPIO, motor, servo, trigger, fire, STEP/DIR/PWM or physical command output.
    </div>

    <div class="grid gap-4 xl:grid-cols-4">
      <DashboardCard title="Inventory" subtitle="USB, serial and camera scan">
        <MetricRow label="Devices" :value="devices.inventory.devices.length" />
        <MetricRow label="Cameras" :value="devices.inventory.cameras.length" />
        <MetricRow label="Serial" :value="devices.inventory.serial.length" />
        <MetricRow label="Warnings" :value="warningsCount" />
        <button class="focus-ring mt-4 rounded-md bg-cyan-500 px-3 py-2 text-sm font-semibold text-slate-950" @click="devices.refreshInventory">
          Refresh devices
        </button>
      </DashboardCard>

      <DashboardCard title="Pico Candidates" subtitle="Candidate is not verified">
        <MetricRow label="Candidate count" :value="devices.inventory.pico_candidates.length" />
        <MetricRow label="Verified state" value="Telemetry verification required" />
        <div class="mt-3 flex flex-wrap gap-2">
          <StatusBadge label="CANDIDATE != VERIFIED" tone="warn" />
          <StatusBadge label="READ-ONLY ONLY" tone="bad" />
        </div>
      </DashboardCard>

      <DashboardCard title="Field Profile" subtitle="Persistent device binding">
        <MetricRow label="Active profile" :value="profile.active?.profile_id ?? 'default'" />
        <div class="mt-2 grid gap-2">
          <div class="flex flex-wrap items-center justify-between gap-2 border-t border-white/8 py-2 text-sm">
            <span class="text-slate-400">Verification level</span>
            <StatusBadge :label="humanStatus(profile.active?.verification_level ?? profile.active?.verification_status)" :tone="statusTone(profile.active?.verification_status)" />
          </div>
          <div class="flex flex-wrap items-center justify-between gap-2 border-t border-white/8 py-2 text-sm">
            <span class="text-slate-400">Camera binding</span>
            <StatusBadge :label="humanStatus(profile.active?.camera_binding_status)" :tone="statusTone(profile.active?.camera_binding_status)" />
          </div>
          <div class="flex flex-wrap items-center justify-between gap-2 border-t border-white/8 py-2 text-sm">
            <span class="text-slate-400">Pico binding</span>
            <StatusBadge :label="humanStatus(profile.active?.pico_binding_status)" :tone="statusTone(profile.active?.pico_binding_status)" />
          </div>
          <div class="flex flex-wrap items-center justify-between gap-2 border-t border-white/8 py-2 text-sm">
            <span class="text-slate-400">Model binding</span>
            <StatusBadge :label="modelPackages.activePackage ? humanStatus(modelPackages.activePackage.metadata?.production_ready ? 'production_model_loaded' : 'fixture_test_adapter') : humanStatus(profile.active?.model_binding_status)" :tone="statusTone(modelPackages.activePackage ? (modelPackages.activePackage.metadata?.production_ready ? 'production_model_loaded' : 'fixture_test_adapter') : profile.active?.model_binding_status)" />
          </div>
          <div class="flex flex-wrap items-center justify-between gap-2 border-t border-white/8 py-2 text-sm">
            <span class="text-slate-400">Competition status</span>
            <StatusBadge :label="humanStatus(profile.active?.competition_status)" :tone="statusTone(profile.active?.competition_status)" />
          </div>
        </div>
        <MetricRow label="Camera" :value="profile.active?.selected_camera_name ?? 'not saved'" />
        <div class="mt-3 flex flex-wrap gap-2">
          <button class="focus-ring rounded-md border border-cyan-400/40 bg-cyan-400/12 px-3 py-2 text-xs font-semibold text-cyan-100" @click="profile.save()">Save as active field profile</button>
          <button class="focus-ring rounded-md border border-amber-400/40 bg-amber-400/12 px-3 py-2 text-xs font-semibold text-amber-100" @click="profile.verify()">Verify active profile</button>
        </div>
        <div v-if="profile.lastResult?.mismatch_warnings.length" class="mt-3 rounded-md border border-amber-400/30 bg-amber-400/10 p-2 text-xs text-amber-100">
          <p v-for="warning in profile.lastResult.mismatch_warnings" :key="warning">{{ warning }}</p>
        </div>
      </DashboardCard>

      <DashboardCard title="Release Binding" subtitle="Portable first-install device readiness">
        <MetricRow label="Selected camera" :value="devices.cameraStatus.selected_camera" />
        <MetricRow label="Stable camera path" :value="profile.active?.selected_camera_stable_path ?? 'not saved'" />
        <MetricRow label="Camera probe result" :value="probeStatus()" />
        <MetricRow label="Pico candidates" :value="release.status.pico_candidate_count" />
        <MetricRow label="Pico state" :value="profile.active?.pico_binding_status ?? 'pico_pending'" />
        <MetricRow label="Model binding" :value="modelPackages.activePackage ? `${modelPackages.activePackage.model_id} · ${modelPackages.activePackage.validation?.class_mapping_status ?? 'not_validated'}` : profile.active?.model_binding_status ?? 'model_pending'" />
        <MetricRow label="Runtime preset binding" :value="modelPackages.activePackage?.thresholds?.recommended_runtime_preset ?? 'not saved'" />
        <MetricRow label="Profile verification" :value="profile.active?.verification_status ?? 'not_verified'" />
        <div class="mt-3 flex flex-wrap gap-2">
          <StatusBadge label="RELEASE CAN RUN WITHOUT HARDWARE" tone="warn" />
          <StatusBadge label="PHYSICAL COMMANDS DISABLED" tone="bad" />
        </div>
      </DashboardCard>

      <DashboardCard title="Camera Runtime" subtitle="Selected source profile">
        <MetricRow label="Source" :value="devices.cameraStatus.profile.source_type" />
        <MetricRow label="Selected" :value="devices.cameraStatus.selected_camera" />
        <MetricRow label="Requested" :value="`${devices.cameraStatus.requested_width}x${devices.cameraStatus.requested_height}@${devices.cameraStatus.requested_fps} ${devices.cameraStatus.requested_pixel_format}`" />
        <MetricRow label="Actual" :value="`${devices.cameraStatus.actual_width}x${devices.cameraStatus.actual_height}@${devices.cameraStatus.actual_fps_measured} ${devices.cameraStatus.actual_pixel_format}`" />
        <MetricRow label="Last apply" :value="devices.cameraStatus.last_apply_ok ? 'ok' : 'failed'" />
      </DashboardCard>

      <DashboardCard title="Vision Runtime" subtitle="Adapter and YOLO parameters">
        <MetricRow label="Adapter" :value="devices.visionStatus.profile.inference_adapter" />
        <MetricRow label="Device" :value="`${devices.visionStatus.requested_device} → ${devices.visionStatus.resolved_device ?? 'BLOCKED'}`" />
        <MetricRow label="Device reason" :value="devices.visionStatus.device_reason" />
        <MetricRow label="CUDA host" :value="devices.visionStatus.cuda_available ? 'available' : 'unavailable'" />
        <MetricRow label="imgsz" :value="devices.visionStatus.profile.imgsz" />
        <MetricRow label="conf/iou" :value="`${devices.visionStatus.profile.conf} / ${devices.visionStatus.profile.iou}`" />
      </DashboardCard>
    </div>

    <div class="grid gap-4 xl:grid-cols-2">
      <DashboardCard title="Camera Devices" subtitle="Probe before selecting real camera">
        <div class="overflow-x-auto">
          <table class="w-full min-w-[820px] text-left text-sm">
            <thead class="text-xs uppercase tracking-[0.14em] text-slate-500">
              <tr><th class="py-2">Path</th><th>Name</th><th>Stable path</th><th>Badge</th><th>Permission</th><th>Action</th></tr>
            </thead>
            <tbody>
              <tr v-for="camera in devices.inventory.cameras" :key="camera.device_id" class="border-t border-white/8">
                <td class="py-2 font-mono text-cyan-100">{{ camera.device_path }}</td>
                <td>{{ camera.description }}</td>
                <td class="font-mono text-xs text-slate-400">{{ camera.stable_path ?? 'none' }}</td>
                <td><StatusBadge :label="cameraBadge(camera)" :tone="camera.permissions_ok && !camera.busy ? 'good' : 'warn'" /></td>
                <td><StatusBadge :label="camera.permissions_ok ? 'ok' : 'denied'" :tone="toneForPermission(camera.permissions_ok)" /></td>
                <td><button class="focus-ring rounded-md bg-slate-700 px-2 py-1 text-xs font-semibold text-white" @click="devices.probe(camera.device_id)">Probe</button></td>
              </tr>
            </tbody>
          </table>
          <p v-if="devices.inventory.cameras.length === 0" class="py-3 text-sm text-slate-400">No camera devices found. Mock camera remains available.</p>
        </div>
      </DashboardCard>

      <DashboardCard title="Serial Devices" subtitle="Pico candidate scoring">
        <div class="mb-3 flex items-center justify-between gap-3">
          <p class="text-xs text-slate-400">Low relevance ports such as /dev/ttyS* are collapsed by default.</p>
          <label class="flex items-center gap-2 text-xs text-slate-300">
            <input v-model="showLowRelevancePorts" type="checkbox" class="accent-cyan-400" />
            Show all low relevance ports
          </label>
        </div>
        <div class="overflow-x-auto">
          <table class="w-full min-w-[860px] text-left text-sm">
            <thead class="text-xs uppercase tracking-[0.14em] text-slate-500">
              <tr><th class="py-2">Path</th><th>Description</th><th>Kind</th><th>Score</th><th>Permission</th><th>Warning</th></tr>
            </thead>
            <tbody>
              <tr v-for="serial in visibleSerial" :key="serial.device_id" class="border-t border-white/8">
                <td class="py-2 font-mono text-cyan-100">{{ serial.device_path }}</td>
                <td>{{ serial.description }}</td>
                <td><StatusBadge :label="serial.kind" :tone="serial.kind === 'pico_candidate' ? 'good' : 'neutral'" /></td>
                <td>
                  {{ serial.candidate_score }}
                  <p class="text-xs text-slate-500">VID/PID, description, hwid and path score</p>
                </td>
                <td><StatusBadge :label="serial.permissions_ok ? 'ok' : 'denied'" :tone="toneForPermission(serial.permissions_ok)" /></td>
                <td class="text-amber-100">{{ serial.warnings[0] ?? serial.suggested_action ?? 'none' }}</td>
              </tr>
            </tbody>
          </table>
          <p v-if="visibleSerial.length === 0" class="py-3 text-sm text-slate-400">No relevant serial devices shown. Enable the toggle to show low relevance ports.</p>
        </div>
      </DashboardCard>
    </div>

    <DashboardCard title="Latest Probe / Apply Result" subtitle="Operator diagnostics">
      <pre class="max-h-[360px] overflow-auto rounded-md bg-black/30 p-3 text-xs text-cyan-100">{{ JSON.stringify(devices.lastCameraResult ?? devices.lastVisionResult ?? devices.inventory.warnings, null, 2) }}</pre>
    </DashboardCard>
  </div>
</template>
