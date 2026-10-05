<script setup lang="ts">
// Kokpit sol alt: yaris surumu secimi (V1..V4, BASE - dogrudan) + "test kaydi" (ayni parkur hareketi her
// surumde kaydedilir, ozet tabloda karsilastirilir). Uclar yoksa (eski surum) hic gorunmez.
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

const SURUMLER = ['E21', 'V5']
const ACIKLAMA: Record<string, string> = {
  E21: '21 Eylül takip · dar kapı (ÖNERİLEN, 1 Ekim testlerinde en iyi)',
  V1: 'Güncel takip · dar kapı · düzeltmeler',
  V2: 'V1 + hız sınırı ~25°/s',
  V3: 'Güncel takip · geniş kapı · düzeltmeler',
  V4: 'V1 + hafif ileri besleme 0,25',
  V5: 'Hız sınırı ~25°/s + ileri besleme 0,25',
  V6: 'V5 + geniş kapı (5–10 m için)',
  BASE: 'GÜNCEL, düzeltmesiz',
}

interface TestKaydi {
  surum?: string; etiket?: string; saat?: string; sure_s?: number; taze_kare?: number
  yatay_hata_medyan_px?: number | null; ic_daire_yuzde?: number | null; balon_ici_yuzde?: number | null
  kilit_yuzde?: number; arama_yuzde?: number; atis_adayi?: number; atis_komutu?: number
}

const surum = ref<string | null>(null)
const onayHedef = ref<string | null>(null)
const durum = ref<'bos' | 'gecis' | 'hata' | 'tamam'>('bos')
const mesaj = ref('')
const testSuruyor = ref(false)
const testBas = ref<number | null>(null)
const simdi = ref(Date.now())
const kayitlar = ref<TestKaydi[]>([])
const tabloAcik = ref(false)
const sonOzet = ref<TestKaydi | null>(null)
let timer: ReturnType<typeof setInterval> | null = null
let saatTimer: ReturnType<typeof setInterval> | null = null
let onayTimer: ReturnType<typeof setTimeout> | null = null
const y15 = ref<boolean | null>(null)
const y15Busy = ref(false)

function apiBase(): string {
  const configured = import.meta.env.VITE_BACKEND_API_URL as string | undefined
  if (configured) return configured.replace(/\/$/, '')
  if (window.location.port && window.location.port !== '5173') return window.location.origin
  return `${window.location.protocol}//${window.location.hostname}:8000`
}

async function oku(): Promise<{ surum: string | null } | null> {
  try {
    const r = await fetch(`${apiBase()}/api/hardware/yaris-surum`, { cache: 'no-store' })
    if (!r.ok) return null
    return await r.json()
  } catch { return null }
}

async function y15Oku(): Promise<void> {
  try {
    const r = await fetch(`${apiBase()}/api/hardware/y15`, { cache: 'no-store' })
    if (!r.ok) { y15.value = null; return }
    const j = await r.json()
    y15.value = typeof j.acik === 'boolean' ? j.acik : null
  } catch { y15.value = null }
}

async function y15Degistir(): Promise<void> {
  if (y15.value === null || y15Busy.value) return
  y15Busy.value = true
  try {
    const r = await fetch(`${apiBase()}/api/hardware/y15?acik=${y15.value ? 0 : 1}`, { method: 'POST' })
    if (r.ok) { const j = await r.json(); y15.value = !!j.acik; mesaj.value = `İz koruma (Y15) ${y15.value ? 'AÇIK' : 'KAPALI'} — hemen geçerli.` }
    else mesaj.value = 'Y15 değiştirilemedi'
  } catch { mesaj.value = 'Sunucuya ulaşılamadı' } finally { y15Busy.value = false }
}

async function listeOku(): Promise<void> {
  try {
    const r = await fetch(`${apiBase()}/api/hardware/yaris-test/liste`, { cache: 'no-store' })
    if (!r.ok) return
    const j = await r.json()
    kayitlar.value = (j.kayit ?? []).slice().reverse()
    testSuruyor.value = !!j.suren
    testBas.value = j.bas ? Number(j.bas) * 1000 : null
  } catch { /* uc yoksa tablo bos */ }
}

onMounted(async () => {
  const j = await oku()
  if (j) surum.value = j.surum
  await listeOku()
  await y15Oku()
  saatTimer = setInterval(() => { simdi.value = Date.now() }, 500)
  try {
    const onceki = sessionStorage.getItem('istiklal_yaris_gecis')
    if (onceki && j?.surum && onceki !== j.surum) {
      durum.value = 'tamam'
      mesaj.value = `${j.surum} açıldı. Şimdi 🏠 Auto-Home yapın.`
      setTimeout(() => { if (durum.value === 'tamam') { durum.value = 'bos'; mesaj.value = '' } }, 20000)
    }
    sessionStorage.removeItem('istiklal_yaris_gecis')
  } catch { /* yok say */ }
})

onBeforeUnmount(() => {
  if (timer) clearInterval(timer)
  if (saatTimer) clearInterval(saatTimer)
  if (onayTimer) clearTimeout(onayTimer)
})

function sec(h: string): void {
  if (h === surum.value || durum.value === 'gecis' || testSuruyor.value) return
  onayHedef.value = h
  if (onayTimer) clearTimeout(onayTimer)
  onayTimer = setTimeout(() => { onayHedef.value = null }, 8000)
}

async function gec(): Promise<void> {
  const hedef = onayHedef.value
  onayHedef.value = null
  if (!hedef) return
  const eski = surum.value
  durum.value = 'gecis'
  mesaj.value = `${hedef} açılıyor…`
  try {
    const r = await fetch(`${apiBase()}/api/hardware/yaris-gecis-hedef?hedef=${encodeURIComponent(hedef)}`, { method: 'POST' })
    if (!r.ok) { durum.value = 'hata'; mesaj.value = 'Geçiş başlatılamadı: ' + (await r.text()).slice(0, 160); return }
  } catch {
    durum.value = 'hata'
    mesaj.value = 'Geçiş başlatılamadı (sunucuya ulaşılamadı). Masaüstündeki YARIS kısayolunu kullanın.'
    return
  }
  try { sessionStorage.setItem('istiklal_yaris_gecis', eski ?? '') } catch { /* yok say */ }
  const t0 = Date.now()
  timer = setInterval(async () => {
    const sn = Math.round((Date.now() - t0) / 1000)
    const j = await oku()
    if (j && j.surum === hedef) { if (timer) clearInterval(timer); window.location.reload(); return }
    mesaj.value = `${hedef} açılıyor… ${sn} s`
    if (sn > 150) {
      if (timer) clearInterval(timer)
      durum.value = 'hata'
      mesaj.value = `${hedef} 2,5 dk içinde açılmadı. Masaüstünde YARIS_${hedef}'e çift tıklayın.`
    }
  }, 2000)
}

async function testDugme(): Promise<void> {
  try {
    if (!testSuruyor.value) {
      const r = await fetch(`${apiBase()}/api/hardware/yaris-test/basla`, { method: 'POST' })
      if (!r.ok) { mesaj.value = 'Test başlatılamadı'; return }
      testSuruyor.value = true
      testBas.value = Date.now()
      sonOzet.value = null
      mesaj.value = ''
    } else {
      mesaj.value = 'Test özeti hesaplanıyor…'
      const r = await fetch(`${apiBase()}/api/hardware/yaris-test/bitir`, { method: 'POST' })
      testSuruyor.value = false
      testBas.value = null
      if (!r.ok) { mesaj.value = 'Test bitirilemedi: ' + (await r.text()).slice(0, 120); return }
      sonOzet.value = await r.json()
      mesaj.value = ''
      await listeOku()
    }
  } catch { mesaj.value = 'Sunucuya ulaşılamadı' }
}

function tabloToggle(): void {
  tabloAcik.value = !tabloAcik.value
  if (tabloAcik.value) void listeOku()
}

const testSure = computed(() => testBas.value ? Math.max(0, Math.round((simdi.value - testBas.value) / 1000)) : 0)
const yuzde = (v: number | null | undefined) => (v === null || v === undefined ? '—' : `%${v}`)
</script>

<template>
  <div v-if="surum" class="yg">
    <div class="satir">
      <span class="rozet" title="Çalışan yarış sürümü">SÜRÜM <b>{{ surum }}</b></span>
      <div class="secim">
        <button v-for="h in SURUMLER" :key="h" type="button" :class="{ aktif: h === surum }" :disabled="durum === 'gecis' || testSuruyor" :title="ACIKLAMA[h]" @click="sec(h)">{{ h }}</button>
      </div>
      <button type="button" class="test" :class="{ kayit: testSuruyor }" :disabled="durum === 'gecis'" :title="testSuruyor ? 'Testi bitir ve özetini çıkar' : 'Aynı parkur hareketini bu sürümde kaydet'" @click="testDugme">
        {{ testSuruyor ? `⏹ Testi bitir (${testSure} s)` : '⏺ Test kaydı' }}
      </button>
      <button v-if="y15 !== null" type="button" class="y15" :class="{ acik: y15 }" :disabled="y15Busy" title="Y15 İz koruma: hızlı dönüşte iz koparsa eski noktaya kör gitmek yerine yakındaki balonu hemen alır, yoksa yumuşak frenler. Varsayılan AÇIK." @click="y15Degistir">🛡 İz koruma: {{ y15 ? 'AÇIK' : 'KAPALI' }}</button>
      <button type="button" class="tablo-btn" @click="tabloToggle">📊 {{ tabloAcik ? 'Gizle' : 'Sonuçlar' }}</button>
    </div>
    <div v-if="onayHedef" class="satir onay">
      <span>{{ onayHedef }}'ye geçilsin mi? ({{ ACIKLAMA[onayHedef] }}) Sistem ~30 s kapanır.</span>
      <button type="button" class="evet" @click="gec">Evet, geç</button>
      <button type="button" class="hayir" @click="onayHedef = null">Vazgeç</button>
    </div>
    <div v-if="mesaj" class="mesaj" :class="durum">{{ mesaj }}</div>
    <div v-if="sonOzet" class="ozet">
      Son test ({{ sonOzet.surum }}, {{ sonOzet.sure_s }} s): iç daire <b>{{ yuzde(sonOzet.ic_daire_yuzde) }}</b> · balon içi {{ yuzde(sonOzet.balon_ici_yuzde) }} · kilit {{ yuzde(sonOzet.kilit_yuzde) }} · yatay hata {{ sonOzet.yatay_hata_medyan_px ?? '—' }} px · atış adayı {{ sonOzet.atis_adayi }}
    </div>
    <div v-if="tabloAcik" class="tablo">
      <table>
        <thead><tr><th>Saat</th><th>Sürüm</th><th>Süre</th><th title="Nişangahın yatayda iç atış dairesinde olduğu karelerin oranı">İç daire</th><th title="Nişangahın yatayda balonun içinde olduğu karelerin oranı">Balon içi</th><th>Kilit</th><th>Arama</th><th title="Medyan yatay hata">Hata px</th><th>Atış adayı</th></tr></thead>
        <tbody>
          <tr v-for="(k, i) in kayitlar" :key="i">
            <td>{{ k.saat }}</td><td><b>{{ k.surum }}</b></td><td>{{ k.sure_s }} s</td><td><b>{{ yuzde(k.ic_daire_yuzde) }}</b></td><td>{{ yuzde(k.balon_ici_yuzde) }}</td><td>{{ yuzde(k.kilit_yuzde) }}</td><td>{{ yuzde(k.arama_yuzde) }}</td><td>{{ k.yatay_hata_medyan_px ?? '—' }}</td><td>{{ k.atis_adayi }}</td>
          </tr>
          <tr v-if="!kayitlar.length"><td colspan="9">Henüz test kaydı yok.</td></tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<style scoped>
.yg{position:fixed;left:10px;bottom:10px;z-index:1100;display:grid;gap:6px;max-width:calc(100vw - 20px);padding:7px 9px;border:1px solid rgba(103,232,249,.28);border-radius:10px;background:rgba(3,12,22,.94);color:#cfefff;font-size:.7rem;box-shadow:0 8px 22px rgba(0,0,0,.4)}
.satir{display:flex;flex-wrap:wrap;align-items:center;gap:6px}
.rozet{padding:3px 7px;border-radius:6px;background:#0b2a38;color:#8fdcef;font-weight:800;letter-spacing:.06em}
.rozet b{color:#fff;margin-left:3px}
.secim{display:flex;gap:3px}
.yg button{border:1px solid rgba(103,232,249,.35);border-radius:7px;background:#0b3343;color:#dffbff;padding:5px 9px;font-size:.7rem;font-weight:800;cursor:pointer}
.yg button:hover:not(:disabled){filter:brightness(1.2)}
.yg button:disabled{opacity:.45;cursor:not-allowed}
.secim button.aktif{background:#0e5a52;border-color:#35d097;color:#fff;cursor:default}
.yg .test{background:#3a1d24;border-color:#f16d7888}
.yg .test.kayit{background:#8a1d2a;border-color:#ff9aa5;animation:nabiz 1s infinite}
@keyframes nabiz{50%{filter:brightness(1.35)}}
.yg .evet{background:#6b1d24;border-color:#f16d78;color:#ffe0e3}
.yg .y15{background:#3a2d10;border-color:#f5c04e88;color:#ffe3a3}
.yg .y15.acik{background:#0e4a3a;border-color:#35d097;color:#d9fff0}
.yg .hayir,.yg .tablo-btn{background:#07141f;color:#a9bfcc}
.onay span{color:#ffd47e;font-weight:700}
.mesaj{color:#bdebd7}
.mesaj.gecis{color:#ffd47e}
.mesaj.hata{color:#ffafb7}
.ozet{color:#e6f7ff}
.tablo{max-height:260px;overflow:auto}
.tablo table{border-collapse:collapse;font-size:.66rem}
.tablo th,.tablo td{padding:3px 7px;border-bottom:1px solid #ffffff14;text-align:right;white-space:nowrap}
.tablo th{color:#8fdcef;position:sticky;top:0;background:#03101a}
.tablo td:nth-child(-n+2),.tablo th:nth-child(-n+2){text-align:left}
</style>
