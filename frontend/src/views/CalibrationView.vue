<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import DashboardCard from '../components/dashboard/DashboardCard.vue'
import MetricRow from '../components/dashboard/MetricRow.vue'
import StatusBadge from '../components/shared/StatusBadge.vue'
import { useCalibrationStore } from '../stores/calibrationStore'
import type { CameraCalibrationConfig } from '../types/calibration'

const calibration = useCalibrationStore()
const configDraft = ref<CameraCalibrationConfig>({ ...calibration.status.config })
const pointLabel = ref('5m reference')
const worldX = ref(0)
const worldY = ref(5)
const imageX = ref(320)
const imageY = ref(180)
const fovDistance = ref(15)
const objectWidth = ref(0.5)
const directionTarget = ref<'left' | 'right' | 'up' | 'down' | 'center'>('right')
const observedX = ref<'camera_left' | 'camera_right' | 'camera_up' | 'camera_down' | 'no_motion' | 'unknown'>('camera_right')
const observedY = ref<'camera_left' | 'camera_right' | 'camera_up' | 'camera_down' | 'no_motion' | 'unknown'>('camera_up')
const observationNote = ref('Operator observation is advisory; no motor command was sent.')
const streamPreviewWidth = 640
const yoloInferenceWidth = 960
const lensProfiles = [
  { lens: '3.6mm', hfov: 70 },
  { lens: '8mm', hfov: 35 },
  { lens: '12mm', hfov: 22 },
]

const estimates = computed(() => [5, 10, 15].map((distance) => {
  const visibleWidth = 2 * distance * Math.tan((configDraft.value.hfov_deg * Math.PI / 180) / 2)
  const objectPixels = objectWidth.value / visibleWidth * configDraft.value.resolution_width
  return {
    distance,
    visibleWidth: visibleWidth.toFixed(2),
    objectPixels: objectPixels.toFixed(1),
    level: objectPixels >= 120 ? 'good' : objectPixels >= 60 ? 'marginal' : 'poor',
  }
}))
const lensComparison = computed(() => lensProfiles.map((profile) => ({
  lens: profile.lens,
  hfov: profile.hfov,
  values: [5, 10, 15].map((distance) => {
    const visibleWidth = 2 * distance * Math.tan((profile.hfov * Math.PI / 180) / 2)
    const capturePixels = objectWidth.value / visibleWidth * configDraft.value.resolution_width
    const yoloPixels = objectWidth.value / visibleWidth * yoloInferenceWidth
    const captureLevel = capturePixels >= 120 ? 'good' : capturePixels >= 60 ? 'marginal' : 'poor'
    const yoloLevel = yoloPixels >= 120 ? 'good' : yoloPixels >= 60 ? 'marginal' : 'poor'
    return {
      distance,
      capturePixels: capturePixels.toFixed(1),
      yoloPixels: yoloPixels.toFixed(1),
      captureLevel,
      yoloLevel,
    }
  }),
})))

function cloneConfig(config: CameraCalibrationConfig): CameraCalibrationConfig {
  return JSON.parse(JSON.stringify(config)) as CameraCalibrationConfig
}

function toneFor(level: string): 'good' | 'warn' | 'bad' {
  if (level === 'good' || level === 'valid') return 'good'
  if (level === 'marginal' || level === 'partial') return 'warn'
  return 'bad'
}

async function addPoint(): Promise<void> {
  await calibration.addPoint({
    label: pointLabel.value,
    world_x_m: worldX.value,
    world_y_m: worldY.value,
    image_x_px: imageX.value,
    image_y_px: imageY.value,
  })
}

async function simulateDirection(): Promise<void> {
  await calibration.simulate({
    target_position: directionTarget.value,
    frame_width: calibration.status.config.resolution_width,
    frame_height: calibration.status.config.resolution_height,
  })
}

async function recordXObservation(): Promise<void> {
  await calibration.recordObservation({
    simulated_axis: 'x',
    system_expected_motion: 'camera_right',
    operator_observed_motion: observedX.value,
    operator_confidence: 'confirmed',
    note: observationNote.value,
  })
}

async function recordYObservation(): Promise<void> {
  await calibration.recordObservation({
    simulated_axis: 'y',
    system_expected_motion: 'camera_up',
    operator_observed_motion: observedY.value,
    operator_confidence: 'confirmed',
    note: observationNote.value,
  })
}

onMounted(async () => {
  await calibration.refresh()
  configDraft.value = cloneConfig(calibration.status.config)
})

watch(
  () => calibration.status.config,
  (nextConfig) => {
    configDraft.value = cloneConfig(nextConfig)
  },
  { deep: true },
)
</script>

<template>
  <div class="grid gap-4">
    <div class="rounded-md border border-amber-400/30 bg-amber-400/10 px-4 py-3 text-sm text-amber-100">
      Field calibration required. HFOV values are estimates and must be validated with real lens calibration.
    </div>

    <div class="grid gap-4 xl:grid-cols-3">
      <DashboardCard title="Camera / Lens Profile" subtitle="Calibration configuration">
        <MetricRow label="Camera" :value="calibration.status.config.camera_name" />
        <MetricRow label="Resolution" :value="`${calibration.status.config.resolution_width}x${calibration.status.config.resolution_height}`" />
        <MetricRow label="FPS" :value="calibration.status.config.fps" />
        <MetricRow label="Lens" :value="calibration.status.config.lens_profile" />
        <MetricRow label="Capture width used by FOV" :value="`${configDraft.resolution_width}px`" />
        <MetricRow label="Stream preview width" :value="`${streamPreviewWidth}px`" />
        <MetricRow label="YOLO inference width" :value="`${yoloInferenceWidth}px`" />
        <MetricRow label="Status" :value="calibration.status.config.calibration_status" />
      </DashboardCard>

      <DashboardCard title="Geometry Inputs" subtitle="Camera/table/target heights">
        <MetricRow label="Camera height" :value="`${calibration.status.config.camera_height_cm} cm`" />
        <MetricRow label="Target height" :value="`${calibration.status.config.target_height_cm} cm`" />
        <MetricRow label="Table height" :value="`${calibration.status.config.table_height_cm} cm`" />
        <MetricRow label="HFOV" :value="`${calibration.status.config.hfov_deg} deg`" />
        <MetricRow label="VFOV" :value="calibration.status.config.vfov_deg === null ? 'not set' : `${calibration.status.config.vfov_deg} deg`" />
      </DashboardCard>

      <DashboardCard title="Homography Status" subtitle="Point validation">
        <MetricRow label="Valid" :value="calibration.status.valid" />
        <MetricRow label="Points" :value="calibration.status.calibration_points.length" />
        <MetricRow label="RANSAC inliers" :value="calibration.status.inlier_count" />
        <MetricRow label="Reprojection" :value="calibration.status.reprojection_error_px === null ? 'not available' : `${calibration.status.reprojection_error_px}px`" />
        <MetricRow label="Mapping" :value="calibration.status.homography_direction" />
        <MetricRow label="Profile hash" :value="calibration.status.calibration_hash?.slice(0, 12) ?? 'not computed'" />
        <div class="mt-3 flex flex-wrap gap-2">
          <StatusBadge :label="calibration.status.config.calibration_status" :tone="toneFor(calibration.status.config.calibration_status)" />
          <StatusBadge :label="calibration.status.config.homography_enabled ? 'HOMOGRAPHY ON' : 'HOMOGRAPHY OFF'" tone="warn" />
        </div>
      </DashboardCard>
    </div>

    <div class="grid gap-4 xl:grid-cols-[1.2fr_1fr_1fr]">
      <DashboardCard title="Direction Simulator" subtitle="Advisory image-to-camera semantics">
        <div class="grid gap-3">
          <label class="grid gap-1 text-xs text-slate-400">Target appears on screen
            <select v-model="directionTarget" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm text-white">
              <option value="left">left</option>
              <option value="right">right</option>
              <option value="up">up</option>
              <option value="down">down</option>
              <option value="center">center</option>
            </select>
          </label>
          <button class="focus-ring rounded-md bg-cyan-500 px-3 py-2 text-sm font-semibold text-slate-950" @click="simulateDirection">Simulate direction</button>
        </div>
        <svg :viewBox="`0 0 ${calibration.status.config.resolution_width} ${calibration.status.config.resolution_height}`" class="mt-4 h-56 w-full rounded-md border border-white/10 bg-black/24">
          <line :x1="calibration.status.config.resolution_width / 2" y1="0" :x2="calibration.status.config.resolution_width / 2" :y2="calibration.status.config.resolution_height" stroke="#334155" />
          <line x1="0" :y1="calibration.status.config.resolution_height / 2" :x2="calibration.status.config.resolution_width" :y2="calibration.status.config.resolution_height / 2" stroke="#334155" />
          <circle :cx="calibration.status.config.resolution_width / 2" :cy="calibration.status.config.resolution_height / 2" r="7" fill="#22d3ee" />
          <circle :cx="calibration.directionSimulation?.target_center_x ?? calibration.status.config.resolution_width * 0.75" :cy="calibration.directionSimulation?.target_center_y ?? calibration.status.config.resolution_height / 2" r="11" fill="#f59e0b" />
        </svg>
        <MetricRow label="Target visual side" :value="calibration.directionSimulation?.target_visual_side ?? 'not_simulated'" />
        <MetricRow label="Required camera motion" :value="calibration.directionSimulation?.required_camera_motion ?? 'not_simulated'" />
        <MetricRow label="Expected image response" :value="calibration.directionSimulation?.expected_image_response ?? 'not_simulated'" />
        <MetricRow label="physical_command_enabled" :value="calibration.directionStatus.physical_command_enabled" />
        <StatusBadge label="no_physical_command_generated=true" tone="good" />
      </DashboardCard>

      <DashboardCard title="Operator Observation" subtitle="Record observed motion without commanding hardware">
        <label class="grid gap-1 text-xs text-slate-400">Observed X axis motion
          <select v-model="observedX" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm text-white">
            <option value="camera_right">camera_right</option>
            <option value="camera_left">camera_left</option>
            <option value="camera_up">camera_up</option>
            <option value="camera_down">camera_down</option>
            <option value="no_motion">no_motion</option>
            <option value="unknown">unknown</option>
          </select>
        </label>
        <label class="mt-3 grid gap-1 text-xs text-slate-400">Observed Y axis motion
          <select v-model="observedY" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm text-white">
            <option value="camera_up">camera_up</option>
            <option value="camera_down">camera_down</option>
            <option value="camera_right">camera_right</option>
            <option value="camera_left">camera_left</option>
            <option value="no_motion">no_motion</option>
            <option value="unknown">unknown</option>
          </select>
        </label>
        <textarea v-model="observationNote" class="mt-3 min-h-24 w-full rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm text-white" />
        <div class="mt-3 flex flex-wrap gap-2">
          <button class="focus-ring rounded-md bg-cyan-500 px-3 py-2 text-xs font-semibold text-slate-950" @click="recordXObservation">Record X observation</button>
          <button class="focus-ring rounded-md bg-cyan-500 px-3 py-2 text-xs font-semibold text-slate-950" @click="recordYObservation">Record Y observation</button>
        </div>
        <MetricRow label="Latest observation" :value="calibration.directionStatus.latest_observation?.observation_id ?? 'none'" />
        <MetricRow label="Axis swap suspected" :value="calibration.directionStatus.latest_observation?.axis_swap_suspected ?? false" />
      </DashboardCard>

      <DashboardCard title="Suggested Mapping" subtitle="Advisory calibration profile">
        <MetricRow label="X direction" :value="calibration.directionStatus.profile.x_axis_multiplier === 1 ? 'normal' : 'inverted'" />
        <MetricRow label="Y direction" :value="calibration.directionStatus.profile.y_axis_multiplier === 1 ? 'normal' : 'inverted'" />
        <MetricRow label="Axis swap" :value="calibration.directionStatus.profile.axis_swap" />
        <MetricRow label="camera_mirror_x" :value="calibration.directionStatus.profile.camera_mirror_x" />
        <MetricRow label="camera_mirror_y" :value="calibration.directionStatus.profile.camera_mirror_y" />
        <MetricRow label="Profile source" :value="calibration.directionStatus.profile.source" />
        <MetricRow label="advisory_only" :value="calibration.directionStatus.profile.advisory_only" />
        <MetricRow label="no_physical_command_generated" :value="calibration.directionStatus.profile.no_physical_command_generated" />
        <div class="mt-3 flex flex-wrap gap-2">
          <button class="focus-ring rounded-md bg-emerald-500 px-3 py-2 text-xs font-semibold text-slate-950" @click="calibration.saveDirection">Save profile</button>
          <button class="focus-ring rounded-md bg-red-500 px-3 py-2 text-xs font-semibold text-white" @click="calibration.resetDirection">Reset direction</button>
        </div>
      </DashboardCard>
    </div>

    <DashboardCard title="Direction Safety Boundary" subtitle="Simulation only">
      <div class="grid gap-2 md:grid-cols-2">
        <MetricRow label="No motor command was sent" value="true" />
        <MetricRow label="No serial write was performed" value="true" />
        <MetricRow label="No GPIO/PWM/STEP/DIR path enabled" value="true" />
        <MetricRow label="physical_command_enabled" :value="calibration.directionStatus.physical_command_enabled" />
        <MetricRow label="no_physical_command_generated" :value="calibration.directionStatus.no_physical_command_generated" />
      </div>
    </DashboardCard>

    <DashboardCard title="Editable Calibration Config" subtitle="Backend validated">
      <div class="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        <label class="grid gap-1 text-xs text-slate-400">Lens profile
          <select v-model="configDraft.lens_profile" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm text-white">
            <option value="unknown">unknown</option>
            <option value="3.6mm">3.6mm</option>
            <option value="8mm">8mm</option>
            <option value="12mm">12mm</option>
            <option value="varifocal_custom">varifocal_custom</option>
          </select>
        </label>
        <label class="grid gap-1 text-xs text-slate-400">Width<input v-model.number="configDraft.resolution_width" type="number" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm text-white" /></label>
        <label class="grid gap-1 text-xs text-slate-400">Height<input v-model.number="configDraft.resolution_height" type="number" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm text-white" /></label>
        <label class="grid gap-1 text-xs text-slate-400">FPS<input v-model.number="configDraft.fps" type="number" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm text-white" /></label>
        <label class="grid gap-1 text-xs text-slate-400">Camera height cm<input v-model.number="configDraft.camera_height_cm" type="number" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm text-white" /></label>
        <label class="grid gap-1 text-xs text-slate-400">Target height cm<input v-model.number="configDraft.target_height_cm" type="number" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm text-white" /></label>
        <label class="grid gap-1 text-xs text-slate-400">Table height cm<input v-model.number="configDraft.table_height_cm" type="number" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm text-white" /></label>
        <label class="grid gap-1 text-xs text-slate-400">HFOV deg<input v-model.number="configDraft.hfov_deg" type="number" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm text-white" /></label>
      </div>
      <div class="mt-4 flex flex-wrap gap-3">
        <label class="flex items-center gap-2 text-sm text-slate-300"><input v-model="configDraft.homography_enabled" type="checkbox" /> Homography enabled</label>
        <label class="flex items-center gap-2 text-sm text-slate-300"><input v-model="configDraft.distortion_enabled" type="checkbox" /> Distortion enabled</label>
      </div>
      <button class="focus-ring mt-4 rounded-md bg-cyan-500 px-3 py-2 text-sm font-semibold text-slate-950" @click="calibration.saveConfig(configDraft)">Save Config</button>
    </DashboardCard>

    <div class="grid gap-4 xl:grid-cols-2">
      <DashboardCard title="FOV Estimator" subtitle="Object pixel width">
        <div class="grid gap-3">
          <label class="grid gap-1 text-xs text-slate-400">Distance m<input v-model.number="fovDistance" type="number" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm text-white" /></label>
          <label class="grid gap-1 text-xs text-slate-400">Object width m<input v-model.number="objectWidth" type="number" step="0.01" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm text-white" /></label>
          <button class="focus-ring rounded-md bg-cyan-500 px-3 py-2 text-sm font-semibold text-slate-950" @click="calibration.estimate({ hfov_deg: configDraft.hfov_deg, distance_m: fovDistance, object_width_m: objectWidth, image_width_px: configDraft.resolution_width })">Estimate</button>
          <div v-if="calibration.fovEstimate" class="rounded-md border border-white/8 bg-black/18 p-3">
            <MetricRow label="Visible width" :value="`${calibration.fovEstimate.visible_width_m} m`" />
            <MetricRow label="Object width px" :value="calibration.fovEstimate.object_width_px" />
            <MetricRow label="Warning" :value="calibration.fovEstimate.warning_level" />
          </div>
        </div>
      </DashboardCard>

      <DashboardCard title="5m / 10m / 15m Estimates" subtitle="Lens selection helper">
        <div class="grid gap-2">
          <div v-for="estimate in estimates" :key="estimate.distance" class="grid grid-cols-4 gap-2 rounded-md border border-white/8 bg-black/18 px-3 py-2 text-sm">
            <span>{{ estimate.distance }}m</span>
            <span>{{ estimate.visibleWidth }}m visible</span>
            <span>{{ estimate.objectPixels }}px</span>
            <StatusBadge :label="estimate.level" :tone="toneFor(estimate.level)" />
          </div>
        </div>
      </DashboardCard>
    </div>

    <DashboardCard title="Lens Comparison" subtitle="3.6mm / 8mm / 12mm estimate table">
      <p class="mb-3 text-sm text-slate-400">
        Capture pixel estimates use camera capture width {{ configDraft.resolution_width }}px. YOLO pixel estimates use inference width {{ yoloInferenceWidth }}px. Verdict badges below use capture pixels; thresholds are good >= 120px, marginal 60-119px, poor < 60px. HFOV values are estimated; validate with real lens calibration.
      </p>
      <div class="overflow-x-auto">
        <table class="w-full min-w-[980px] text-left text-sm">
          <thead class="text-xs uppercase tracking-[0.14em] text-slate-500">
            <tr>
              <th class="py-2">Lens</th>
              <th>HFOV</th>
              <th>5m capture / YOLO</th>
              <th>10m capture / YOLO</th>
              <th>15m capture / YOLO</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="profile in lensComparison" :key="profile.lens" class="border-t border-white/8">
              <td class="py-2 font-semibold text-white">{{ profile.lens }}</td>
              <td>{{ profile.hfov }} deg</td>
              <td v-for="value in profile.values" :key="`${profile.lens}-${value.distance}`">
                <div class="grid gap-1">
                  <span class="font-mono text-xs">capture {{ value.capturePixels }}px</span>
                  <span class="font-mono text-xs text-slate-400">YOLO {{ value.yoloPixels }}px</span>
                  <StatusBadge :label="`verdict ${value.captureLevel}`" :tone="toneFor(value.captureLevel)" />
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </DashboardCard>

    <DashboardCard title="Parkur FOV Diagram" subtitle="Placeholder geometry view">
      <svg viewBox="0 0 520 180" class="h-52 w-full rounded-md border border-white/10 bg-black/24">
        <circle cx="260" cy="150" r="8" fill="#22d3ee" />
        <line x1="260" y1="150" x2="90" y2="30" stroke="#334155" stroke-width="2" />
        <line x1="260" y1="150" x2="430" y2="30" stroke="#334155" stroke-width="2" />
        <path d="M 170 86 Q 260 46 350 86" fill="none" stroke="#f59e0b" stroke-width="2" />
        <line x1="80" y1="120" x2="440" y2="120" stroke="#64748b" stroke-dasharray="4 4" />
        <text x="244" y="170" fill="#67e8f9" font-size="12">Camera</text>
        <text x="80" y="28" fill="#94a3b8" font-size="12">HFOV cone</text>
        <text x="228" y="112" fill="#fbbf24" font-size="12">5m / 10m / 15m reference lanes</text>
      </svg>
    </DashboardCard>

    <div class="grid gap-4 xl:grid-cols-[0.8fr_1.2fr]">
      <DashboardCard title="Add Calibration Point" subtitle="World/image mapping">
        <div class="grid gap-3">
          <input v-model="pointLabel" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm text-white" />
          <input v-model.number="worldX" type="number" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm text-white" />
          <input v-model.number="worldY" type="number" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm text-white" />
          <input v-model.number="imageX" type="number" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm text-white" />
          <input v-model.number="imageY" type="number" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm text-white" />
          <button class="focus-ring rounded-md bg-cyan-500 px-3 py-2 text-sm font-semibold text-slate-950" @click="addPoint">Add Point</button>
          <button class="focus-ring rounded-md bg-slate-700 px-3 py-2 text-sm font-semibold text-white" @click="calibration.compute">Compute</button>
          <button class="focus-ring rounded-md bg-red-500 px-3 py-2 text-sm font-semibold text-white" @click="calibration.reset">Reset</button>
        </div>
      </DashboardCard>

      <DashboardCard title="Calibration Point Table" subtitle="5m / 10m / 15m references">
        <div class="overflow-x-auto">
          <table class="w-full min-w-[720px] text-left text-sm">
            <thead class="text-xs uppercase tracking-[0.14em] text-slate-500">
              <tr><th class="py-2">Label</th><th>World</th><th>Image</th><th>Action</th></tr>
            </thead>
            <tbody>
              <tr v-for="point in calibration.status.calibration_points" :key="point.id" class="border-t border-white/8">
                <td class="py-2">{{ point.label }}</td>
                <td class="font-mono text-xs">{{ point.world_x_m }}, {{ point.world_y_m }} m</td>
                <td class="font-mono text-xs">{{ point.image_x_px }}, {{ point.image_y_px }} px</td>
                <td><button class="rounded-md bg-red-500 px-2 py-1 text-xs font-semibold text-white" @click="calibration.deletePoint(point.id)">Delete</button></td>
              </tr>
            </tbody>
          </table>
        </div>
      </DashboardCard>
    </div>

    <DashboardCard title="Warnings" subtitle="Calibration status messages">
      <div class="flex flex-wrap gap-2">
        <StatusBadge v-for="warning in calibration.status.warnings" :key="warning" :label="warning" tone="warn" />
      </div>
    </DashboardCard>
  </div>
</template>
