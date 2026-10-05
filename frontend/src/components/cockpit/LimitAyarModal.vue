<script setup lang="ts">
// Ust bardaki "Limitler" dugmesiyle acilan hareket limiti penceresi.
// Acilar AutoHome referansina goredir (Pico'nun POZ derecesi): PAN 0-270 (0 = sol switch, merkez 135),
// TILT 0-60 (0 = ust switch, merkez 30). Uygulama mevcut /api/hardware/motion-envelope yolunu kullanir:
// Pico'ya LIMIT,PAN / LIMIT,TILT gider (firmware AutoHome sonrasi bu acilarin disina cikmaz) ve
// yazilim tarafi yumusak limitleri de ayni araliga ayarlanir.
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { fetchMotionEnvelope, updateMotionEnvelope, type HardwareMotionEnvelope } from '../../api/hardware'

const props = defineProps<{ open: boolean }>()
const emit = defineEmits<{ close: [] }>()

const TAM: HardwareMotionEnvelope = { pan_min_deg: 0, pan_max_deg: 270, tilt_min_deg: 0, tilt_max_deg: 60 }
const form = reactive<HardwareMotionEnvelope>({ ...TAM })
const aktif = ref<HardwareMotionEnvelope | null>(null)
const pan = ref<number | null>(null)
const tilt = ref<number | null>(null)
const homed = ref<boolean | null>(null)
const busy = ref(false)
const mesaj = ref('')
const hata = ref(false)
let timer: ReturnType<typeof setInterval> | null = null

function apiBase(): string {
  const configured = import.meta.env.VITE_BACKEND_API_URL as string | undefined
  if (configured) return configured.replace(/\/$/, '')
  if (window.location.port && window.location.port !== '5173') return window.location.origin
  return `${window.location.protocol}//${window.location.hostname}:8000`
}

async function konumOku(): Promise<void> {
  try {
    const r = await fetch(`${apiBase()}/api/motion/status`)
    if (!r.ok) return
    const s = await r.json()
    pan.value = typeof s.physical_pan_deg === 'number' ? s.physical_pan_deg : null
    tilt.value = typeof s.physical_tilt_deg === 'number' ? s.physical_tilt_deg : null
    homed.value = typeof s.homed === 'boolean' ? s.homed : null
  } catch { /* baglanti yoksa gostergeler bos kalir */ }
}

async function yukle(): Promise<void> {
  mesaj.value = ''
  hata.value = false
  try {
    const r = await fetchMotionEnvelope()
    aktif.value = { ...r.envelope }
    Object.assign(form, r.envelope)
  } catch (e) {
    hata.value = true
    mesaj.value = 'Aktif limitler okunamadı: ' + (e instanceof Error ? e.message : '')
  }
  await konumOku()
}

watch(() => props.open, (acik) => {
  if (timer) { clearInterval(timer); timer = null }
  if (acik) {
    void yukle()
    timer = setInterval(() => { void konumOku() }, 700)
  }
}, { immediate: true })
onBeforeUnmount(() => { if (timer) clearInterval(timer) })

const tamAralik = (e: HardwareMotionEnvelope | null) => !!e && e.pan_min_deg <= 0 && e.pan_max_deg >= 270 && e.tilt_min_deg <= 0 && e.tilt_max_deg >= 60

const dogrulama = computed(() => {
  const h: string[] = []
  const u: string[] = []
  const f = form
  const sayi = [f.pan_min_deg, f.pan_max_deg, f.tilt_min_deg, f.tilt_max_deg]
  if (sayi.some((v) => typeof v !== 'number' || Number.isNaN(v))) h.push('Dört açının hepsini girin.')
  if (f.pan_min_deg < 0 || f.pan_max_deg > 270) h.push('X (yatay) 0–270° arasında olmalı.')
  if (f.tilt_min_deg < 0 || f.tilt_max_deg > 60) h.push('Y (dikey) 0–60° arasında olmalı.')
  if (f.pan_max_deg - f.pan_min_deg < 2) h.push('X: bitiş, başlangıçtan en az 2° büyük olmalı.')
  if (f.tilt_max_deg - f.tilt_min_deg < 2) h.push('Y: bitiş, başlangıçtan en az 2° büyük olmalı.')
  if (!h.length) {
    if (f.pan_min_deg > 120 || f.pan_max_deg < 150) u.push('Radar merkezi 135° ve tarama ~120–151° bu X aralığının dışında kalıyor: otonomda radar merkeze varamayabilir / tam süpüremez.')
    if (f.tilt_min_deg > 30 || f.tilt_max_deg < 30) u.push('Radar tarama yüksekliği (Y 30°) aralığın dışında: otonomda radar merkeze varamaz.')
    if (pan.value !== null && tilt.value !== null && homed.value && (pan.value < f.pan_min_deg || pan.value > f.pan_max_deg || tilt.value < f.tilt_min_deg || tilt.value > f.tilt_max_deg)) {
      u.push('Taret şu an bu aralığın DIŞINDA. Uygulanınca yalnız aralığa doğru hareket edebilir; önce içeri getirmeniz iyi olur.')
    }
  }
  if (homed.value === false) u.push('Bu oturumda AutoHome yapılmadı: açılar henüz geçersiz. Limit kaydedilir, AutoHome tamamlanınca otomatik devreye girer.')
  return { hatalar: h, uyarilar: u }
})

function simdikiKonum(alan: keyof HardwareMotionEnvelope): void {
  const v = alan.startsWith('pan') ? pan.value : tilt.value
  if (v === null) return
  form[alan] = Math.round(v * 10) / 10
}

async function uygula(e: HardwareMotionEnvelope): Promise<void> {
  busy.value = true
  hata.value = false
  mesaj.value = 'Uygulanıyor…'
  try {
    const r = await updateMotionEnvelope({ ...e })
    if (r.accepted) {
      aktif.value = { ...r.envelope }
      Object.assign(form, r.envelope)
      mesaj.value = tamAralik(r.envelope)
        ? 'Limitler kaldırıldı (tam aralık). Pico onayladı.'
        : `Uygulandı, Pico onayladı: X ${r.envelope.pan_min_deg}–${r.envelope.pan_max_deg}°, Y ${r.envelope.tilt_min_deg}–${r.envelope.tilt_max_deg}°`
      if (r.reason_codes.includes('PICO_TILT_LIMIT_SOFTWARE_ONLY')) {
        mesaj.value += ' · Y (dikey) limiti yazılımla, Pico\'nun bildirdiği gerçek konuma göre uygulanır; sınırı birkaç derece aşıp durabilir.'
      }
      window.dispatchEvent(new CustomEvent('istiklal:motion-envelope-updated', { detail: r.envelope }))
    } else {
      hata.value = true
      mesaj.value = 'REDDEDİLDİ: ' + (r.reason_codes.join(' · ') || r.detail)
    }
  } catch (err) {
    hata.value = true
    mesaj.value = 'Uygulanamadı: ' + (err instanceof Error ? err.message : '')
  } finally {
    busy.value = false
  }
}

const fmt = (v: number | null) => (v === null ? '—' : v.toFixed(1) + '°')
</script>

<template>
  <div v-if="open" class="lm-arka" @click.self="emit('close')">
    <section class="lm" role="dialog" aria-label="Hareket limitleri">
      <header>
        <div><p>HAREKET LİMİTLERİ</p><h3>Taret yalnız bu açılar arasında hareket eder</h3></div>
        <button class="kapat" type="button" title="Kapat" @click="emit('close')">✕</button>
      </header>

      <div class="durum">
        <div><small>AUTOHOME</small><b :class="homed ? 'iyi' : homed === false ? 'kotu' : ''">{{ homed ? 'YAPILDI' : homed === false ? 'YAPILMADI' : '?' }}</b></div>
        <div><small>ŞU AN X (yatay)</small><b>{{ fmt(pan) }}</b></div>
        <div><small>ŞU AN Y (dikey)</small><b>{{ fmt(tilt) }}</b></div>
        <div><small>AKTİF LİMİT</small><b>{{ aktif ? (tamAralik(aktif) ? 'YOK (tam)' : `X ${aktif.pan_min_deg}–${aktif.pan_max_deg} · Y ${aktif.tilt_min_deg}–${aktif.tilt_max_deg}`) : '—' }}</b></div>
      </div>

      <div class="grid">
        <fieldset>
          <legend>X · YATAY (PAN) · 0–270° · merkez 135°</legend>
          <label>Başlangıç<input v-model.number="form.pan_min_deg" type="number" min="0" max="270" step="1"><button type="button" :disabled="pan === null" title="Taretin şu anki yatay açısını yaz" @click="simdikiKonum('pan_min_deg')">şu an</button></label>
          <label>Bitiş<input v-model.number="form.pan_max_deg" type="number" min="0" max="270" step="1"><button type="button" :disabled="pan === null" title="Taretin şu anki yatay açısını yaz" @click="simdikiKonum('pan_max_deg')">şu an</button></label>
          <small class="ipucu">0° = sol limit switch · 270° = en sağ</small>
        </fieldset>
        <fieldset>
          <legend>Y · DİKEY (TILT) · 0–60° · merkez 30°</legend>
          <label>Başlangıç<input v-model.number="form.tilt_min_deg" type="number" min="0" max="60" step="1"><button type="button" :disabled="tilt === null" title="Taretin şu anki dikey açısını yaz" @click="simdikiKonum('tilt_min_deg')">şu an</button></label>
          <label>Bitiş<input v-model.number="form.tilt_max_deg" type="number" min="0" max="60" step="1"><button type="button" :disabled="tilt === null" title="Taretin şu anki dikey açısını yaz" @click="simdikiKonum('tilt_max_deg')">şu an</button></label>
          <small class="ipucu">0° = üst limit switch · 60° = en alt · tam aralık (0–60) = dikey limit yok</small>
        </fieldset>
      </div>

      <ul v-if="dogrulama.hatalar.length" class="liste hata"><li v-for="m in dogrulama.hatalar" :key="m">{{ m }}</li></ul>
      <ul v-if="dogrulama.uyarilar.length" class="liste uyari"><li v-for="m in dogrulama.uyarilar" :key="m">{{ m }}</li></ul>

      <div class="eylem">
        <button class="ana" type="button" :disabled="busy || dogrulama.hatalar.length > 0" @click="uygula({ ...form })">{{ busy ? 'İşlem sürüyor…' : 'Limitleri uygula' }}</button>
        <button type="button" :disabled="busy" @click="uygula({ ...TAM })">Limitleri kaldır (tam aralık)</button>
        <button class="soluk" type="button" :disabled="busy" @click="yukle">Yenile</button>
      </div>
      <div v-if="mesaj" class="mesaj" :class="{ kotu: hata }">{{ mesaj }}</div>
      <p class="not">Açılar AutoHome referansına göredir. Limit Pico'da ve yazılımda uygulanır, kalıcı kaydedilir. Kokpitteki 🏠 Auto-Home her tamamlandığında kayıtlı limitler Pico'ya otomatik yeniden gönderilir (Pico yeniden başlasa bile).</p>
    </section>
  </div>
</template>

<style scoped>
.lm-arka{position:fixed;inset:0;z-index:1200;display:grid;place-items:center;padding:16px;background:rgba(2,8,16,.72)}
.lm{display:grid;gap:12px;width:min(720px,100%);max-height:calc(100vh - 32px);overflow:auto;padding:18px;border:1px solid #39cbe655;border-radius:16px;background:linear-gradient(145deg,#081a28,#06131f);color:#eaf8ff;box-shadow:0 24px 60px rgba(0,0,0,.55)}
.lm header{display:flex;justify-content:space-between;align-items:flex-start;gap:12px}
.lm header p{margin:0;color:#54ddeb;font-size:.64rem;font-weight:900;letter-spacing:.16em}
.lm header h3{margin:4px 0 0;font-size:1.02rem}
.kapat{border:1px solid #ffffff22;border-radius:8px;background:#0b1d2b;color:#cfe;padding:6px 10px;cursor:pointer}
.durum{display:grid;grid-template-columns:repeat(4,1fr);gap:7px}
.durum>div{display:grid;gap:3px;padding:9px;border:1px solid #ffffff12;border-radius:9px;background:#03101a}
.durum small{color:#7f9aaa;font-size:.58rem;font-weight:800}
.durum b{font:800 .8rem ui-monospace,monospace}
.durum b.iyi{color:#75edb4}.durum b.kotu{color:#ffafb7}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.grid fieldset{display:grid;gap:8px;margin:0;padding:12px;border:1px solid #ffffff1c;border-radius:10px}
.grid legend{padding:0 5px;color:#9dc0cf;font-size:.68rem;font-weight:900}
.grid label{display:grid;grid-template-columns:70px 1fr auto;align-items:center;gap:6px;color:#a9bfcc;font-size:.72rem}
.grid input{width:100%;box-sizing:border-box;border:1px solid #55dbea45;border-radius:7px;background:#020a12;color:#ecfbff;padding:8px;font:700 .9rem ui-monospace,monospace}
.grid label button{border:1px solid #49dbea44;border-radius:7px;background:#07202c;color:#bfefff;padding:7px 8px;font-size:.64rem;font-weight:800;cursor:pointer}
.ipucu{color:#7f9aaa;font-size:.62rem}
.liste{margin:0;padding:8px 10px 8px 26px;border-radius:8px;font-size:.7rem;line-height:1.45}
.liste.hata{background:#31171b;color:#ffd0d4;border-left:3px solid #f16d78}
.liste.uyari{background:#2e2610;color:#ffe3a3;border-left:3px solid #f5c04e}
.eylem{display:flex;flex-wrap:wrap;gap:8px}
.eylem button{border:1px solid #49dbea55;border-radius:9px;background:#0b3343;color:#dffbff;padding:10px 13px;font-size:.74rem;font-weight:900;cursor:pointer}
.eylem button.ana{background:#0e5a52;border-color:#35d09788}
.eylem button.soluk{background:#07141f;color:#a9bfcc}
.eylem button:disabled{opacity:.45;cursor:not-allowed}
.mesaj{padding:9px 11px;border-left:3px solid #35d097;border-radius:6px;background:#08251e;color:#bdebd7;font-size:.72rem}
.mesaj.kotu{border-left-color:#f16d78;background:#31171b;color:#ffd0d4}
.not{margin:0;color:#7f9aaa;font-size:.64rem;line-height:1.45}
@media(max-width:700px){.durum{grid-template-columns:1fr 1fr}.grid{grid-template-columns:1fr}}
</style>
