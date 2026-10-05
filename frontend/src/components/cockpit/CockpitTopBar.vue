<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { Clock3, Crosshair, Settings2, ShieldCheck } from '@lucide/vue'
import type { CockpitBadge } from './types'

const props = defineProps<{ badges: CockpitBadge[], stage?: string | null }>()
const emit = defineEmits<{
  toggleEngineer: []
  triggerAutoHome: []
  openZeroingWizard: []
  openRadarModal: []
  openLimits: []
  openRadarSweep: []
  openNisanKalibre: []
  setStage: [stage: 'STAGE_1' | 'STAGE_2' | 'STAGE_3']
}>()
const clock = ref(new Date())
let timer: ReturnType<typeof setInterval> | null = null

const timeLabel = computed(() => clock.value.toLocaleTimeString('tr-TR', { hour: '2-digit', minute: '2-digit', second: '2-digit' }))
const visibleBadges = computed(() => props.badges.slice(0, 4).map((badge) => {
  const separator = badge.label.indexOf(' ')
  return {
    ...badge,
    key: separator > 0 ? badge.label.slice(0, separator) : 'DURUM',
    value: separator > 0 ? badge.label.slice(separator + 1) : badge.label,
  }
}))

function onSelectStage(targetStage: 'STAGE_1' | 'STAGE_2' | 'STAGE_3'): void {
  emit('setStage', targetStage)
}

onMounted(() => { timer = setInterval(() => { clock.value = new Date() }, 1000) })
onBeforeUnmount(() => { if (timer) clearInterval(timer) })
</script>

<template>
  <header class="topbar">
    <div class="brand">
      <span class="brand-mark"><ShieldCheck :size="18" /></span>
      <div><p>İSTİKLAL</p><h1>Operasyon Kokpiti</h1></div>
    </div>

    <div class="stage-selectors" role="group" aria-label="Yarışma Aşamaları">
      <button
        class="stage-button"
        :class="{ active: props.stage === 'STAGE_1' }"
        type="button"
        title="Aşama 1: Manuel Operatör (Bağımsız Balon + Hava Aracı)"
        @click="onSelectStage('STAGE_1')"
      >
        AŞAMA 1
      </button>
      <button
        class="stage-button"
        :class="{ active: props.stage === 'STAGE_2' }"
        type="button"
        title="Aşama 2: Otonom Balon Avlama (Radar Taramalı)"
        @click="onSelectStage('STAGE_2')"
      >
        AŞAMA 2
      </button>
      <button
        class="stage-button"
        :class="{ active: props.stage === 'STAGE_3' }"
        type="button"
        title="Aşama 3: Otonom Birlikte Takip (Düşman Balonu & 3 Kol Devriyesi)"
        @click="onSelectStage('STAGE_3')"
      >
        AŞAMA 3
      </button>
    </div>

    <div class="states" aria-label="Operasyon durumu">
      <div v-for="badge in visibleBadges" :key="badge.label" class="state" :class="`state-${badge.tone}`">
        <span>{{ badge.key }}</span><b>{{ badge.value }}</b>
      </div>
    </div>

    <div class="top-actions">
      <button class="engineer zeroing-btn" type="button" title="Taret silahı sıfırlama ve düşme kalibrasyonu" @click="emit('openZeroingWizard')">
        <Crosshair :size="15" /><span>🎯 Sıfırlama</span>
      </button>
      <button class="engineer" type="button" title="Limit switchleri arayarak tareti sıfırla" @click="emit('triggerAutoHome')">
        <span>🏠 Auto-Home</span>
      </button>
      <button class="engineer limits-btn" type="button" title="Taretin hareket edebileceği X / Y açı aralığını ayarla (AutoHome referanslı)" @click="emit('openLimits')">
        <span>📐 Limitler</span>
      </button>
      <button class="engineer" type="button" title="Radar tarama açısı: merkezden sola X°, sağa Y°" @click="emit('openRadarSweep')">
        <span>📡 Radar</span>
      </button>
      <button class="engineer" type="button" title="Nişan kalibrasyonu: saçmanın düştüğü yeri cm olarak girin" @click="emit('openNisanKalibre')">
        <span>🎯 Kalibre</span>
      </button>
      <button class="engineer" type="button" @click="emit('toggleEngineer')"><Settings2 :size="16" /><span>Mühendis</span></button>
      <time><Clock3 :size="15" />{{ timeLabel }}</time>
    </div>
  </header>
</template>

<style scoped>
.topbar{display:grid;grid-template-columns:minmax(190px,.7fr) auto minmax(400px,1.8fr) auto;align-items:center;gap:12px;min-height:68px;padding:10px 13px;border:1px solid rgba(92,225,248,.18);border-radius:16px;background:linear-gradient(180deg,rgba(8,24,38,.96),rgba(4,13,24,.96));color:#eefaff;box-shadow:0 14px 36px rgba(0,0,0,.3)}.brand{display:flex;align-items:center;gap:10px;min-width:0}.brand-mark{display:grid;place-items:center;width:37px;height:37px;border:1px solid rgba(94,234,255,.34);border-radius:11px;background:rgba(8,47,73,.72);color:#6ee7f9}.brand p{margin:0;color:#60e6fa;font-size:.66rem;font-weight:900;letter-spacing:.24em}.brand h1{overflow:hidden;margin:3px 0 0;font-size:1.02rem;text-overflow:ellipsis;white-space:nowrap}
.stage-selectors{display:flex;align-items:center;gap:5px;background:rgba(2,8,18,.75);padding:3px;border-radius:12px;border:1px solid rgba(103,232,249,.22)}
.stage-button{border:1px solid transparent;border-radius:8px;background:transparent;color:#94a3b8;padding:6px 12px;font-size:.76rem;font-weight:900;letter-spacing:.04em;cursor:pointer;transition:all .16s ease}
.stage-button:hover{color:#e2e8f0;background:rgba(15,23,42,.6)}
.stage-button.active{border-color:rgba(56,189,248,.6);background:linear-gradient(180deg,rgba(14,116,144,.6),rgba(8,47,73,.8));color:#38bdf8;box-shadow:0 0 12px rgba(56,189,248,.25)}
.states{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px}.state{position:relative;min-width:0;padding:8px 10px 8px 13px;border:1px solid rgba(148,163,184,.13);border-radius:10px;background:rgba(8,23,37,.76)}.state:before{position:absolute;top:10px;bottom:10px;left:0;width:3px;border-radius:0 3px 3px 0;background:#64748b;content:''}.state span,.state b{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.state span{color:#8aa0b1;font-size:.62rem;font-weight:900;letter-spacing:.12em}.state b{margin-top:3px;color:#dbeafe;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.77rem}.state-good:before{background:#34d399}.state-warn:before{background:#fbbf24}.state-bad:before{background:#fb7185}.state-good b{color:#9af2c3}.state-warn b{color:#ffd47e}.state-bad b{color:#ffa2aa}.top-actions{display:flex;align-items:center;gap:8px}.engineer,time{display:flex;align-items:center;gap:7px;border:1px solid rgba(103,232,249,.24);border-radius:10px;background:rgba(8,47,73,.42);color:#cffafe;padding:9px 11px;font-size:.75rem;font-weight:850;white-space:nowrap}.engineer{cursor:pointer;transition:.16s}.engineer:hover{border-color:rgba(103,232,249,.58);background:rgba(8,74,99,.58);transform:translateY(-1px)}time{border-color:rgba(148,163,184,.14);background:rgba(2,8,18,.5);color:#cbd5e1;font-family:ui-monospace,monospace}@media(max-width:1250px){.topbar{grid-template-columns:1fr auto}.stage-selectors{grid-column:1/-1;grid-row:2;justify-content:center}.states{grid-column:1/-1;grid-row:3}.top-actions{grid-column:2;grid-row:1}}@media(max-width:700px){.topbar{display:flex;flex-wrap:wrap}.brand{flex:1}.states{order:3;grid-template-columns:repeat(2,1fr);width:100%}time{display:none}.engineer span{display:none}}
</style>
