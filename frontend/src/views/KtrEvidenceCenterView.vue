<script setup lang="ts">
import { computed, onMounted } from 'vue'
import DashboardCard from '../components/dashboard/DashboardCard.vue'
import MetricRow from '../components/dashboard/MetricRow.vue'
import StatusBadge from '../components/shared/StatusBadge.vue'
import { useDeviceRuntimeStore } from '../stores/deviceRuntimeStore'
import { useHardwareStore } from '../stores/hardwareStore'
import { useInterfacesStore } from '../stores/interfacesStore'
import { useMissionStore } from '../stores/missionStore'
import { useReportsStore } from '../stores/reportsStore'
import { useSerialStore } from '../stores/serialStore'
import { useSystemStore } from '../stores/systemStore'
import { useVisionStore } from '../stores/visionStore'

type Tone = 'good' | 'warn' | 'bad' | 'neutral'

const reports = useReportsStore()
const interfaces = useInterfacesStore()
const system = useSystemStore()
const vision = useVisionStore()
const hardware = useHardwareStore()
const serial = useSerialStore()
const runtime = useDeviceRuntimeStore()
const mission = useMissionStore()

const performance = computed(() => system.latestEvents.find((event) => event.type === 'performance.status')?.payload as any | undefined)
const missionState = computed(() => mission.snapshot.state)
const missionScore = computed(() => mission.snapshot.score)

const ktrScoreRows = computed(() => [
  { title: '4.2 Yazılım Tasarım Süreci', points: '20 puan içinde', status: runtime.visionStatus.effective_adapter && serial.status.last_command_raw ? 'kanıt var' : 'eksik', tone: runtime.visionStatus.effective_adapter && serial.status.last_command_raw ? 'good' : 'warn', evidence: 'YOLO runtime, tracking, atış kontrol, komut debug' },
  { title: '4.3 Arayüzler', points: '10 puan', status: interfaces.inventory.interfaces.length ? 'hazır' : 'eksik', tone: interfaces.inventory.interfaces.length ? 'good' : 'warn', evidence: `${interfaces.inventory.interfaces.length} interface kaydı` },
  { title: '4.4 Sistem İşleyiş Senaryoları', points: '10 puan', status: system.latestEvents.length ? 'canlı event var' : 'eksik', tone: system.latestEvents.length ? 'good' : 'warn', evidence: 'Canlı sistem haritası, command lifecycle, hata senaryoları' },
  { title: '5 Test', points: '10 puan', status: performance.value ? 'ölçüm var' : 'eksik', tone: performance.value ? 'good' : 'warn', evidence: 'FPS, ms gecikme, CPU/GPU/RAM, ACK/queue' },
  { title: '6 Güvenlik', points: '5 puan', status: hardware.status.telemetry.estop_state !== null || serial.status.magazine_capacity ? 'görünür' : 'eksik', tone: 'good', evidence: 'E-stop, yasak bölge kapısı, fire gate, şarjör kilidi' },
  { title: 'Görev Oturumu', points: 'KTR/demo kanıtı', status: missionState.value.updated_at ? 'kayıtlı' : 'beklemede', tone: missionState.value.updated_at ? 'good' : 'warn', evidence: `Aktif aşama ${missionState.value.active_stage}, tahmini toplam ${missionScore.value.total_estimated_score} puan` },
  { title: '9 Özgünlük', points: '5 puan', status: 'güçlü', tone: 'good', evidence: 'Canlı akış haritası, bottleneck teşhisi, KTR kanıt exportu' },
])

const videoChecklist = computed(() => [
  { item: 'Yetenek 1 - Kullanıcı arayüzü tüm fonksiyonlar', ok: true, evidence: 'Yarışma Konsolu + Sistem Haritası + KTR Kanıt Merkezi' },
  { item: 'Yetenek 2 - 15 m durağan balon imha', ok: serial.status.magazine_capacity > 0, evidence: 'Manuel mod, şarjör, fire debug, video overlay' },
  { item: 'Yetenek 3 - X/Y hareket ederken E-stop', ok: hardware.status.telemetry.estop_state !== null || hardware.status.pico_verified, evidence: 'Pico telemetry, motion state, system map' },
  { item: 'Yetenek 4 - Ateş ederken E-stop ateşi keser', ok: true, evidence: 'Fire gate + serial command log + servo/lazer hattı' },
  { item: 'Yetenek 5 - Yan/yükseliş hareketli hedef takip', ok: vision.visionStatus.balloon_count > 0 || vision.visionStatus.running, evidence: 'Tracking state, PID, crosshair overlay' },
  { item: 'Yetenek 6 - 5/10/15 m sınıflandırma opsiyonel', ok: runtime.visionStatus.production_yolo_loaded, evidence: 'Model package, class mapping, target type/range gates' },
])

const scenarioRows = [
  { name: 'Kamera yok', response: 'Camera node kırmızı, frame akışı kesilir, fire gate kapalı kalır.' },
  { name: 'YOLO yavaş', response: 'Inference ms sarı/kırmızı, total pipeline artar, bottleneck panelinde görünür.' },
  { name: 'Pico kopuk', response: 'Serial/Pico edge kırmızı, komutlar timeout olur, motion/fire güvenlik durur.' },
  { name: 'Komut sıkışması', response: 'TX queue ve pending ACK kırmızı, son komut yaşı ve RTT görünür.' },
  { name: 'Şarjör bitti', response: 'Magazine empty, LZR fire bloklanır, operatör resetlemeden ateş yok.' },
  { name: 'Dost hedef', response: 'Aşama 3 fire gate dost hedefte kapalı kalacak şekilde tasarlanır.' },
]

const latestFiles = computed(() => reports.latestExport?.files ?? [])

function tone(value: string): Tone {
  if (value === 'good' || value === 'warn' || value === 'bad') return value
  return 'neutral'
}

onMounted(() => {
  void reports.refresh()
  void interfaces.refresh()
  void runtime.refresh()
  void mission.refresh()
})
</script>

<template>
  <div class="grid gap-4">
    <div class="grid gap-4 xl:grid-cols-3">
      <DashboardCard title="KTR Puan Haritası" subtitle="Arayüzün rapordaki doğrudan kanıtları">
        <div class="grid gap-2">
          <div v-for="row in ktrScoreRows" :key="row.title" class="rounded-md border border-white/8 bg-black/18 p-3">
            <div class="mb-2 flex items-center justify-between gap-3">
              <span class="text-sm font-semibold text-slate-100">{{ row.title }}</span>
              <StatusBadge :label="row.status" :tone="tone(row.tone)" />
            </div>
            <p class="text-xs text-slate-400">{{ row.points }} · {{ row.evidence }}</p>
          </div>
        </div>
      </DashboardCard>

      <DashboardCard title="Export Merkezi" subtitle="KTR, demo ve readiness paketleri">
        <div class="grid gap-2">
          <button class="focus-ring rounded-md bg-cyan-500 px-3 py-2 text-sm font-semibold text-slate-950 disabled:opacity-60" :disabled="reports.isGenerating" @click="reports.generate('ktr')">KTR summary üret</button>
          <button class="focus-ring rounded-md bg-slate-700 px-3 py-2 text-sm font-semibold text-white disabled:opacity-60" :disabled="reports.isGenerating" @click="reports.generate('demo')">Demo pack üret</button>
          <button class="focus-ring rounded-md bg-slate-700 px-3 py-2 text-sm font-semibold text-white disabled:opacity-60" :disabled="reports.isGenerating" @click="reports.generate('readiness')">Readiness pack üret</button>
          <button class="focus-ring rounded-md border border-cyan-400/40 bg-cyan-400/12 px-3 py-2 text-sm font-semibold text-cyan-100 disabled:opacity-60" :disabled="interfaces.isExporting" @click="interfaces.exportInventory">Interface inventory export</button>
        </div>
        <MetricRow label="Latest export" :value="reports.latestExport?.export_id ?? 'none'" />
        <MetricRow label="Output dir" :value="reports.latestExport?.output_dir ?? 'none'" />
        <MetricRow label="Interface count" :value="interfaces.inventory.interfaces.length" />
        <p v-if="reports.error || interfaces.error" class="mt-3 rounded-md border border-red-400/30 bg-red-500/10 px-3 py-2 text-sm text-red-100">{{ reports.error ?? interfaces.error }}</p>
      </DashboardCard>

      <DashboardCard title="Canlı Test Özeti" subtitle="Rapor ve final sunumu için ölçümler">
        <MetricRow label="Camera FPS" :value="performance?.camera_fps ?? vision.visionStatus.fps" />
        <MetricRow label="Inference ms" :value="performance?.inference_ms ?? vision.visionStatus.latest_latency_ms" />
        <MetricRow label="Total pipeline ms" :value="performance?.total_pipeline_ms ?? 'n/a'" />
        <MetricRow label="CPU" :value="performance?.cpu_percent == null ? 'n/a' : `${performance.cpu_percent}%`" />
        <MetricRow label="GPU" :value="performance?.gpu_util_percent == null ? 'n/a' : `${performance.gpu_util_percent}%`" />
        <MetricRow label="Serial queue" :value="serial.status.command_queue_depth" />
        <MetricRow label="Magazine" :value="`${serial.status.magazine_remaining}/${serial.status.magazine_capacity}`" />
        <MetricRow label="Mission stage" :value="missionState.active_stage" />
        <MetricRow label="Mission score" :value="missionScore.total_estimated_score" />
        <MetricRow label="Mission evidence" value="mission_evidence.md/json" />
      </DashboardCard>
    </div>

    <div class="grid gap-4 xl:grid-cols-2">
      <DashboardCard title="Görev Kabiliyeti Video Checklist" subtitle="Şartnamedeki Yetenek 1-6 sırasına göre">
        <div class="grid gap-2">
          <div v-for="row in videoChecklist" :key="row.item" class="rounded-md border border-white/8 bg-black/18 p-3">
            <div class="mb-1 flex items-center justify-between gap-3">
              <span class="text-sm font-semibold text-slate-100">{{ row.item }}</span>
              <StatusBadge :label="row.ok ? 'GÖSTERİLEBİLİR' : 'EKSİK'" :tone="row.ok ? 'good' : 'warn'" />
            </div>
            <p class="text-xs text-slate-400">{{ row.evidence }}</p>
          </div>
        </div>
      </DashboardCard>

      <DashboardCard title="Hata Senaryoları" subtitle="Rainy-day senaryolar ve sistem tepkisi">
        <div class="grid gap-2">
          <div v-for="row in scenarioRows" :key="row.name" class="rounded-md border border-white/8 bg-black/18 p-3">
            <p class="text-sm font-semibold text-white">{{ row.name }}</p>
            <p class="mt-1 text-xs text-slate-400">{{ row.response }}</p>
          </div>
        </div>
      </DashboardCard>
    </div>

    <DashboardCard title="Son Export Dosyaları" subtitle="Hakem/KTR teslim dosyaları">
      <div class="grid gap-2 md:grid-cols-2">
        <div v-for="file in latestFiles" :key="file" class="rounded-md border border-white/8 bg-black/18 px-3 py-2 font-mono text-xs text-cyan-100">{{ file }}</div>
        <p v-if="latestFiles.length === 0" class="text-sm text-slate-400">Henüz export yok.</p>
      </div>
    </DashboardCard>
  </div>
</template>
