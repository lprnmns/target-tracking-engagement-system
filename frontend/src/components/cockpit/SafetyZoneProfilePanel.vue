<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { fetchSafetyZoneProfile, replaceSafetyZoneProfile } from '../../api/safety'
import type { AngularSafetyZone, SafetyZoneProfile } from '../../api/safety'

const profile = ref<SafetyZoneProfile | null>(null)
const motionZones = ref<AngularSafetyZone[]>([])
const fireZones = ref<AngularSafetyZone[]>([])
const busy = ref(false)
const error = ref<string | null>(null)
const saved = ref(false)

const profileHash = computed(() => profile.value?.profile_hash.slice(0, 12) ?? 'yükleniyor')

function copyZones(zones: AngularSafetyZone[]): AngularSafetyZone[] {
  return zones.map((zone) => ({ ...zone }))
}

function newZone(scope: 'motion' | 'fire'): AngularSafetyZone {
  const existing = scope === 'motion' ? motionZones.value : fireZones.value
  return {
    name: `${scope}_sector_${existing.length + 1}`,
    pan_min_deg: -1,
    pan_max_deg: 1,
    tilt_min_deg: -1,
    tilt_max_deg: 1,
    enabled: true,
  }
}

function addZone(scope: 'motion' | 'fire'): void {
  ;(scope === 'motion' ? motionZones : fireZones).value.push(newZone(scope))
  saved.value = false
}

function removeZone(scope: 'motion' | 'fire', index: number): void {
  ;(scope === 'motion' ? motionZones : fireZones).value.splice(index, 1)
  saved.value = false
}

async function load(): Promise<void> {
  busy.value = true
  error.value = null
  try {
    const next = await fetchSafetyZoneProfile()
    profile.value = next
    motionZones.value = copyZones(next.motion_zones)
    fireZones.value = copyZones(next.fire_zones)
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : String(reason)
  } finally {
    busy.value = false
  }
}

async function save(): Promise<void> {
  busy.value = true
  error.value = null
  saved.value = false
  try {
    const next = await replaceSafetyZoneProfile({
      motion_zones: copyZones(motionZones.value),
      fire_zones: copyZones(fireZones.value),
    })
    profile.value = next
    motionZones.value = copyZones(next.motion_zones)
    fireZones.value = copyZones(next.fire_zones)
    saved.value = true
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : String(reason)
  } finally {
    busy.value = false
  }
}

onMounted(() => { void load() })
</script>

<template>
  <section class="rounded-xl border border-amber-400/25 bg-slate-950/70 p-4 shadow-lg shadow-black/10">
    <div class="flex flex-wrap items-start justify-between gap-3">
      <div>
        <h3 class="text-sm font-semibold uppercase tracking-[0.15em] text-amber-100">Güvenlik sektör profili</h3>
        <p class="mt-1 text-xs text-slate-400">Hareket ve ateş sektörleri ayrıdır. Kaydetmek tüm fiziksel çıktıyı durdurur; devam için görünür preflight ve arm gerekir.</p>
      </div>
      <div class="text-right text-xs text-slate-400">
        <p class="font-mono text-cyan-200">{{ profileHash }}</p>
        <p>{{ profile?.source ?? '—' }}</p>
      </div>
    </div>

    <p v-if="error" class="mt-3 rounded border border-red-400/30 bg-red-500/10 px-3 py-2 font-mono text-xs text-red-200">{{ error }}</p>
    <p v-else-if="saved" class="mt-3 rounded border border-emerald-400/30 bg-emerald-500/10 px-3 py-2 text-xs text-emerald-100">Profil kaydedildi; preflight geçersiz kılındı.</p>

    <div class="mt-4 grid gap-4 xl:grid-cols-2">
      <div class="rounded-lg border border-white/10 bg-black/20 p-3">
        <div class="mb-3 flex items-center justify-between gap-2">
          <p class="text-sm font-medium text-white">Hareket yasak sektörleri</p>
          <button type="button" class="rounded bg-slate-700 px-2 py-1 text-xs text-white" :disabled="busy" @click="addZone('motion')">+ sektör</button>
        </div>
        <p v-if="!motionZones.length" class="text-xs text-slate-500">Ek hareket sektörü tanımlı değil.</p>
        <div v-for="(zone, index) in motionZones" :key="`motion-${index}`" class="mb-3 grid gap-2 rounded border border-white/8 p-2 md:grid-cols-3">
          <label class="grid gap-1 text-xs text-slate-400 md:col-span-3">Ad<input v-model.trim="zone.name" class="rounded border border-white/10 bg-black/30 px-2 py-1 text-sm text-white" /></label>
          <label class="grid gap-1 text-xs text-slate-400">Pan min<input v-model.number="zone.pan_min_deg" type="number" step="0.1" class="rounded border border-white/10 bg-black/30 px-2 py-1 text-sm text-white" /></label>
          <label class="grid gap-1 text-xs text-slate-400">Pan max<input v-model.number="zone.pan_max_deg" type="number" step="0.1" class="rounded border border-white/10 bg-black/30 px-2 py-1 text-sm text-white" /></label>
          <label class="grid gap-1 text-xs text-slate-400">Tilt min<input v-model.number="zone.tilt_min_deg" type="number" step="0.1" class="rounded border border-white/10 bg-black/30 px-2 py-1 text-sm text-white" /></label>
          <label class="grid gap-1 text-xs text-slate-400">Tilt max<input v-model.number="zone.tilt_max_deg" type="number" step="0.1" class="rounded border border-white/10 bg-black/30 px-2 py-1 text-sm text-white" /></label>
          <label class="flex items-center gap-2 self-end text-xs text-slate-300"><input v-model="zone.enabled" type="checkbox" /> Etkin</label>
          <button type="button" class="self-end rounded bg-red-500/80 px-2 py-1 text-xs font-medium text-white" :disabled="busy" @click="removeZone('motion', index)">Sil</button>
        </div>
      </div>

      <div class="rounded-lg border border-white/10 bg-black/20 p-3">
        <div class="mb-3 flex items-center justify-between gap-2">
          <p class="text-sm font-medium text-white">Ateş yasak sektörleri</p>
          <button type="button" class="rounded bg-slate-700 px-2 py-1 text-xs text-white" :disabled="busy" @click="addZone('fire')">+ sektör</button>
        </div>
        <p v-if="!fireZones.length" class="text-xs text-slate-500">Ek ateş sektörü tanımlı değil.</p>
        <div v-for="(zone, index) in fireZones" :key="`fire-${index}`" class="mb-3 grid gap-2 rounded border border-white/8 p-2 md:grid-cols-3">
          <label class="grid gap-1 text-xs text-slate-400 md:col-span-3">Ad<input v-model.trim="zone.name" class="rounded border border-white/10 bg-black/30 px-2 py-1 text-sm text-white" /></label>
          <label class="grid gap-1 text-xs text-slate-400">Pan min<input v-model.number="zone.pan_min_deg" type="number" step="0.1" class="rounded border border-white/10 bg-black/30 px-2 py-1 text-sm text-white" /></label>
          <label class="grid gap-1 text-xs text-slate-400">Pan max<input v-model.number="zone.pan_max_deg" type="number" step="0.1" class="rounded border border-white/10 bg-black/30 px-2 py-1 text-sm text-white" /></label>
          <label class="grid gap-1 text-xs text-slate-400">Tilt min<input v-model.number="zone.tilt_min_deg" type="number" step="0.1" class="rounded border border-white/10 bg-black/30 px-2 py-1 text-sm text-white" /></label>
          <label class="grid gap-1 text-xs text-slate-400">Tilt max<input v-model.number="zone.tilt_max_deg" type="number" step="0.1" class="rounded border border-white/10 bg-black/30 px-2 py-1 text-sm text-white" /></label>
          <label class="flex items-center gap-2 self-end text-xs text-slate-300"><input v-model="zone.enabled" type="checkbox" /> Etkin</label>
          <button type="button" class="self-end rounded bg-red-500/80 px-2 py-1 text-xs font-medium text-white" :disabled="busy" @click="removeZone('fire', index)">Sil</button>
        </div>
      </div>
    </div>

    <div class="mt-4 flex flex-wrap gap-2">
      <button type="button" class="rounded bg-cyan-500 px-3 py-2 text-sm font-semibold text-slate-950 disabled:opacity-50" :disabled="busy" @click="save">Profili kaydet ve güvenli durdur</button>
      <button type="button" class="rounded bg-slate-700 px-3 py-2 text-sm font-medium text-white disabled:opacity-50" :disabled="busy" @click="load">Yeniden yükle</button>
    </div>
  </section>
</template>
