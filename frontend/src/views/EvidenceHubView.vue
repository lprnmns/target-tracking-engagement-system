<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { RouterLink } from 'vue-router'
import StatusBadge from '../components/shared/StatusBadge.vue'
import { useRuntimeTruth } from '../composables/useRuntimeTruth'
import { useDataLabStore } from '../stores/dataLabStore'
import { useFirstRunStore } from '../stores/firstRunStore'
import { useMissionStore } from '../stores/missionStore'
import { useReportsStore } from '../stores/reportsStore'
import { useSelfTestStore } from '../stores/selfTestStore'
import { useSerialStore } from '../stores/serialStore'
import { useVisionStore } from '../stores/visionStore'

const truth = useRuntimeTruth()
const selfTest = useSelfTestStore()
const firstRun = useFirstRunStore()
const reports = useReportsStore()
const dataLab = useDataLabStore()
const mission = useMissionStore()
const serial = useSerialStore()
const vision = useVisionStore()

const readinessRows = computed(() => [
  { label: 'Gerçek kamera', ok: truth.realCameraStreamHealthy.value, detail: truth.cameraMessage.value },
  { label: 'Pico telemetry', ok: truth.picoHealthy.value, detail: truth.picoLabel.value },
  { label: 'Şarjör', ok: !serial.status.magazine_empty, detail: truth.magazineLabel.value },
  { label: 'Self-test', ok: selfTest.latestRun?.status === 'passed', detail: selfTest.latestRun?.status ?? 'not run' },
  { label: 'First run', ok: firstRun.displayStatus === 'PASSED', detail: firstRun.displayBadge },
])

const scoreRows = computed(() => [
  { label: 'Aktif puan', value: mission.snapshot.score.active_score },
  { label: 'Tahmini toplam', value: mission.snapshot.score.total_estimated_score },
  { label: 'Aşama 1 hit', value: mission.snapshot.state.stage1_hits },
  { label: 'Aşama 2 hit', value: mission.snapshot.state.stage2_hits },
  { label: 'Aşama 3 hit', value: mission.snapshot.state.stage3_hits },
])

onMounted(() => {
  void Promise.all([
    selfTest.refresh(),
    firstRun.refresh(),
    reports.refresh(),
    dataLab.refresh(),
    mission.refresh(),
    serial.refresh(),
    vision.refreshLegacyEvidence(),
  ])
})
</script>

<template>
  <div class="grid gap-4 xl:grid-cols-[1fr_1fr]">
    <section class="rounded-md border border-white/10 bg-[#10161d] p-4">
      <div class="mb-4">
        <h3 class="text-lg font-semibold text-white">KTR Kanıt Özeti</h3>
        <p class="text-sm text-slate-500">Jüriye gösterilecek readiness, güvenlik ve görev kanıtları.</p>
      </div>
      <div class="grid gap-2">
        <div v-for="row in readinessRows" :key="row.label" class="flex items-center justify-between gap-3 rounded-md border border-white/10 bg-black/20 px-3 py-2">
          <div>
            <p class="text-sm font-semibold text-white">{{ row.label }}</p>
            <p class="text-xs text-slate-500">{{ row.detail }}</p>
          </div>
          <StatusBadge :label="row.ok ? 'hazır' : 'eksik'" :tone="row.ok ? 'good' : 'warn'" />
        </div>
      </div>
    </section>

    <section class="rounded-md border border-white/10 bg-[#10161d] p-4">
      <div class="mb-4 flex items-center justify-between gap-3">
        <div>
          <h3 class="text-lg font-semibold text-white">Görev Skoru</h3>
          <p class="text-sm text-slate-500">Aktif mission state ve şarjör durumu.</p>
        </div>
        <StatusBadge :label="mission.snapshot.state.active_stage" tone="neutral" />
      </div>
      <div class="grid gap-2">
        <div v-for="row in scoreRows" :key="row.label" class="flex justify-between gap-3 border-t border-white/8 py-2 text-sm first:border-t-0">
          <span class="text-slate-400">{{ row.label }}</span>
          <span class="font-mono text-white">{{ row.value }}</span>
        </div>
      </div>
    </section>

    <section class="rounded-md border border-white/10 bg-[#10161d] p-4">
      <h3 class="text-lg font-semibold text-white">Evidence Kısayolları</h3>
      <div class="mt-4 grid gap-2 md:grid-cols-2">
        <RouterLink class="focus-ring rounded-md border border-white/10 bg-black/20 px-3 py-3 text-sm font-semibold text-slate-100 hover:bg-white/6" to="/ktr-evidence">KTR Merkezi</RouterLink>
        <RouterLink class="focus-ring rounded-md border border-white/10 bg-black/20 px-3 py-3 text-sm font-semibold text-slate-100 hover:bg-white/6" to="/reports">Rapor Export</RouterLink>
        <RouterLink class="focus-ring rounded-md border border-white/10 bg-black/20 px-3 py-3 text-sm font-semibold text-slate-100 hover:bg-white/6" to="/self-test">Self-Test</RouterLink>
        <RouterLink class="focus-ring rounded-md border border-white/10 bg-black/20 px-3 py-3 text-sm font-semibold text-slate-100 hover:bg-white/6" to="/data-lab">Data Lab</RouterLink>
      </div>
    </section>

    <section class="rounded-md border border-white/10 bg-[#10161d] p-4">
      <h3 class="text-lg font-semibold text-white">Son Kanıt Durumu</h3>
      <div class="mt-4 grid gap-2 text-sm">
        <div class="flex justify-between gap-3"><span class="text-slate-500">Latest report</span><span class="text-right font-mono">{{ reports.latestExport?.export_id ?? 'none' }}</span></div>
        <div class="flex justify-between gap-3"><span class="text-slate-500">Data session</span><span class="text-right font-mono">{{ dataLab.activeSession?.session_id ?? 'none' }}</span></div>
        <div class="flex justify-between gap-3"><span class="text-slate-500">Real camera evidence</span><span class="text-right font-mono">{{ vision.realCameraEvidenceStatus.status }}</span></div>
        <div class="flex justify-between gap-3"><span class="text-slate-500">Latest self-test</span><span class="text-right font-mono">{{ selfTest.latestRun?.run_id ?? 'none' }}</span></div>
      </div>
    </section>
  </div>
</template>
