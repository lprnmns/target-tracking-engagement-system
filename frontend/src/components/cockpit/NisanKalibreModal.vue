<script setup lang="ts">
// Ust bardaki "Kalibre" dugmesi: kagit hedefe atis yapildiktan sonra grubun merkezi hedef merkezinden
// kac cm yukari/asagi, saga/sola gitti yazilir; bizim kameraya gore piksele cevrilip nisan noktasi
// (aim_offset) kaydirilir. Mantik: nisangah, saçmanin gercekten dustugu yere tasinir.
// Kamera olcegi (1 Ekim olcumu): taret POZ (~41) + 16 cm balon 15 m/5 m isaretleri (~46) -> ~44 px/derece -> f ~ 2520 px.
// Kayit mevcut /api/calibration/zeroing yolu ile config.yaml'a yazilir; surum gecisinde tasinir.
import { computed, ref, watch } from 'vue'

const props = defineProps<{ open: boolean }>()
const emit = defineEmits<{ close: [] }>()

const ODAK_PX = 2520 // 1280x720 kameramiz; px = cm/100 * ODAK_PX / mesafe_m
const VARSAYILAN = { x: 0, y: -22 }
const GECMIS_ANAHTAR = 'istiklal_nisan_kalibre_gecmis'

const mevcut = ref<{ x: number, y: number } | null>(null)
const mesafe = ref(10)
const dikeyCm = ref(0)
const dikeyYon = ref<'asagi' | 'yukari'>('asagi')
const yatayCm = ref(0)
const yatayYon = ref<'saga' | 'sola'>('saga')
const busy = ref(false)
const mesaj = ref('')
const hata = ref(false)
const gecmis = ref<Array<{ zaman: string, x: number, y: number, not: string }>>([])

const yuvarla = (v: number) => Math.round(v * 10) / 10

function gecmisOku(): void {
  try { gecmis.value = JSON.parse(localStorage.getItem(GECMIS_ANAHTAR) || '[]') } catch { gecmis.value = [] }
}
function gecmisYaz(): void {
  try { localStorage.setItem(GECMIS_ANAHTAR, JSON.stringify(gecmis.value.slice(0, 15))) } catch { /* yok say */ }
}

async function yukle(): Promise<void> {
  mesaj.value = ''
  hata.value = false
  gecmisOku()
  try {
    const r = await fetch('/api/calibration/zeroing')
    if (!r.ok) throw new Error(`HTTP ${r.status}`)
    const j = await r.json()
    mevcut.value = { x: Number(j.aim_offset_x_px), y: Number(j.aim_offset_y_px) }
  } catch (e) {
    hata.value = true
    mesaj.value = 'Mevcut nişan ofseti okunamadı: ' + (e instanceof Error ? e.message : '')
  }
}

watch(() => props.open, (acik) => { if (acik) void yukle() }, { immediate: true })

const pxPerCm = computed(() => (Number(mesafe.value) > 0 ? ODAK_PX / (100 * Number(mesafe.value)) : 0))
const dikeyPx = computed(() => yuvarla(Number(dikeyCm.value || 0) * pxPerCm.value * (dikeyYon.value === 'asagi' ? 1 : -1)))
const yatayPx = computed(() => yuvarla(Number(yatayCm.value || 0) * pxPerCm.value * (yatayYon.value === 'saga' ? 1 : -1)))
const yeni = computed(() => mevcut.value ? { x: yuvarla(mevcut.value.x + yatayPx.value), y: yuvarla(mevcut.value.y + dikeyPx.value) } : null)

const dogrulama = computed(() => {
  const h: string[] = []
  const u: string[] = []
  const d = Number(mesafe.value)
  if (!Number.isFinite(d) || d < 2 || d > 30) h.push('Mesafe 2–30 m arasında olmalı.')
  if (Number(dikeyCm.value) < 0 || Number(yatayCm.value) < 0) h.push('cm değerlerini pozitif girin; yönü düğmeyle seçin.')
  if (Number(dikeyCm.value) > 40 || Number(yatayCm.value) > 40) u.push('40 cm’den büyük sapma: önce ölçümü kontrol edin (yanlış yön/mesafe olabilir).')
  if (yeni.value && (Math.abs(yeni.value.x) > 150 || Math.abs(yeni.value.y) > 150)) h.push('Yeni ofset ±150 px’i aşıyor; güvenlik için kaydedilmez.')
  if (dikeyPx.value === 0 && yatayPx.value === 0) u.push('Değişiklik yok: en az bir sapma girin.')
  return { hatalar: h, uyarilar: u }
})

async function kaydet(x: number, y: number, not: string, gecmiseEkle = true): Promise<void> {
  busy.value = true
  hata.value = false
  mesaj.value = 'Kaydediliyor…'
  const onceki = mevcut.value
  try {
    const r = await fetch('/api/calibration/zeroing', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ aim_offset_x_px: x, aim_offset_y_px: y, distance_m: Math.min(50, Math.max(1, Number(mesafe.value) || 10)), persist: true }),
    })
    if (!r.ok) throw new Error(`HTTP ${r.status}`)
    const j = await r.json()
    if (onceki && gecmiseEkle) {
      gecmis.value.unshift({ zaman: new Date().toLocaleTimeString('tr-TR'), x: onceki.x, y: onceki.y, not: 'önceki' })
      gecmisYaz()
    }
    mevcut.value = { x: Number(j.aim_offset_x_px), y: Number(j.aim_offset_y_px) }
    mesaj.value = `Kaydedildi (${not}): nişan ofseti X ${mevcut.value.x} px, Y ${mevcut.value.y} px${j.persisted ? ' · diske yazıldı' : ' · UYARI: diske yazılamadı, yeniden başlatınca kaybolur'}. Hemen geçerli.`
    hata.value = !j.persisted
    dikeyCm.value = 0
    yatayCm.value = 0
  } catch (e) {
    hata.value = true
    mesaj.value = 'Kaydedilemedi: ' + (e instanceof Error ? e.message : '')
  } finally {
    busy.value = false
  }
}

function uygula(): void {
  if (!yeni.value) return
  void kaydet(yeni.value.x, yeni.value.y, `${mesafe.value} m, ${dikeyCm.value || 0} cm ${dikeyYon.value}, ${yatayCm.value || 0} cm ${yatayYon.value}`)
}
function varsayilan(): void { void kaydet(VARSAYILAN.x, VARSAYILAN.y, 'varsayılan') }
function geriAl(): void {
  const g = gecmis.value[0]
  if (!g) return
  gecmis.value.shift()
  gecmisYaz()
  void kaydet(g.x, g.y, 'geri alındı', false)
}
</script>

<template>
  <div v-if="open" class="nk-arka" @click.self="emit('close')">
    <section class="nk" role="dialog" aria-label="Nişan kalibrasyonu">
      <header>
        <div><p>NİŞAN KALİBRASYONU</p><h3>Saçma nereye düştü? Yaz, nişangah oraya kaysın</h3></div>
        <button class="kapat" type="button" title="Kapat" @click="emit('close')">✕</button>
      </header>

      <div class="durum">
        <div><small>ŞU ANKİ X (yatay)</small><b>{{ mevcut ? `${mevcut.x} px` : '—' }}</b></div>
        <div><small>ŞU ANKİ Y (dikey)</small><b>{{ mevcut ? `${mevcut.y} px` : '—' }}</b></div>
        <div><small>ÖLÇEK @ {{ mesafe }} m</small><b>{{ pxPerCm ? `${yuvarla(pxPerCm)} px/cm` : '—' }}</b></div>
      </div>

      <div class="formul">
        <label>Mesafe<input v-model.number="mesafe" type="number" step="0.5" min="2" max="30"><em>m</em></label>
      </div>
      <div class="satir">
        <span class="etiket">Dikey</span>
        <input v-model.number="dikeyCm" type="number" step="0.5" min="0" max="60"><em>cm</em>
        <div class="yon">
          <button type="button" :class="{ sec: dikeyYon === 'yukari' }" @click="dikeyYon = 'yukari'">▲ Yukarı</button>
          <button type="button" :class="{ sec: dikeyYon === 'asagi' }" @click="dikeyYon = 'asagi'">▼ Aşağı</button>
        </div>
        <b class="px">{{ dikeyPx >= 0 ? '+' : '' }}{{ dikeyPx }} px</b>
      </div>
      <div class="satir">
        <span class="etiket">Yatay</span>
        <input v-model.number="yatayCm" type="number" step="0.5" min="0" max="60"><em>cm</em>
        <div class="yon">
          <button type="button" :class="{ sec: yatayYon === 'sola' }" @click="yatayYon = 'sola'">◀ Sola</button>
          <button type="button" :class="{ sec: yatayYon === 'saga' }" @click="yatayYon = 'saga'">Sağa ▶</button>
        </div>
        <b class="px">{{ yatayPx >= 0 ? '+' : '' }}{{ yatayPx }} px</b>
      </div>

      <div class="sonuc">Yeni ofset: <b>X {{ yeni?.x ?? '—' }} px</b> · <b>Y {{ yeni?.y ?? '—' }} px</b></div>

      <ul v-if="dogrulama.hatalar.length" class="liste hata"><li v-for="m in dogrulama.hatalar" :key="m">{{ m }}</li></ul>
      <ul v-if="dogrulama.uyarilar.length" class="liste uyari"><li v-for="m in dogrulama.uyarilar" :key="m">{{ m }}</li></ul>

      <div class="eylem">
        <button class="ana" type="button" :disabled="busy || !yeni || dogrulama.hatalar.length > 0 || (dikeyPx === 0 && yatayPx === 0)" @click="uygula">{{ busy ? 'İşlem sürüyor…' : 'Kaydet' }}</button>
        <button type="button" :disabled="busy || !gecmis.length" @click="geriAl">↶ Geri al</button>
        <button class="soluk" type="button" :disabled="busy" @click="varsayilan">Varsayılan (X 0 · Y −22)</button>
      </div>
      <div v-if="mesaj" class="mesaj" :class="{ kotu: hata }">{{ mesaj }}</div>

      <div v-if="gecmis.length" class="gecmis"><small>Önceki değerler:</small> <span v-for="(g, i) in gecmis.slice(0, 5)" :key="i">{{ g.zaman }} → X {{ g.x }} / Y {{ g.y }}</span></div>

      <p class="not">Nasıl: taret balona/hedefe kilitliyken kağıda 5–8 atış yapın. Grubun <b>ortasının</b>, nişan alınan noktadan kaç cm ve hangi yöne gittiğini <b>taretin arkasından bakarak</b> yazın (ör. 4 cm aşağı, 2 cm sağa). Kaydet’e basınca nişangah o kadar kayar ve hemen geçerli olur; sürüm geçişinde de taşınır. Sonra 3–5 atışla kontrol edin. Ölçek kameramıza göre ~44 px/derece: 13 cm balon 10 m’de ~33 px, 15 m’de ~22 px.</p>
    </section>
  </div>
</template>

<style scoped>
.nk-arka{position:fixed;inset:0;z-index:1200;display:grid;place-items:center;padding:16px;background:rgba(2,8,16,.72)}
.nk{display:grid;gap:11px;width:min(620px,100%);max-height:calc(100vh - 32px);overflow:auto;padding:18px;border:1px solid #39cbe655;border-radius:16px;background:linear-gradient(145deg,#081a28,#06131f);color:#eaf8ff;box-shadow:0 24px 60px rgba(0,0,0,.55)}
.nk header{display:flex;justify-content:space-between;align-items:flex-start;gap:12px}
.nk header p{margin:0;color:#54ddeb;font-size:.64rem;font-weight:900;letter-spacing:.16em}
.nk header h3{margin:4px 0 0;font-size:1.02rem}
.kapat{border:1px solid #ffffff22;border-radius:8px;background:#0b1d2b;color:#cfe;padding:6px 10px;cursor:pointer}
.durum{display:grid;grid-template-columns:repeat(3,1fr);gap:7px}
.durum>div{display:grid;gap:3px;padding:9px;border:1px solid #ffffff12;border-radius:9px;background:#03101a}
.durum small{color:#7f9aaa;font-size:.58rem;font-weight:800}
.durum b{font:800 .8rem ui-monospace,monospace}
.formul{display:flex;gap:8px}
.formul label{display:flex;align-items:center;gap:6px;color:#a9bfcc;font-size:.72rem;font-weight:800}
input{width:84px;box-sizing:border-box;border:1px solid #55dbea45;border-radius:7px;background:#020a12;color:#ecfbff;padding:8px;font:700 .95rem ui-monospace,monospace}
em{font-style:normal;color:#7f9aaa;font-size:.72rem}
.satir{display:flex;flex-wrap:wrap;align-items:center;gap:8px;padding:8px 10px;border-radius:9px;background:#03101a}
.etiket{width:46px;color:#a9bfcc;font-size:.72rem;font-weight:800}
.yon{display:flex;gap:4px}
.yon button{border:1px solid #49dbea40;border-radius:7px;background:#07141f;color:#a9bfcc;padding:7px 10px;font-size:.72rem;font-weight:800;cursor:pointer}
.yon button.sec{background:#0e5a52;border-color:#35d097;color:#fff}
.px{margin-left:auto;font:800 .82rem ui-monospace,monospace;color:#9ff0d0}
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
.gecmis{display:flex;flex-wrap:wrap;gap:8px;color:#7f9aaa;font-size:.64rem}
.gecmis span{font-family:ui-monospace,monospace}
.not{margin:0;color:#7f9aaa;font-size:.64rem;line-height:1.45}
@media(max-width:600px){.durum{grid-template-columns:1fr 1fr}}
</style>
