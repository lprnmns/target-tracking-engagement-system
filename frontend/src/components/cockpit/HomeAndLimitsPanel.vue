<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import {
  fetchHardwareHomeStatus,
  fetchHardwarePosition,
  fetchMotionEnvelope,
  startHardwareHome,
  updateMotionEnvelope,
  type HardwareHomeResult,
} from '../../api/hardware'

const envelope = reactive({ pan_min_deg: 0, pan_max_deg: 270, tilt_min_deg: 0, tilt_max_deg: 60 })
const status = ref<HardwareHomeResult | null>(null)
const busy = ref(false)
const message = ref('Pico konumu okunuyor…')
let pollTimer: ReturnType<typeof setInterval> | null = null

const phaseLabel = computed(() => {
  const phase = status.value?.phase
  if (phase === 'TILT_SEEK') return 'Dikey üst sıfır aranıyor'
  if (phase === 'PAN_SEEK') return 'Yatay sol sıfır aranıyor'
  if (phase === 'CENTERING') return 'Merkeze gidiyor · PAN 135° / TILT 30°'
  if (phase === 'DONE') return 'Referans hazır'
  if (phase === 'FAULT') return 'Homing hatası'
  return status.value?.homed ? 'Referans hazır' : 'Referans bekleniyor'
})

function stopPolling(): void {
  if (pollTimer) clearInterval(pollTimer)
  pollTimer = null
}

async function refreshPosition(): Promise<void> {
  try {
    status.value = await fetchHardwarePosition()
    message.value = status.value.accepted ? 'Pico mutlak konumu doğrulandı.' : status.value.reason_codes.join(' · ')
  } catch (caught) {
    message.value = caught instanceof Error ? caught.message : 'Pico konumu okunamadı.'
  }
}

async function pollHome(): Promise<void> {
  try {
    status.value = await fetchHardwareHomeStatus()
    if (status.value.phase === 'DONE' && status.value.homed) {
      stopPolling()
      busy.value = false
      message.value = 'Auto-home tamamlandı; taret merkezde.'
    } else if (!status.value.accepted || status.value.phase === 'FAULT') {
      stopPolling()
      busy.value = false
      message.value = status.value.reason_codes.join(' · ') || status.value.detail
    }
  } catch (caught) {
    stopPolling()
    busy.value = false
    message.value = caught instanceof Error ? caught.message : 'Homing durumu alınamadı.'
  }
}

async function startHome(): Promise<void> {
  busy.value = true
  message.value = 'Auto-home başlatılıyor…'
  try {
    status.value = await startHardwareHome()
    if (!status.value.accepted) {
      busy.value = false
      message.value = status.value.reason_codes.join(' · ') || status.value.detail
      return
    }
    stopPolling()
    pollTimer = setInterval(() => { void pollHome() }, 250)
    await pollHome()
  } catch (caught) {
    busy.value = false
    message.value = caught instanceof Error ? caught.message : 'Auto-home başlatılamadı.'
  }
}

async function saveEnvelope(): Promise<void> {
  busy.value = true
  try {
    const result = await updateMotionEnvelope({ ...envelope })
    message.value = result.accepted ? 'İzinli hareket aralığı Gateway ve Pico’ya uygulandı.' : result.reason_codes.join(' · ')
    if (result.accepted) window.dispatchEvent(new CustomEvent('istiklal:motion-envelope-updated', { detail: result.envelope }))
  } catch (caught) {
    message.value = caught instanceof Error ? caught.message : 'Hareket aralığı uygulanamadı.'
  } finally {
    busy.value = false
  }
}

onMounted(async () => {
  try {
    const result = await fetchMotionEnvelope()
    Object.assign(envelope, result.envelope)
  } catch { /* position read below exposes the live reason */ }
  await refreshPosition()
})
onBeforeUnmount(stopPolling)
</script>

<template>
  <section class="home-limit-panel">
    <header>
      <div><p>REFERANS VE HAREKET ZARFI</p><h3>Auto-home & derece sınırları</h3></div>
      <span :class="status?.homed ? 'ready' : status?.phase === 'FAULT' ? 'fault' : 'pending'">{{ phaseLabel }}</span>
    </header>

    <div class="position-strip">
      <div><small>PAN</small><b>{{ status?.pan_deg?.toFixed(1) ?? '—' }}°</b></div>
      <div><small>TILT</small><b>{{ status?.tilt_deg?.toFixed(1) ?? '—' }}°</b></div>
      <div><small>GP22 · TILT ÜST</small><b>{{ status?.limit_x_active ? 'AKTİF' : 'SERBEST' }}</b></div>
      <div><small>GP26 · PAN SOL</small><b>{{ status?.limit_y_active ? 'AKTİF' : 'SERBEST' }}</b></div>
    </div>

    <div class="limit-grid">
      <fieldset><legend>Yatay / PAN · 0–270°</legend><label>Başlangıç<input v-model.number="envelope.pan_min_deg" type="number" min="0" max="269" step="1">°</label><label>Bitiş<input v-model.number="envelope.pan_max_deg" type="number" min="1" max="270" step="1">°</label></fieldset>
      <fieldset><legend>Dikey / TILT · 0–60°</legend><label>Başlangıç<input v-model.number="envelope.tilt_min_deg" type="number" min="0" max="59" step="1">°</label><label>Bitiş<input v-model.number="envelope.tilt_max_deg" type="number" min="1" max="60" step="1">°</label></fieldset>
    </div>

    <div class="actions"><button type="button" :disabled="busy" @click="startHome">{{ busy ? 'İşlem sürüyor…' : 'Auto-home başlat' }}</button><button type="button" :disabled="busy" @click="saveEnvelope">Aralığı uygula</button><button class="ghost" type="button" :disabled="busy" @click="refreshPosition">Konumu yenile</button></div>
    <div class="readback" :class="{ fault: status?.phase === 'FAULT' || status?.accepted === false }"><span>{{ message }}</span><code v-if="status?.limit_reason_code">{{ status.limit_reason_code }}</code><code v-for="code in status?.reason_codes ?? []" :key="code">{{ code }}</code></div>
  </section>
</template>

<style scoped>
.home-limit-panel{display:grid;gap:13px;padding:16px;border:1px solid #39cbe633;border-radius:14px;background:linear-gradient(145deg,#081a28,#06131f);color:#eaf8ff}.home-limit-panel header{display:flex;align-items:flex-start;justify-content:space-between;gap:12px}.home-limit-panel p{margin:0;color:#54ddeb;font-size:.62rem;font-weight:900;letter-spacing:.16em}.home-limit-panel h3{margin:4px 0 0;font-size:1rem}.home-limit-panel header>span{padding:6px 9px;border-radius:99px;background:#213143;color:#b9cad5;font-size:.64rem;font-weight:900}.home-limit-panel header>span.ready{background:#0c3b2d;color:#75edb4}.home-limit-panel header>span.fault{background:#4a1d25;color:#ffafb7}.position-strip{display:grid;grid-template-columns:repeat(4,1fr);gap:7px}.position-strip>div{display:grid;gap:3px;padding:9px;border:1px solid #ffffff12;border-radius:9px;background:#03101a}.position-strip small{color:#7f9aaa;font-size:.56rem}.position-strip b{font:800 .76rem ui-monospace,monospace}.limit-grid{display:grid;grid-template-columns:1fr 1fr;gap:9px}.limit-grid fieldset{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin:0;padding:11px;border:1px solid #ffffff16;border-radius:10px}.limit-grid legend{padding:0 5px;color:#9dc0cf;font-size:.65rem;font-weight:800}.limit-grid label{display:grid;grid-template-columns:1fr 68px auto;align-items:center;gap:5px;color:#a9bfcc;font-size:.66rem}.limit-grid input{width:100%;box-sizing:border-box;border:1px solid #55dbea35;border-radius:7px;background:#020a12;color:#ecfbff;padding:7px}.actions{display:flex;flex-wrap:wrap;gap:7px}.actions button{border:1px solid #49dbea55;border-radius:8px;background:#0b3343;color:#dffbff;padding:8px 11px;font-size:.68rem;font-weight:900;cursor:pointer}.actions button:hover:not(:disabled){filter:brightness(1.15)}.actions button:disabled{opacity:.45}.actions .ghost{background:#07141f;color:#a9bfcc}.readback{display:flex;flex-wrap:wrap;gap:7px;align-items:center;padding:8px 10px;border-left:3px solid #35d097;background:#08251e;color:#bdebd7;font-size:.66rem}.readback.fault{border-left-color:#f16d78;background:#31171b;color:#ffd0d4}.readback code{color:#ffd17d;font-size:.61rem}@media(max-width:760px){.position-strip,.limit-grid{grid-template-columns:1fr 1fr}.limit-grid fieldset{grid-column:1/-1}}
</style>
