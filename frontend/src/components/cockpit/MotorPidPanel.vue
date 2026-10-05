<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { Activity, Check, Gauge, Play, RotateCcw, SlidersHorizontal, Square, Star } from '@lucide/vue'
import {
  applyTrackingPreset,
  fetchTrackingTuning,
  rateTrackingTrial,
  startTrackingTrial,
  stopTrackingTrial,
  type TrackingTuningStatus,
} from '../../api/trackingTuning'
import { fetchTrackingStatus, updateTrackingConfig } from '../../api/tracking'
import type { TrackingPreset } from '../../api/trackingTuning'
import type { TrackingStatus } from '../../types/tracking'

const status = ref<TrackingTuningStatus>({ presets: [], active_trial: null, results: [] })
const busy = ref(false)
const message = ref('Bir profil seçin; her denemede balonu aynı rotada hareket ettirin.')
const liveStatus = ref<TrackingStatus | null>(null)
const dirty = ref(false)
const draft = reactive({
  pid_kp_x: 11.0,
  pid_ki_x: 0,
  pid_kd_x: 1.1,
  pid_kp_y: 8.0,
  pid_ki_y: 0,
  pid_kd_y: 2.0,
  smoothing_alpha: 0.25,
  command_rate_hz: 66,
  max_speed_x: 8000,
  max_speed_y: 8000,
})
let timer: ReturnType<typeof setInterval> | null = null

const rankedResults = computed(() => [...status.value.results].reverse().sort((a, b) => {
  const operatorDelta = (b.operator_rating ?? 0) - (a.operator_rating ?? 0)
  return operatorDelta || b.technical_score - a.technical_score
}))

const appliedAtLabel = computed(() => {
  const timestamp = liveStatus.value?.config_applied_at
  return timestamp ? new Date(timestamp * 1000).toLocaleTimeString('tr-TR') : '—'
})
const liveOutputLabel = computed(() => {
  const update = liveStatus.value?.last_update
  if (!update) return 'Henüz kontrol çıktısı yok'
  return `Hata ${Math.round(update.error_x_px)} / ${Math.round(update.error_y_px)} px · Çıkış ${update.speed_x} / ${update.speed_y}`
})

function syncDraft(source: TrackingStatus): void {
  draft.pid_kp_x = source.pid_kp_x
  draft.pid_ki_x = source.pid_ki_x
  draft.pid_kd_x = source.pid_kd_x
  draft.pid_kp_y = source.pid_kp_y
  draft.pid_ki_y = source.pid_ki_y
  draft.pid_kd_y = source.pid_kd_y
  draft.smoothing_alpha = source.smoothing_alpha
  draft.command_rate_hz = source.command_rate_hz
  draft.max_speed_x = source.max_speed_x ?? source.max_speed
  draft.max_speed_y = source.max_speed_y ?? source.max_speed
  dirty.value = false
}

function markDirty(): void { dirty.value = true }

async function refresh(): Promise<void> {
  try {
    const [tuning, tracker] = await Promise.all([fetchTrackingTuning(), fetchTrackingStatus()])
    status.value = tuning
    liveStatus.value = tracker
    if (!dirty.value) syncDraft(tracker)
  } catch (error) { message.value = String(error) }
}

async function applyLive(): Promise<void> {
  busy.value = true
  try {
    const applied = await updateTrackingConfig({ ...draft })
    liveStatus.value = applied
    syncDraft(applied)
    message.value = `Canlı uygulandı · revizyon ${applied.config_revision} · ${applied.active ? 'takip kesilmeden etkin' : 'sonraki takip için hazır'}.`
  } catch (error) {
    message.value = `Canlı PID uygulanamadı: ${error instanceof Error ? error.message : error}`
  } finally { busy.value = false }
}

function loadPresetToDraft(preset: TrackingPreset): void {
  const value = (key: string, fallback: number) => Number(preset.config[key] ?? fallback)
  draft.pid_kp_x = value('pid_kp_x', draft.pid_kp_x)
  draft.pid_ki_x = value('pid_ki_x', draft.pid_ki_x)
  draft.pid_kd_x = value('pid_kd_x', draft.pid_kd_x)
  draft.pid_kp_y = value('pid_kp_y', draft.pid_kp_y)
  draft.pid_ki_y = value('pid_ki_y', draft.pid_ki_y)
  draft.pid_kd_y = value('pid_kd_y', draft.pid_kd_y)
  draft.smoothing_alpha = value('smoothing_alpha', draft.smoothing_alpha)
  draft.command_rate_hz = value('command_rate_hz', draft.command_rate_hz)
  draft.max_speed_x = value('max_speed_x', value('max_speed', draft.max_speed_x))
  draft.max_speed_y = value('max_speed_y', value('max_speed', draft.max_speed_y))
  dirty.value = true
  message.value = `${preset.name} değerleri düzenleyiciye alındı. Etkinleştirmek için Canlı uygula.`
}

async function start(presetId: string): Promise<void> {
  busy.value = true
  try {
    status.value = await startTrackingTrial(presetId)
    const tracker = await fetchTrackingStatus()
    liveStatus.value = tracker
    syncDraft(tracker)
    message.value = 'Deneme başladı · balonu sağ-sol, yukarı-aşağı ve kısa süre kadraj dışına taşıyın.'
  } catch (error) { message.value = `Başlatılamadı: ${error instanceof Error ? error.message : error}` }
  finally { busy.value = false }
}

async function stop(): Promise<void> {
  busy.value = true
  try { status.value = await stopTrackingTrial(); message.value = 'Deneme kaydedildi. Arkadaşınız 1–5 yıldız versin.' }
  catch (error) { message.value = `Durdurulamadı: ${error instanceof Error ? error.message : error}` }
  finally { busy.value = false }
}

async function rate(trialId: string, rating: number): Promise<void> {
  status.value = await rateTrackingTrial(trialId, rating)
  message.value = `${rating}/5 operatör puanı kaydedildi.`
}

async function applyWinner(presetId: string): Promise<void> {
  busy.value = true
  try {
    status.value = await applyTrackingPreset(presetId)
    const tracker = await fetchTrackingStatus()
    liveStatus.value = tracker
    syncDraft(tracker)
    message.value = `Profil canlı uygulandı · revizyon ${tracker.config_revision}${tracker.active ? ' · takip kesilmedi' : ''}.`
  }
  catch (error) { message.value = `Uygulanamadı: ${error instanceof Error ? error.message : error}` }
  finally { busy.value = false }
}

onMounted(() => { void refresh(); timer = setInterval(() => void refresh(), 750) })
onBeforeUnmount(() => { if (timer) clearInterval(timer) })
</script>

<template>
  <section class="tuning-panel">
    <header>
      <div><p>FİZİKSEL SAHA KARŞILAŞTIRMASI</p><h3>Takip Algoritması ve PID Deneyleri</h3></div>
      <span :class="liveStatus?.active ? 'live' : ''"><Activity :size="14" />{{ liveStatus?.active ? 'TAKİP AKTİF' : 'TAKİP BEKLİYOR' }}</span>
    </header>

    <section class="live-editor" :class="{ dirty }">
      <div class="live-editor-head">
        <div><SlidersHorizontal :size="17" /><span><b>Canlı PID ayarı</b><small>Değerler tek paket halinde uygulanır; aktif takip durmaz.</small></span></div>
        <div class="revision"><b>REV {{ liveStatus?.config_revision ?? 0 }}</b><small>{{ appliedAtLabel }}</small></div>
      </div>

      <div class="axis-grid">
        <fieldset>
          <legend>YATAY · X / PAN</legend>
          <label><span>Kp</span><input v-model.number="draft.pid_kp_x" type="number" min="0" max="10000" step="0.1" @input="markDirty"></label>
          <label><span>Ki</span><input v-model.number="draft.pid_ki_x" type="number" min="0" max="10000" step="0.01" @input="markDirty"></label>
          <label><span>Kd</span><input v-model.number="draft.pid_kd_x" type="number" min="0" max="10000" step="0.1" @input="markDirty"></label>
          <label><span>Azami hız</span><input v-model.number="draft.max_speed_x" type="number" min="1" max="15000" step="100" @input="markDirty"></label>
        </fieldset>
        <fieldset>
          <legend>DİKEY · Y / TILT</legend>
          <label><span>Kp</span><input v-model.number="draft.pid_kp_y" type="number" min="0" max="10000" step="0.1" @input="markDirty"></label>
          <label><span>Ki</span><input v-model.number="draft.pid_ki_y" type="number" min="0" max="10000" step="0.01" @input="markDirty"></label>
          <label><span>Kd</span><input v-model.number="draft.pid_kd_y" type="number" min="0" max="10000" step="0.1" @input="markDirty"></label>
          <label><span>Azami hız</span><input v-model.number="draft.max_speed_y" type="number" min="1" max="15000" step="100" @input="markDirty"></label>
        </fieldset>
      </div>

      <div class="runtime-grid">
        <label><span>Yumuşatma</span><input v-model.number="draft.smoothing_alpha" type="number" min="0" max="1" step="0.01" @input="markDirty"></label>
        <label><span>Komut hızı</span><input v-model.number="draft.command_rate_hz" type="number" min="1" max="100" step="1" @input="markDirty"><em>Hz</em></label>
      </div>

      <div class="live-readback">
        <span><Activity :size="14" />{{ liveOutputLabel }}</span>
        <b>{{ dirty ? 'UYGULANMAMIŞ DEĞİŞİKLİK' : 'SUNUCU READ-BACK DOĞRULANDI' }}</b>
      </div>
      <div class="editor-actions">
        <button class="reset" type="button" :disabled="busy || !liveStatus" @click="liveStatus && syncDraft(liveStatus)"><RotateCcw :size="14" /> Geri al</button>
        <button class="apply-live" type="button" :disabled="busy || !dirty" @click="applyLive"><Check :size="15" /> Canlı uygula</button>
      </div>
    </section>

    <div v-if="status.active_trial" class="active-trial">
      <div><small>Çalışan profil</small><b>{{ status.active_trial.preset_name }}</b><em>{{ status.active_trial.algorithm }}</em></div>
      <div class="live-metrics">
        <span><b>{{ status.active_trial.elapsed_s }} s</b><small>Süre</small></span>
        <span><b>{{ status.active_trial.target_frames }}</b><small>Hedefli kare</small></span>
        <span><b>{{ status.active_trial.lost_frames }}</b><small>Kayıp kare</small></span>
        <span><b>{{ status.active_trial.reversals }}</b><small>Yön değişimi</small></span>
      </div>
      <button class="stop" :disabled="busy" @click="stop"><Square :size="15" /> Denemeyi bitir ve kaydet</button>
    </div>

    <div class="preset-grid">
      <article v-for="preset in status.presets" :key="preset.preset_id" :class="{ selected: status.active_trial?.preset_id === preset.preset_id }">
        <div class="preset-title"><Gauge :size="17" /><div><b>{{ preset.name }}</b><small>{{ preset.algorithm }}</small></div></div>
        <p>{{ preset.description }}</p>
        <div class="config-line">
          <span>Kp {{ preset.config.pid_kp_x }}/{{ preset.config.pid_kp_y }}</span>
          <span>Kd {{ preset.config.pid_kd_x }}/{{ preset.config.pid_kd_y }}</span>
          <span>Hız X/Y {{ preset.config.max_speed_x ?? preset.config.max_speed }}/{{ preset.config.max_speed_y ?? preset.config.max_speed }}</span>
          <span>{{ preset.config.lead_enabled ? 'Lead açık' : 'Lead kapalı' }}</span>
        </div>
        <div class="preset-actions">
          <button class="ghost" :disabled="busy" @click="loadPresetToDraft(preset)"><SlidersHorizontal :size="14" /> Değerleri al</button>
          <button :disabled="busy || !!status.active_trial" @click="start(preset.preset_id)"><Play :size="14" /> Bu profille dene</button>
        </div>
      </article>
    </div>

    <p class="message">{{ message }}</p>

    <div v-if="rankedResults.length" class="results">
      <h4>Kaydedilmiş denemeler</h4>
      <article v-for="(result, index) in rankedResults" :key="result.trial_id">
        <div class="rank">#{{ index + 1 }}</div>
        <div class="result-name"><b>{{ result.preset_name }}</b><small>{{ result.duration_s }} s · {{ result.samples }} kare</small></div>
        <div><b>{{ result.mean_error_px }} px</b><small>Ort. hata</small></div>
        <div><b>{{ result.p95_error_px }} px</b><small>P95 hata</small></div>
        <div><b>{{ Math.round(result.loss_ratio * 100) }}%</b><small>Hedef kaybı</small></div>
        <div><b>{{ result.technical_score }}</b><small>Teknik puan</small></div>
        <div class="stars"><button v-for="star in 5" :key="star" :class="{ on: star <= (result.operator_rating ?? 0) }" @click="rate(result.trial_id, star)"><Star :size="13" /></button></div>
        <button class="apply" :disabled="busy || !!status.active_trial" @click="applyWinner(result.preset_id)"><Check :size="13" /> Uygula</button>
      </article>
    </div>
  </section>
</template>

<style scoped>
.tuning-panel{display:grid;gap:12px;color:#e8f7ff}.tuning-panel>header{display:flex;justify-content:space-between;gap:12px;align-items:flex-start}.tuning-panel header p{margin:0;color:#5ee5fb;font-size:.59rem;font-weight:900;letter-spacing:.17em}.tuning-panel h3{margin:4px 0 0;font-size:1rem}.tuning-panel header>span{display:flex;align-items:center;gap:5px;border:1px solid #ffffff1c;border-radius:999px;padding:6px 8px;color:#93aabd;font-size:.62rem;font-weight:900}.tuning-panel header>span.live{border-color:#40d99b66;color:#7bf1bd;background:#0b392b88}.active-trial{display:grid;gap:10px;padding:12px;border:1px solid #42dda05c;border-radius:12px;background:#08291f}.active-trial small,.results small{display:block;color:#91a9b9;font-size:.61rem}.active-trial em{display:block;color:#70e8f7;font-size:.68rem;font-style:normal}.live-metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:6px}.live-metrics span{padding:7px;border-radius:8px;background:#031610;text-align:center}.stop,.preset-grid button,.apply{display:flex;align-items:center;justify-content:center;gap:6px;border:1px solid #ffffff20;border-radius:8px;padding:8px;background:#0b2030;color:#d9f7ff;font-size:.68rem;font-weight:900;cursor:pointer}.stop{border-color:#fa718866;background:#41151d;color:#ffd6dc}.preset-grid{display:grid;grid-template-columns:1fr 1fr;gap:8px}.preset-grid article{display:grid;gap:8px;padding:11px;border:1px solid #ffffff16;border-radius:11px;background:#071523}.preset-grid article.selected{border-color:#53e3ae;background:#092a21}.preset-title{display:flex;gap:8px;align-items:center}.preset-title>b,.preset-title b{display:block;font-size:.76rem}.preset-title small{display:block;color:#63ddec;font-size:.6rem}.preset-grid p{min-height:32px;margin:0;color:#9db1bf;font-size:.64rem;line-height:1.4}.config-line{display:flex;gap:5px;flex-wrap:wrap}.config-line span{padding:3px 5px;border-radius:5px;background:#020b14;color:#a8bdcb;font:600 .56rem ui-monospace,monospace}.message{margin:0;padding:8px 10px;border-left:3px solid #55daeb;background:#08202c;color:#bdeef5;font-size:.67rem}.results{display:grid;gap:6px}.results h4{margin:4px 0;font-size:.75rem}.results article{display:grid;grid-template-columns:28px minmax(100px,1.5fr) repeat(4,minmax(58px,1fr));gap:6px;align-items:center;padding:8px;border:1px solid #ffffff12;border-radius:9px;background:#06111d}.results article>div:not(.stars){min-width:0}.results article>div>b{font-size:.66rem}.rank{color:#5ee5fb;font-weight:900}.stars{display:flex;grid-column:2/6;gap:2px}.stars button{border:0;background:transparent;color:#425469;padding:2px;cursor:pointer}.stars button.on{color:#facc15}.apply{grid-column:6;padding:6px}.tuning-panel button:hover:not(:disabled){filter:brightness(1.18);transform:translateY(-1px)}.tuning-panel button:disabled{opacity:.4;cursor:not-allowed}@media(max-width:560px){.preset-grid{grid-template-columns:1fr}.live-metrics{grid-template-columns:1fr 1fr}.results article{grid-template-columns:28px 1fr 1fr}.stars,.apply{grid-column:auto}}
.live-editor{display:grid;gap:10px;padding:13px;border:1px solid rgba(94,229,251,.22);border-radius:13px;background:linear-gradient(145deg,rgba(8,31,48,.94),rgba(4,15,26,.98));box-shadow:inset 0 1px rgba(255,255,255,.025)}
.live-editor.dirty{border-color:rgba(250,204,21,.42)}
.live-editor-head,.live-editor-head>div,.live-readback,.editor-actions,.preset-actions{display:flex;align-items:center}
.live-editor-head{justify-content:space-between;gap:12px}.live-editor-head>div:first-child{gap:8px;color:#67e8f9}.live-editor-head span b{display:block;color:#f4fbff;font-size:.8rem}.live-editor-head span small{display:block;margin-top:2px;color:#8fa9b8;font-size:.61rem}.revision{text-align:right}.revision b{display:block;color:#7bf1bd;font:800 .65rem ui-monospace,monospace}.revision small{display:block;color:#748b9a;font-size:.58rem}
.axis-grid{display:grid;grid-template-columns:1fr 1fr;gap:8px}.axis-grid fieldset{display:grid;grid-template-columns:repeat(3,1fr);gap:7px;margin:0;padding:10px;border:1px solid rgba(148,163,184,.15);border-radius:10px;background:rgba(2,9,17,.68)}.axis-grid legend{padding:0 5px;color:#72ddeb;font-size:.58rem;font-weight:900;letter-spacing:.1em}.axis-grid label,.runtime-grid label{display:grid;gap:4px}.axis-grid label span,.runtime-grid label span{color:#8fa9b8;font-size:.58rem;font-weight:800}.axis-grid input,.runtime-grid input{width:100%;box-sizing:border-box;border:1px solid rgba(148,163,184,.2);border-radius:7px;background:#020914;padding:7px 8px;color:#f4fbff;font:700 .72rem ui-monospace,monospace;outline:none}.axis-grid input:focus,.runtime-grid input:focus{border-color:#52dceb;box-shadow:0 0 0 2px rgba(82,220,235,.09)}
.runtime-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}.runtime-grid label{position:relative;padding:8px;border:1px solid rgba(148,163,184,.12);border-radius:9px;background:rgba(3,13,23,.72)}.runtime-grid em{position:absolute;right:16px;bottom:15px;color:#6f8796;font-size:.56rem;font-style:normal}
.live-readback{justify-content:space-between;gap:10px;padding:8px 9px;border-radius:8px;background:rgba(2,9,17,.75);color:#a6c4d2;font:600 .61rem ui-monospace,monospace}.live-readback span{display:flex;align-items:center;gap:6px}.live-readback b{color:#7bf1bd;font-size:.56rem;letter-spacing:.08em}.dirty .live-readback b{color:#fde68a}
.editor-actions{justify-content:flex-end;gap:7px}.editor-actions button{display:flex;align-items:center;justify-content:center;gap:6px;min-height:33px;border-radius:8px;padding:0 12px;font-size:.66rem;font-weight:900;cursor:pointer}.reset{border:1px solid rgba(148,163,184,.22);background:#071523;color:#a9becb}.apply-live{border:1px solid rgba(52,211,153,.48);background:linear-gradient(180deg,#0c684e,#084a39);color:#d1fae5}.preset-actions{gap:6px}.preset-actions>button{flex:1}.preset-grid button.ghost{border-color:rgba(94,229,251,.2);background:#07131f;color:#9ed9e3}
@media(max-width:760px){.axis-grid{grid-template-columns:1fr}.runtime-grid{grid-template-columns:1fr 1fr}.live-readback{align-items:flex-start;flex-direction:column}}@media(max-width:460px){.runtime-grid{grid-template-columns:1fr}.axis-grid fieldset{grid-template-columns:1fr 1fr 1fr}}
.axis-grid fieldset{grid-template-columns:repeat(4,1fr)}
.runtime-grid{grid-template-columns:repeat(2,1fr)}
@media(max-width:460px){.axis-grid fieldset{grid-template-columns:1fr 1fr}}
</style>
