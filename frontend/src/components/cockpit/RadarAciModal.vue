<script setup lang="ts">
// Ust bardaki "Radar" dugmesi: radar supurme acisini "sol X + sag Y" olarak ayarlar.
// Radar, merkezden (varsayilan 135 = AutoHome merkezi) X derece sola ve Y derece saga supurur.
// Mevcut /api/operation/patrol/angles yolu kullanilir: path2 (supurme merkezi) = merkez + (Y - X) / 2,
// sweep_sector = X + Y. Kalici kaydedilir (patrol_radar.active.json); surum gecisinde de tasinir.
import { computed, ref, watch } from 'vue'
import { fetchPatrolStatus, updatePatrolAngles } from '../../api/operation'
import { fetchMotionEnvelope } from '../../api/hardware'

const props = defineProps<{ open: boolean }>()
const emit = defineEmits<{ close: [] }>()

const MERKEZ_ANAHTAR = 'istiklal_radar_merkez'
const merkez = ref(135)
const sol = ref(15)
const sag = ref(15)
const aktif = ref<{ path2: number, sektor: number } | null>(null)
const durum = ref<any>(null)
const zarf = ref<{ pan_min_deg: number, pan_max_deg: number } | null>(null)
const busy = ref(false)
const mesaj = ref('')
const hata = ref(false)

const yuvarla = (v: number) => Math.round(v * 10) / 10

function merkezOku(): number | null {
  try {
    const v = Number(localStorage.getItem(MERKEZ_ANAHTAR))
    return Number.isFinite(v) && v > 0 ? v : null
  } catch { return null }
}

async function yukle(): Promise<void> {
  mesaj.value = ''
  hata.value = false
  try {
    const s = await fetchPatrolStatus()
    durum.value = s
    const p2 = Number(s.path2_deg ?? 135)
    const sek = Number(s.sweep_sector_deg ?? 30)
    aktif.value = { path2: p2, sektor: sek }
    const m = merkezOku()
    const alt = p2 - sek / 2
    const ust = p2 + sek / 2
    if (m !== null && m >= alt - 0.05 && m <= ust + 0.05) {
      merkez.value = m
      sol.value = yuvarla(m - alt)
      sag.value = yuvarla(ust - m)
    } else {
      merkez.value = p2
      sol.value = yuvarla(sek / 2)
      sag.value = yuvarla(sek / 2)
    }
  } catch (e) {
    hata.value = true
    mesaj.value = 'Radar ayarı okunamadı: ' + (e instanceof Error ? e.message : '')
  }
  try {
    const z = await fetchMotionEnvelope()
    zarf.value = { pan_min_deg: z.envelope.pan_min_deg, pan_max_deg: z.envelope.pan_max_deg }
  } catch { zarf.value = null }
}

watch(() => props.open, (acik) => { if (acik) void yukle() }, { immediate: true })

const aralik = computed(() => ({ alt: yuvarla(merkez.value - sol.value), ust: yuvarla(merkez.value + sag.value) }))
const aktifAralik = computed(() => aktif.value ? { alt: yuvarla(aktif.value.path2 - aktif.value.sektor / 2), ust: yuvarla(aktif.value.path2 + aktif.value.sektor / 2) } : null)

const dogrulama = computed(() => {
  const h: string[] = []
  const u: string[] = []
  const x = Number(sol.value), y = Number(sag.value), m = Number(merkez.value)
  if (![x, y, m].every((v) => Number.isFinite(v))) h.push('Üç değeri de girin.')
  else {
    if (x < 0 || y < 0) h.push('Sol ve sağ açı 0 ya da daha büyük olmalı.')
    if (x + y < 5) h.push('Toplam tarama (sol + sağ) en az 5° olmalı.')
    if (x + y > 270) h.push('Toplam tarama (sol + sağ) en fazla 270° olabilir.')
    if (m - x < 0 || m + y > 270) h.push('Tarama 0–270° dışına çıkıyor.')
    if (zarf.value && (m - x < zarf.value.pan_min_deg || m + y > zarf.value.pan_max_deg)) {
      u.push(`Tarama, aktif hareket limitinin (${zarf.value.pan_min_deg}–${zarf.value.pan_max_deg}°) dışına taşıyor; taret limitte durur.`)
    }
    if (x + y > 60) u.push(`Geniş tarama: radar ~9,4°/s döndüğü için bir tur ~${Math.round(2 * (x + y) / 9.4)} s sürer; aynı yeri daha seyrek tarar.`)
    if (Math.abs(m - 135) > 5) u.push('Merkez 135°’ten (AutoHome merkezi) farklı: radar önce 135°’e döner, sonra bu aralığa geçer.')
  }
  return { hatalar: h, uyarilar: u }
})

async function uygula(): Promise<void> {
  busy.value = true
  hata.value = false
  mesaj.value = 'Uygulanıyor…'
  const x = Number(sol.value), y = Number(sag.value), m = Number(merkez.value)
  const s = durum.value ?? {}
  try {
    const r = await updatePatrolAngles({
      path1_deg: s.path1_deg,
      path2_deg: yuvarla(m + (y - x) / 2),
      path3_deg: s.path3_deg,
      dwell_time_s: s.dwell_time_s,
      sweep_sector_deg: yuvarla(x + y),
    })
    try { localStorage.setItem(MERKEZ_ANAHTAR, String(m)) } catch { /* yok say */ }
    aktif.value = { path2: Number(r.path2_deg), sektor: Number(r.sweep_sector_deg) }
    durum.value = r
    mesaj.value = `Uygulandı: radar ${aktifAralik.value?.alt}° – ${aktifAralik.value?.ust}° arasında tarar (sol ${x}°, sağ ${y}°).`
  } catch (e) {
    hata.value = true
    mesaj.value = 'Uygulanamadı: ' + (e instanceof Error ? e.message : '')
  } finally {
    busy.value = false
  }
}

function varsayilan(): void {
  merkez.value = 135
  sol.value = 15
  sag.value = 15
}
</script>

<template>
  <div v-if="open" class="ra-arka" @click.self="emit('close')">
    <section class="ra" role="dialog" aria-label="Radar tarama açısı">
      <header>
        <div><p>RADAR TARAMA AÇISI</p><h3>Merkezden sola X°, sağa Y° tarar</h3></div>
        <button class="kapat" type="button" title="Kapat" @click="emit('close')">✕</button>
      </header>

      <div class="durum">
        <div><small>ŞU ANKİ TARAMA</small><b>{{ aktifAralik ? `${aktifAralik.alt}° – ${aktifAralik.ust}°` : '—' }}</b></div>
        <div><small>ŞU ANKİ TOPLAM</small><b>{{ aktif ? `${aktif.sektor}°` : '—' }}</b></div>
        <div><small>RADAR DURUMU</small><b>{{ durum?.state ?? '—' }}</b></div>
      </div>

      <div class="formul">
        <label>Merkez<input v-model.number="merkez" type="number" step="0.5" min="0" max="270"><span>°</span></label>
        <label>Sol X<input v-model.number="sol" type="number" step="1" min="0" max="270"><span>°</span></label>
        <label>Sağ Y<input v-model.number="sag" type="number" step="1" min="0" max="270"><span>°</span></label>
      </div>
      <div class="sonuc">Tarama: <b>{{ aralik.alt }}°</b> ← merkez {{ merkez }}° → <b>{{ aralik.ust }}°</b> · toplam {{ yuvarla(Number(sol) + Number(sag)) }}°</div>

      <ul v-if="dogrulama.hatalar.length" class="liste hata"><li v-for="m in dogrulama.hatalar" :key="m">{{ m }}</li></ul>
      <ul v-if="dogrulama.uyarilar.length" class="liste uyari"><li v-for="m in dogrulama.uyarilar" :key="m">{{ m }}</li></ul>

      <div class="eylem">
        <button class="ana" type="button" :disabled="busy || dogrulama.hatalar.length > 0" @click="uygula">{{ busy ? 'İşlem sürüyor…' : 'Uygula' }}</button>
        <button type="button" :disabled="busy" @click="varsayilan">Varsayılan (135° · 15 + 15)</button>
        <button class="soluk" type="button" :disabled="busy" @click="yukle">Yenile</button>
      </div>
      <div v-if="mesaj" class="mesaj" :class="{ kotu: hata }">{{ mesaj }}</div>
      <p class="not">Açılar AutoHome referanslı (0° = sol switch, 135° = merkez). Aşama 2/3 çalışırken de uygulanabilir; yeni aralık hemen kullanılır, kalıcı kaydedilir ve sürüm geçişinde taşınır. Radar yine önce merkeze (135°) döner, sonra tarar.</p>
    </section>
  </div>
</template>

<style scoped>
.ra-arka{position:fixed;inset:0;z-index:1200;display:grid;place-items:center;padding:16px;background:rgba(2,8,16,.72)}
.ra{display:grid;gap:12px;width:min(620px,100%);max-height:calc(100vh - 32px);overflow:auto;padding:18px;border:1px solid #39cbe655;border-radius:16px;background:linear-gradient(145deg,#081a28,#06131f);color:#eaf8ff;box-shadow:0 24px 60px rgba(0,0,0,.55)}
.ra header{display:flex;justify-content:space-between;align-items:flex-start;gap:12px}
.ra header p{margin:0;color:#54ddeb;font-size:.64rem;font-weight:900;letter-spacing:.16em}
.ra header h3{margin:4px 0 0;font-size:1.02rem}
.kapat{border:1px solid #ffffff22;border-radius:8px;background:#0b1d2b;color:#cfe;padding:6px 10px;cursor:pointer}
.durum{display:grid;grid-template-columns:repeat(3,1fr);gap:7px}
.durum>div{display:grid;gap:3px;padding:9px;border:1px solid #ffffff12;border-radius:9px;background:#03101a}
.durum small{color:#7f9aaa;font-size:.58rem;font-weight:800}
.durum b{font:800 .8rem ui-monospace,monospace}
.formul{display:flex;flex-wrap:wrap;align-items:flex-end;gap:8px}
.formul label{display:grid;gap:4px;color:#a9bfcc;font-size:.68rem;font-weight:800}
.formul label span{display:none}
.formul input{width:92px;box-sizing:border-box;border:1px solid #55dbea45;border-radius:7px;background:#020a12;color:#ecfbff;padding:9px;font:700 1rem ui-monospace,monospace}
.op{padding-bottom:10px;color:#54ddeb}
.sonuc{padding:9px 11px;border-radius:8px;background:#03101a;color:#cfefff;font-size:.8rem}
.sonuc b{color:#fff;font-family:ui-monospace,monospace}
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
@media(max-width:600px){.durum{grid-template-columns:1fr 1fr}}
</style>
