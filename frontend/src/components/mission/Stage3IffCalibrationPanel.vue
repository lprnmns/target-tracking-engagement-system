<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import DashboardCard from '../dashboard/DashboardCard.vue'
import MetricRow from '../dashboard/MetricRow.vue'
import StatusBadge from '../shared/StatusBadge.vue'
import { addColorCalibrationReference, fetchColorCalibration, resetColorCalibration } from '../../api/color'
import type { ColorCalibrationStatus } from '../../types/color'

const emptyStatus: ColorCalibrationStatus = {
  valid: false,
  profile_hash: null,
  enemy_reference_count: 0,
  friend_reference_count: 0,
  references: [],
  reason_codes: ['A3_IFF_CALIBRATION_REQUIRED'],
  updated_at: 0,
}

const status = ref<ColorCalibrationStatus>({ ...emptyStatus })
const expectedTeam = ref<'enemy' | 'friend'>('enemy')
const captureId = ref('')
const busy = ref(false)
const error = ref<string | null>(null)
const tone = computed(() => status.value.valid ? 'good' : 'bad')

async function refresh(): Promise<void> {
  try {
    status.value = await fetchColorCalibration()
    error.value = null
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : 'IFF kalibrasyon durumu alınamadı'
  }
}

async function record(): Promise<void> {
  if (!captureId.value.trim()) {
    error.value = 'Capture ID zorunlu.'
    return
  }
  busy.value = true
  try {
    status.value = await addColorCalibrationReference({ expected_team: expectedTeam.value, capture_id: captureId.value.trim() })
    captureId.value = ''
    error.value = null
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : 'IFF referansı kaydedilemedi'
  } finally {
    busy.value = false
  }
}

async function reset(): Promise<void> {
  busy.value = true
  try {
    status.value = await resetColorCalibration()
    error.value = null
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : 'IFF referansları sıfırlanamadı'
  } finally {
    busy.value = false
  }
}

onMounted(() => { void refresh() })
</script>

<template>
  <DashboardCard title="Aşama 3 Gerçek IFF Referansları" subtitle="Bilinen dost ve düşman gövde ROI’lerinden profil-hash bağlı saha referansı">
    <div class="mb-3 flex flex-wrap items-center gap-2">
      <StatusBadge :label="status.valid ? 'IFF FIELD PROFILE VERIFIED' : 'IFF NO-GO'" :tone="tone as any" />
      <button class="focus-ring ml-auto rounded-md bg-slate-700 px-3 py-2 text-xs font-semibold text-white" :disabled="busy" @click="refresh">Yenile</button>
    </div>
    <div class="grid gap-2 md:grid-cols-3">
      <MetricRow label="Enemy referansı" :value="`${status.enemy_reference_count}/3`" />
      <MetricRow label="Friend referansı" :value="`${status.friend_reference_count}/3`" />
      <MetricRow label="Profil hash" :value="status.profile_hash?.slice(0, 12) ?? 'yok'" />
    </div>
    <p class="mt-3 text-xs text-slate-400">Bilinen referans hedefini gerçek kamera altında göster. Son gerçek ROI kararı seçilen takım ile uyumlu ve yeterli piksel/temporal kanıta sahipse kaydedilir; mock örnek kabul edilmez.</p>
    <div class="mt-3 grid gap-2 md:grid-cols-[160px_1fr_auto_auto]">
      <select v-model="expectedTeam" :disabled="busy" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm text-white"><option value="enemy">Known enemy</option><option value="friend">Known friend</option></select>
      <input v-model="captureId" :disabled="busy" type="text" placeholder="Capture ID" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm text-white">
      <button class="focus-ring rounded-md bg-cyan-500 px-3 py-2 text-sm font-semibold text-slate-950 disabled:opacity-40" :disabled="busy" @click="record">Son ROI’yi referans yap</button>
      <button class="focus-ring rounded-md bg-red-700 px-3 py-2 text-sm font-semibold text-white disabled:opacity-40" :disabled="busy" @click="reset">Sıfırla</button>
    </div>
    <p v-if="status.reason_codes.length" class="mt-3 font-mono text-xs text-amber-200">{{ status.reason_codes.join(' · ') }}</p>
    <p v-if="error" class="mt-2 font-mono text-xs text-red-200">{{ error }}</p>
  </DashboardCard>
</template>
