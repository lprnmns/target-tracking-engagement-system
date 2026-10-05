<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { RouterLink, RouterView, useRoute } from 'vue-router'
import { useRuntimeTruth } from '../../composables/useRuntimeTruth'
import { useDeviceRuntimeStore } from '../../stores/deviceRuntimeStore'
import { useFirstRunStore } from '../../stores/firstRunStore'
import { useHardwareStore } from '../../stores/hardwareStore'
import { useInterfacesStore } from '../../stores/interfacesStore'
import { useMotionStore } from '../../stores/motionStore'
import { useSelfTestStore } from '../../stores/selfTestStore'
import { useSerialStore } from '../../stores/serialStore'
import { useSystemStore } from '../../stores/systemStore'
import { useVisionStore } from '../../stores/visionStore'
import StatusBadge from '../shared/StatusBadge.vue'

const route = useRoute()
const system = useSystemStore()
const serial = useSerialStore()
const vision = useVisionStore()
const motion = useMotionStore()
const selfTest = useSelfTestStore()
const firstRun = useFirstRunStore()
const interfaces = useInterfacesStore()
const hardware = useHardwareStore()
const runtime = useDeviceRuntimeStore()
const truth = useRuntimeTruth()
const lastRefreshAt = ref('başlatılıyor')
let refreshTimer: ReturnType<typeof setInterval> | null = null

const primaryNavItems = [
  { label: 'Kokpit', to: '/cockpit' },
  { label: 'Kurulum', to: '/setup' },
  { label: 'Kanıt', to: '/evidence' },
]

const routeTitles: Record<string, string> = {
  '/': 'Başlangıç Ekrani',
  '/cockpit': 'Kokpit',
  '/cockpit/model-calibration': 'Model Kalibrasyon',
  '/setup': 'Kurulum',
  '/debug': 'Debug',
  '/evidence': 'Kanıt',
  '/legacy-console': 'Eski Konsol',
  '/vision': 'Vision',
  '/motion': 'Motion',
  '/devices': 'Devices',
  '/pico': 'Pico',
  '/serial': 'Serial',
  '/dashboard': 'Dashboard',
  '/system-map': 'Sistem Haritası',
  '/mission-modes': 'Görev Modları',
  '/safety': 'Safety',
  '/models': 'Models',
  '/calibration': 'Calibration',
  '/color': 'Color',
  '/data-lab': 'Data Lab',
  '/interfaces': 'Interfaces',
  '/self-test': 'Self-Test',
  '/first-run': 'First Run',
  '/demo': 'Demo',
  '/reports': 'Reports',
  '/logs': 'Logs',
  '/ktr-evidence': 'KTR Merkezi',
}
const title = computed(() => routeTitles[route.path] ?? 'Kokpit')
const isFullscreenRoute = computed(() => route.name === 'cockpit' || route.name === 'cockpit-world' || route.name === 'landing' || route.name === 'setup-center')
const buildHash = ((import.meta.env.VITE_GIT_HASH as string | undefined) ?? 'DEV-LOCAL').toUpperCase()
const buildLabel = `ISTIKLAL · ${buildHash}`
const missionMode = computed(() => system.systemState.mode)
const firePolicyTone = computed(() => system.systemState.fire_policy === 'NO_FIRE' ? 'good' : 'bad')
const hardwareTone = computed(() => system.systemState.hardware_enabled ? 'warn' : 'good')

async function refreshLiveState(): Promise<void> {
  const tasks: Promise<unknown>[] = [selfTest.refresh(), firstRun.refresh(), interfaces.refresh()]
  // The WebSocket is the live source for camera/vision, motion, serial,
  // hardware and runtime status. Keep the REST fan-out as a reconnect
  // fallback; polling the same 1080p/YOLO truth while connected only adds
  // event-loop and browser work.
  if (system.connectionStatus !== 'connected') {
    tasks.push(
      vision.refresh(),
      motion.refresh(),
      motion.refreshTrackingStatus(),
      serial.refresh(),
      hardware.refresh(),
      runtime.refresh(),
    )
  }
  await Promise.all(tasks)
  lastRefreshAt.value = new Date().toLocaleTimeString()
}

onMounted(() => {
  system.connect()
  void refreshLiveState()
  refreshTimer = setInterval(() => {
    void refreshLiveState()
  }, 5000)
})

onUnmounted(() => {
  if (refreshTimer) clearInterval(refreshTimer)
})
</script>

<template>
  <RouterView v-if="isFullscreenRoute" />
  <div v-else class="min-h-screen bg-[#080b0f] text-slate-100">
    <div class="flex min-h-screen">
      <aside class="hidden w-[232px] shrink-0 border-r border-white/10 bg-[#0d1218] px-3 py-4 lg:block">
        <div class="mb-5 px-2">
          <p class="text-[11px] font-semibold uppercase tracking-[0.28em] text-cyan-300">ISTIKLAL</p>
          <h1 class="mt-2 text-lg font-semibold text-white">Yarışma Kokpiti</h1>
          <p class="mt-1 text-xs text-slate-500">{{ buildLabel }}</p>
        </div>

        <nav class="grid gap-5">
          <div class="grid gap-1">
            <p class="px-2 text-[10px] font-semibold uppercase tracking-[0.18em] text-slate-500">Operasyon</p>
            <RouterLink
              v-for="item in primaryNavItems"
              :key="item.to"
              :to="item.to"
              class="focus-ring rounded-md px-2.5 py-2.5 text-sm font-semibold text-slate-300 transition hover:bg-white/6 hover:text-white"
              :class="{ 'bg-cyan-400/12 text-cyan-100 ring-1 ring-cyan-400/30': route.path === item.to }"
            >
              {{ item.label }}
            </RouterLink>
          </div>
        </nav>

        <div class="mt-5 rounded-md border border-white/10 bg-black/22 p-3">
          <div class="flex items-start justify-between gap-3">
            <div>
              <p class="text-[10px] font-semibold uppercase tracking-[0.18em] text-slate-500">Canlı Durum</p>
              <p class="mt-2 text-sm font-semibold text-white">{{ missionMode }}</p>
            </div>
            <StatusBadge :label="truth.overallLabel.value" :tone="truth.overallTone.value" />
          </div>
          <div class="mt-3 flex flex-wrap gap-1.5">
            <StatusBadge :label="system.systemState.fire_policy" :tone="firePolicyTone" />
            <StatusBadge :label="system.systemState.hardware_enabled ? 'HW LIVE' : 'HW SAFE'" :tone="hardwareTone" />
          </div>
        </div>

        <p class="mt-4 border-t border-white/10 pt-3 px-2 text-[10px] leading-4 text-slate-600">Gelişmiş ve legacy araçlar yalnız Kokpit içindeki Mühendis Panelinden açılır.</p>
      </aside>

      <div class="flex min-w-0 flex-1 flex-col">
        <header class="border-b border-white/10 bg-[#0e141b]/95 px-4 py-3">
          <div class="flex flex-col gap-3 xl:flex-row xl:items-center xl:justify-between">
            <div>
              <p class="text-[11px] uppercase tracking-[0.24em] text-slate-500">{{ buildLabel }}</p>
              <h2 class="mt-1 text-xl font-semibold text-white">{{ title }}</h2>
            </div>
            <div class="flex flex-wrap items-center gap-2">
              <StatusBadge :label="system.connectionStatus === 'connected' ? 'Backend bağlı' : 'Backend kopuk'" :tone="system.connectionStatus === 'connected' ? 'good' : 'bad'" />
              <StatusBadge :label="truth.overallLabel.value" :tone="truth.overallTone.value" />
            </div>
          </div>
        </header>

        <main class="min-w-0 flex-1 px-3 py-3 md:px-5 md:py-4">
          <div
            v-if="system.isOffline"
            class="mb-3 rounded-md border border-amber-400/30 bg-amber-400/10 px-4 py-3 text-sm text-amber-100"
          >
            Backend bağlantısı yok; bu ekranda frontend mock bağlantı üretmez.
          </div>
          <RouterView />
        </main>
      </div>
    </div>
  </div>
</template>
