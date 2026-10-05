<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { fetchCommandProfile, runCommandPreflight, selectCommandProfile, type GatewayPreflightResult } from '../../api/safety'
import type { CockpitMetric } from './types'

const props = defineProps<{ metrics: CockpitMetric[] }>()
const open = ref(false)
const startupIntent = localStorage.getItem('istiklal_startup_intent')
const preflight = ref<GatewayPreflightResult | null>(null)
const busy = ref(false)
const error = ref('')
const liveEntryRequested = (() => {
  const params = new URLSearchParams(window.location.search)
  return (params.get('live') === '1' && params.get('fire') !== '0') || startupIntent === 'LIVE_HARDWARE'
})()
const actuatorArm = ref(liveEntryRequested)
const authorityReady = computed(() => actuatorArm.value ? preflight.value?.physical_fire_enabled : preflight.value?.physical_motion_enabled)
const modeLabel = computed(() => actuatorArm.value ? 'CANLI SİSTEM' : 'TEST')
async function execute(action: () => Promise<void>): Promise<void> { busy.value = true; error.value = ''; try { await action() } catch (caught) { error.value = caught instanceof Error ? caught.message : 'İşlem başarısız.' } finally { busy.value = false } }
function selectTest(): Promise<void> { return execute(async () => { actuatorArm.value = false; preflight.value = await selectCommandProfile('LIVE_TEST', false); localStorage.setItem('istiklal_startup_intent', 'TRACKING_TEST') }) }
function selectLive(): Promise<void> { return execute(async () => { preflight.value = await selectCommandProfile('LIVE_TEST', true); actuatorArm.value = Boolean(preflight.value.actuator_armed); localStorage.setItem('istiklal_startup_intent', 'LIVE_HARDWARE') }) }
function preflightRun(): Promise<void> { return execute(async () => { preflight.value = await runCommandPreflight(actuatorArm.value); actuatorArm.value = Boolean(preflight.value.actuator_armed) }) }
onMounted(() => { void execute(async () => {
  preflight.value = await fetchCommandProfile()
  actuatorArm.value = preflight.value.actuator_armed
  // A successful landing acceptance enters cockpit in the live state. Keep
  // the trigger toggle visible, but make CANLI the initial selection so the
  // operator can explicitly switch to TEST whenever needed.
  if (liveEntryRequested && !actuatorArm.value) {
    preflight.value = await selectCommandProfile('LIVE_TEST', true)
    actuatorArm.value = Boolean(preflight.value.actuator_armed)
  }
}) })
</script>

<template>
  <footer class="safety-drawer">
    <button class="summary" type="button" @click="open = !open"><span>{{ modeLabel }} · Komut hazırlığı</span><b :class="authorityReady ? 'ready' : 'blocked'">{{ authorityReady ? 'READY' : preflight?.reason_codes[0] ?? 'PREFLIGHT_REQUIRED' }}</b><small>{{ open ? 'Kapat' : 'Kontroller' }}</small></button>
    <div v-if="open" class="drawer-body">
      <div class="metrics"><div v-for="metric in props.metrics" :key="metric.key"><span>{{ metric.label }}</span><b>{{ metric.value }}</b></div></div>
      <section class="controls"><div class="profile-row"><button :class="{ selected: !actuatorArm }" type="button" :disabled="busy" @click="selectTest">TEST · Tetik kapalı</button><button :class="{ selected: actuatorArm }" type="button" :disabled="busy" @click="selectLive">CANLI SİSTEM · Tetik açık</button></div><button class="preflight" type="button" :disabled="busy" @click="preflightRun">{{ busy ? 'Kontrol ediliyor…' : 'Ön kontrolü yenile' }}</button><p v-if="preflight?.reason_codes.length"><code>{{ preflight.reason_codes.join(' · ') }}</code></p><p v-if="error" class="error">{{ error }}</p></section>
    </div>
  </footer>
</template>

<style scoped>
.safety-drawer{border:1px solid #ffffff18;border-radius:12px;background:#07121df2;color:#e9f7ff}.summary{display:grid;grid-template-columns:1fr auto auto;gap:14px;align-items:center;width:100%;border:0;background:transparent;color:inherit;padding:11px 15px;text-align:left;cursor:pointer}.summary span{font-size:.7rem;font-weight:900;letter-spacing:.12em;text-transform:uppercase;color:#93a9bb}.summary b{font-size:.75rem}.summary small{color:#6de3f7;font-weight:800}.ready{color:#73eba8}.blocked{color:#ffd07a}.drawer-body{display:grid;grid-template-columns:1fr minmax(310px,430px);gap:14px;border-top:1px solid #ffffff14;padding:14px}.metrics{display:grid;grid-template-columns:repeat(2,1fr);gap:8px}.metrics div{padding:10px;border:1px solid #ffffff12;border-radius:8px;background:#020812}.metrics span,.metrics b{display:block}.metrics span{color:#8197aa;font-size:.62rem}.metrics b{margin-top:3px;font-size:.75rem}.controls{display:grid;gap:9px}.profile-row{display:grid;grid-template-columns:repeat(2,1fr);gap:5px}.controls button,.controls input{border:1px solid #ffffff22;border-radius:7px;background:#0c2030;color:#e9f9ff;padding:8px;font-size:.69rem;font-weight:800}.profile-row .selected{border-color:#58def5;background:#0e3548}.pico{display:grid;grid-template-columns:1fr 78px auto;gap:5px}.controls label{font-size:.76rem;color:#c2d5e1}.preflight{background:#36cbb8!important;color:#01201e!important}.controls p{margin:0}.controls code{color:#ffd27e;font-size:.65rem}.error{color:#ff9ba4;font-size:.72rem}@media(max-width:760px){.drawer-body{grid-template-columns:1fr}.pico{grid-template-columns:1fr 80px}.pico button{grid-column:1/3}}
</style>
