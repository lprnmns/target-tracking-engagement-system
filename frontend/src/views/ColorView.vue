<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import DashboardCard from '../components/dashboard/DashboardCard.vue'
import MetricRow from '../components/dashboard/MetricRow.vue'
import StatusBadge from '../components/shared/StatusBadge.vue'
import { useColorStore } from '../stores/colorStore'
import type { ColorClassifierConfig, TeamValue } from '../types/color'

const color = useColorStore()
const configDraft = ref<ColorClassifierConfig>({ ...color.config })
const sampleTeam = ref<TeamValue>('enemy')
const balloonPresent = ref(true)

const latest = computed(() => color.latest)
const ratios = computed(() => [
  { label: 'enemy', value: latest.value?.enemy_pixel_ratio ?? 0, tone: 'bad' as const },
  { label: 'friend', value: latest.value?.friend_pixel_ratio ?? 0, tone: 'good' as const },
  { label: 'unknown', value: latest.value?.unknown_pixel_ratio ?? 0, tone: 'warn' as const },
])

function cloneConfig(config: ColorClassifierConfig): ColorClassifierConfig {
  return JSON.parse(JSON.stringify(config)) as ColorClassifierConfig
}

function toneForDecision(decision: string): 'good' | 'warn' | 'bad' {
  if (decision === 'enemy') return 'bad'
  if (decision === 'friend') return 'good'
  return 'warn'
}

function sampleRequest() {
  return {
    frame_id: 1,
    detection_id: 1,
    body_crop_bbox: { x: 80, y: 60, w: 160, h: 110, format: 'pixel' as const },
    mock_team: sampleTeam.value,
    balloon_bbox_present: balloonPresent.value,
  }
}

onMounted(async () => {
  await color.refresh()
  configDraft.value = cloneConfig(color.config)
})

watch(
  () => color.config,
  (nextConfig) => {
    configDraft.value = cloneConfig(nextConfig)
  },
  { deep: true },
)
</script>

<template>
  <div class="grid gap-4">
    <div class="rounded-md border border-red-400/30 bg-red-400/10 px-4 py-3 text-sm text-red-100">
      Color output is advisory only. It is not connected to motor, servo or fire commands in Phase 8.
    </div>
    <div class="rounded-md border border-amber-400/35 bg-amber-400/10 px-4 py-3 text-sm font-semibold text-amber-100">
      Body color only, balloon excluded. Balloon mask status and mask preview are shown separately.
    </div>

    <div class="grid gap-4 xl:grid-cols-3">
      <DashboardCard title="Color Classifier" subtitle="HSV default, LAB placeholder">
        <MetricRow label="Color space" :value="color.config.color_space" />
        <MetricRow label="LAB enabled" :value="color.config.lab_enabled" />
        <MetricRow label="Balloon mask" :value="color.config.balloon_mask_enabled" />
        <MetricRow label="Decision threshold" :value="color.config.decision_threshold" />
        <MetricRow label="Consistent frames" :value="color.config.required_consistent_frames" />
      </DashboardCard>

      <DashboardCard title="Latest Color Decision" subtitle="Advisory team metadata">
        <MetricRow label="Decision" :value="latest?.decision ?? 'none'" />
        <MetricRow label="Confidence" :value="latest?.confidence ?? 0" />
        <MetricRow label="Decision mask applied" :value="latest?.balloon_mask_applied ?? false" />
        <MetricRow label="Body pixels" :value="latest?.body_pixel_count ?? 0" />
        <div class="mt-3 flex flex-wrap gap-2">
          <StatusBadge :label="latest?.decision ?? 'unknown'" :tone="toneForDecision(latest?.decision ?? 'unknown')" />
          <StatusBadge :label="latest?.balloon_mask_applied ? 'BALLOON MASKED' : 'MASK NOT APPLIED'" :tone="latest?.balloon_mask_applied ? 'good' : 'warn'" />
        </div>
      </DashboardCard>

      <DashboardCard title="Sample Runner" subtitle="Mock classification">
        <div class="grid gap-3">
          <select v-model="sampleTeam" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm text-white">
            <option value="enemy">enemy sample</option>
            <option value="friend">friend sample</option>
            <option value="unknown">unknown sample</option>
          </select>
          <label class="flex items-center gap-2 text-sm text-slate-300"><input v-model="balloonPresent" type="checkbox" /> Balloon bbox present</label>
          <button class="focus-ring rounded-md bg-cyan-500 px-3 py-2 text-sm font-semibold text-slate-950" @click="color.classify(sampleRequest())">Classify Sample</button>
          <button class="focus-ring rounded-md bg-amber-500 px-3 py-2 text-sm font-semibold text-slate-950" @click="color.preview(sampleRequest())">Preview Mask</button>
          <button class="focus-ring rounded-md bg-slate-700 px-3 py-2 text-sm font-semibold text-white" @click="color.reset">Reset</button>
        </div>
      </DashboardCard>
    </div>

    <div class="grid gap-4 xl:grid-cols-[1.2fr_0.8fr]">
      <DashboardCard title="HSV Tuning" subtitle="Enemy/friend thresholds">
        <div class="grid gap-4 xl:grid-cols-2">
          <div class="rounded-md border border-white/8 bg-black/18 p-3">
            <p class="mb-3 text-sm font-semibold text-red-200">Enemy HSV ranges</p>
            <div v-for="(range, index) in configDraft.enemy_hsv_ranges" :key="`enemy-${index}`" class="mb-3 grid grid-cols-2 gap-2">
              <label class="text-xs text-slate-400">Hue min<input v-model.number="range.h_min" type="range" min="0" max="180" class="w-full" />{{ range.h_min }}</label>
              <label class="text-xs text-slate-400">Hue max<input v-model.number="range.h_max" type="range" min="0" max="180" class="w-full" />{{ range.h_max }}</label>
              <label class="text-xs text-slate-400">Sat min<input v-model.number="range.s_min" type="range" min="0" max="255" class="w-full" />{{ range.s_min }}</label>
              <label class="text-xs text-slate-400">Val min<input v-model.number="range.v_min" type="range" min="0" max="255" class="w-full" />{{ range.v_min }}</label>
            </div>
          </div>

          <div class="rounded-md border border-white/8 bg-black/18 p-3">
            <p class="mb-3 text-sm font-semibold text-cyan-200">Friend HSV ranges</p>
            <div v-for="(range, index) in configDraft.friend_hsv_ranges" :key="`friend-${index}`" class="mb-3 grid grid-cols-2 gap-2">
              <label class="text-xs text-slate-400">Hue min<input v-model.number="range.h_min" type="range" min="0" max="180" class="w-full" />{{ range.h_min }}</label>
              <label class="text-xs text-slate-400">Hue max<input v-model.number="range.h_max" type="range" min="0" max="180" class="w-full" />{{ range.h_max }}</label>
              <label class="text-xs text-slate-400">Sat min<input v-model.number="range.s_min" type="range" min="0" max="255" class="w-full" />{{ range.s_min }}</label>
              <label class="text-xs text-slate-400">Val min<input v-model.number="range.v_min" type="range" min="0" max="255" class="w-full" />{{ range.v_min }}</label>
            </div>
          </div>
        </div>
        <button class="focus-ring mt-4 rounded-md bg-cyan-500 px-3 py-2 text-sm font-semibold text-slate-950" @click="color.saveConfig(configDraft)">Save Color Config</button>
      </DashboardCard>

      <DashboardCard title="Classifier Settings" subtitle="Validation parameters">
        <div class="grid gap-3">
          <label class="flex items-center gap-2 text-sm text-slate-300"><input v-model="configDraft.lab_enabled" type="checkbox" /> LAB enabled placeholder</label>
          <label class="flex items-center gap-2 text-sm text-slate-300"><input v-model="configDraft.balloon_mask_enabled" type="checkbox" /> Balloon mask enabled</label>
          <label class="grid gap-1 text-xs text-slate-400">Saturation min<input v-model.number="configDraft.saturation_min" type="number" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm text-white" /></label>
          <label class="grid gap-1 text-xs text-slate-400">Value min<input v-model.number="configDraft.value_min" type="number" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm text-white" /></label>
          <label class="grid gap-1 text-xs text-slate-400">Min body pixels<input v-model.number="configDraft.min_body_pixels" type="number" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm text-white" /></label>
          <label class="grid gap-1 text-xs text-slate-400">Decision threshold<input v-model.number="configDraft.decision_threshold" type="number" step="0.01" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm text-white" /></label>
          <label class="grid gap-1 text-xs text-slate-400">Required consistent frames<input v-model.number="configDraft.required_consistent_frames" type="number" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm text-white" /></label>
        </div>
      </DashboardCard>
    </div>

    <div class="grid gap-4 xl:grid-cols-2">
      <DashboardCard title="Pixel Ratio Bars" subtitle="Latest sample result">
        <p class="mb-3 text-xs text-slate-400">Pixel ratios update only after Classify Sample. Preview Mask only updates mask visualization.</p>
        <div class="grid gap-3">
          <div v-for="ratio in ratios" :key="ratio.label">
            <div class="mb-1 flex justify-between text-sm"><span>{{ ratio.label }}</span><span>{{ Math.round(ratio.value * 100) }}%</span></div>
            <div class="h-3 overflow-hidden rounded bg-slate-800">
              <div class="h-full" :class="ratio.label === 'enemy' ? 'bg-red-500' : ratio.label === 'friend' ? 'bg-cyan-500' : 'bg-amber-500'" :style="{ width: `${ratio.value * 100}%` }"></div>
            </div>
          </div>
        </div>
      </DashboardCard>

      <DashboardCard title="Mask Preview / Warnings" subtitle="Preview state is separate from latest decision">
        <svg viewBox="0 0 360 160" class="mb-4 h-40 w-full rounded-md border border-white/10 bg-black/24">
          <rect x="70" y="28" width="170" height="104" rx="8" fill="#1f2937" stroke="#38bdf8" stroke-width="2" />
          <circle cx="208" cy="72" r="24" :fill="color.maskPreview?.balloon_mask_applied ? '#f97316' : '#7f1d1d'" opacity="0.75" />
          <path v-if="color.maskPreview?.balloon_mask_applied" d="M185 48 L232 96 M232 48 L185 96" stroke="#111827" stroke-width="5" stroke-linecap="round" />
          <rect x="70" y="28" width="170" height="104" rx="8" fill="none" stroke="#94a3b8" stroke-dasharray="4 4" />
          <text x="80" y="24" fill="#93c5fd" font-size="12">Body crop</text>
          <text x="244" y="76" fill="#fbbf24" font-size="12">{{ color.maskPreview?.balloon_mask_applied ? 'Balloon excluded' : 'Mask not applied' }}</text>
          <text x="80" y="150" fill="#cbd5e1" font-size="12">Mock mask visualization</text>
        </svg>
        <MetricRow label="Preview available" :value="color.maskPreview?.debug_masks_available ?? false" />
        <MetricRow label="Preview mask applied" :value="color.maskPreview?.balloon_mask_applied ?? false" />
        <MetricRow label="Latest decision mask" :value="latest?.balloon_mask_applied ?? false" />
        <div class="mt-3 flex flex-wrap gap-2">
          <StatusBadge v-for="warning in latest?.blocking_warnings ?? []" :key="warning" :label="warning" tone="warn" />
          <StatusBadge v-for="warning in color.maskPreview?.warnings ?? []" :key="`mask-${warning}`" :label="warning" tone="warn" />
          <p v-if="!(latest?.blocking_warnings.length) && !(color.maskPreview?.warnings.length)" class="text-sm text-slate-400">No warnings.</p>
        </div>
      </DashboardCard>
    </div>
  </div>
</template>
