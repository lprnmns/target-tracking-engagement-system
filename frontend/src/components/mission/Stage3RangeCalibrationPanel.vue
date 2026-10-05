<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import DashboardCard from '../dashboard/DashboardCard.vue'
import MetricRow from '../dashboard/MetricRow.vue'
import StatusBadge from '../shared/StatusBadge.vue'
import { addStage3RangeObservation, fetchStage3RangeStatus, removeStage3RangeObservation, resetStage3Range, validateStage3Range } from '../../api/stage3'
import type { Stage3RangeCalibrationStatus } from '../../types/stage3'

const emptyStatus: Stage3RangeCalibrationStatus = {
  valid: false,
  reason_codes: ['A3_RANGE_CALIBRATION_UNAVAILABLE'],
  body_model_id: null,
  body_model_hash: null,
  calibration_hash: null,
  observations: [],
  fits: [],
  validated_at: null,
  updated_at: 0,
}

const status = ref<Stage3RangeCalibrationStatus>({ ...emptyStatus })
const error = ref<string | null>(null)
const busy = ref(false)
const className = ref('f16')
const distanceM = ref(5)
const bboxHeightPx = ref(200)
const captureId = ref('')
const note = ref('')

const tone = computed(() => status.value.valid ? 'good' : 'bad')
const expectedDistances = computed(() => 'Her sınıf için 5 / 10 / 15 m')

async function refresh(): Promise<void> {
  try {
    status.value = await fetchStage3RangeStatus()
    error.value = null
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : 'A3 range durumu alınamadı'
  }
}

async function addObservation(): Promise<void> {
  if (!captureId.value.trim()) {
    error.value = 'Capture ID zorunlu.'
    return
  }
  busy.value = true
  try {
    status.value = await addStage3RangeObservation({
      class_name: className.value,
      distance_m: distanceM.value,
      bbox_height_px: bboxHeightPx.value,
      capture_id: captureId.value.trim(),
      note: note.value.trim() || undefined,
    })
    captureId.value = ''
    note.value = ''
    error.value = null
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : 'Range gözlemi eklenemedi'
  } finally {
    busy.value = false
  }
}

async function validate(): Promise<void> {
  busy.value = true
  try {
    status.value = await validateStage3Range()
    error.value = null
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : 'Range profili doğrulanamadı'
  } finally {
    busy.value = false
  }
}

async function remove(observationId: string): Promise<void> {
  busy.value = true
  try {
    status.value = await removeStage3RangeObservation(observationId)
    error.value = null
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : 'Gözlem silinemedi'
  } finally {
    busy.value = false
  }
}

async function reset(): Promise<void> {
  busy.value = true
  try {
    status.value = await resetStage3Range()
    error.value = null
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : 'Range profili sıfırlanamadı'
  } finally {
    busy.value = false
  }
}

onMounted(() => { void refresh() })
</script>

<template>
  <DashboardCard title="Aşama 3 Metrik Menzil Profili" subtitle="Model hash’ine bağlı 5 / 10 / 15 m saha kalibrasyonu">
    <div class="mb-3 flex flex-wrap items-center gap-2">
      <StatusBadge :label="status.valid ? 'RANGE PROFILE VERIFIED' : 'RANGE NO-GO'" :tone="tone as any" />
      <StatusBadge :label="status.body_model_id ?? 'BODY MODEL YOK'" tone="neutral" />
      <button class="focus-ring ml-auto rounded-md bg-slate-700 px-3 py-2 text-xs font-semibold text-white" :disabled="busy" @click="refresh">Yenile</button>
    </div>
    <div class="grid gap-2 md:grid-cols-3">
      <MetricRow label="Zorunlu mesafeler" :value="expectedDistances" />
      <MetricRow label="Kayıtlı gözlem" :value="status.observations.length" />
      <MetricRow label="Kalibrasyon hash" :value="status.calibration_hash?.slice(0, 12) ?? 'yok'" />
    </div>
    <p class="mt-3 text-xs text-slate-400">Her kayıt gerçek kamera frame’inden alınmış body bbox yüksekliği ve aynı capture ID ile girilir. Competition+Aşama 3 sırasında profil kilitlidir.</p>
    <div class="mt-3 grid gap-2 md:grid-cols-5">
      <select v-model="className" :disabled="busy" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm text-white">
        <option value="f16">Savaş Uçağı</option><option value="helicopter">Helikopter</option><option value="ballistic_missile">Balistik Füze</option><option value="mini_micro_uav">Mini/Mikro İHA</option>
      </select>
      <input v-model.number="distanceM" :disabled="busy" min="0.1" max="30" step="0.1" type="number" placeholder="Mesafe m" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm text-white">
      <input v-model.number="bboxHeightPx" :disabled="busy" min="1" step="0.1" type="number" placeholder="Body bbox px" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm text-white">
      <input v-model="captureId" :disabled="busy" type="text" placeholder="Capture ID" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm text-white">
      <button class="focus-ring rounded-md bg-cyan-500 px-3 py-2 text-sm font-semibold text-slate-950 disabled:opacity-40" :disabled="busy" @click="addObservation">Gözlem ekle</button>
    </div>
    <input v-model="note" :disabled="busy" type="text" placeholder="Opsiyonel saha notu" class="mt-2 w-full rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm text-white">
    <div class="mt-3 flex flex-wrap gap-2">
      <button class="focus-ring rounded-md bg-emerald-500 px-3 py-2 text-sm font-semibold text-slate-950 disabled:opacity-40" :disabled="busy" @click="validate">Model hash ile doğrula</button>
      <button class="focus-ring rounded-md bg-red-700 px-3 py-2 text-sm font-semibold text-white disabled:opacity-40" :disabled="busy" @click="reset">Profili sıfırla</button>
    </div>
    <p v-if="status.reason_codes.length" class="mt-3 font-mono text-xs text-amber-200">{{ status.reason_codes.join(' · ') }}</p>
    <p v-if="error" class="mt-2 font-mono text-xs text-red-200">{{ error }}</p>
    <div class="mt-4 max-h-52 overflow-auto rounded-md border border-white/8">
      <div v-for="item in status.observations" :key="item.observation_id" class="grid grid-cols-[1fr_auto] items-center gap-2 border-b border-white/8 px-3 py-2 text-xs last:border-b-0">
        <span class="font-mono text-slate-300">{{ item.class_name }} · {{ item.distance_m }}m · {{ item.bbox_height_px }}px · {{ item.capture_id }}</span>
        <button class="focus-ring rounded bg-slate-700 px-2 py-1 text-white disabled:opacity-40" :disabled="busy" @click="remove(item.observation_id)">Sil</button>
      </div>
      <p v-if="!status.observations.length" class="p-3 text-xs text-slate-500">Henüz saha gözlemi yok.</p>
    </div>
  </DashboardCard>
</template>
