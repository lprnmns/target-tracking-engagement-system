<script setup lang="ts">
import { computed, onMounted } from 'vue'
import DashboardCard from '../components/dashboard/DashboardCard.vue'
import MetricRow from '../components/dashboard/MetricRow.vue'
import StatusBadge from '../components/shared/StatusBadge.vue'
import { useDeviceRuntimeStore } from '../stores/deviceRuntimeStore'
import { useModelPackageStore } from '../stores/modelPackageStore'

const models = useModelPackageStore()
const runtime = useDeviceRuntimeStore()
const selected = computed(() => models.selectedPackage)
const validation = computed(() => selected.value?.validation ?? null)
const thresholds = computed(() => selected.value?.thresholds ?? null)
const metadata = computed(() => selected.value?.metadata ?? null)
const activeDetails = computed(() => runtime.visionStatus.active_model_details)
const semantic = computed(() => {
  const details = selected.value?.active ? activeDetails.value : {}
  return {
    package_schema_status: String(details.package_schema_status ?? (selected.value?.validation?.valid ? 'passed' : 'not_validated')),
    runtime_status: String(details.runtime_status ?? 'not_checked'),
    production_status: String(details.production_status ?? (metadata.value?.production_ready ? 'production_model_loaded' : 'test_adapter_only')),
    competition_status: String(details.competition_status ?? (metadata.value?.production_ready && selected.value?.last_test_result ? 'rehearsal_ready' : selected.value ? 'limited_demo_only' : 'blocked')),
    package_kind: String(details.package_kind ?? (metadata.value?.production_ready ? 'production' : 'fixture')),
    adapter_mode: String(details.adapter_mode ?? (metadata.value?.production_ready ? 'ultralytics_yolo' : 'fixture_test_adapter')),
  }
})
const isProduction = computed(() => metadata.value?.production_ready === true)
const productionReady = computed(() => selected.value?.active ? activeDetails.value.production_ready === true : false)
const competitionReady = computed(() => selected.value?.active ? activeDetails.value.competition_ready === true : false)
const activeBadge = computed(() => {
  if (!selected.value?.active) return 'PASSIVE'
  return isProduction.value ? 'PRODUCTION ACTIVE' : 'TEST ADAPTER ACTIVE'
})
const activateLabel = computed(() => isProduction.value ? 'Activate production model' : 'Activate test adapter')
const currentRuntimeSummary = computed(() => {
  const profile = runtime.visionStatus.profile
  return `${runtime.visionStatus.effective_adapter} · imgsz ${profile.imgsz} · conf ${profile.conf} · iou ${profile.iou} · max_det ${profile.max_det}`
})
const activeModelRows = computed(() => [
  ['active_model_id', selected.value?.active ? selected.value.model_id : 'not_active'],
  ['package_id', selected.value?.package_name ?? 'not_available'],
  ['model_file', selected.value?.model_file ?? 'not_loaded'],
  ['model_format', metadata.value?.model_format ?? 'not_available'],
  ['package_kind', semantic.value.package_kind],
  ['adapter_mode', semantic.value.adapter_mode],
  ['package_schema_status', semantic.value.package_schema_status],
  ['runtime_status', semantic.value.runtime_status],
  ['production_status', semantic.value.production_status],
  ['competition_status', semantic.value.competition_status],
  ['production_ready', String(productionReady.value)],
  ['competition_ready', String(competitionReady.value)],
  ['last_test_status', selected.value?.last_test_result ? 'completed' : 'not_tested'],
])
const safetyEvidenceRows = computed(() => [
  ['advisory_only', 'true'],
  ['dry_run', 'true'],
  ['physical_command_enabled', 'false'],
  ['no_physical_command_generated', String(selected.value?.no_physical_command_generated ?? true)],
  ['production_ready', String(productionReady.value)],
  ['competition_ready', String(competitionReady.value)],
])

function tone(value: string | boolean | null | undefined): 'good' | 'warn' | 'bad' | 'neutral' {
  if (value === true || value === 'passed' || value === 'validated' || value === 'active' || value === 'complete') return 'good'
  if (value === false || value === 'failed' || value === 'invalid' || value === 'missing_file' || value === 'missing_required_classes') return 'bad'
  if (value === 'warning' || value === 'imported' || value === 'inactive') return 'warn'
  return 'neutral'
}

onMounted(() => {
  void models.refresh()
  void runtime.refresh()
})
</script>

<template>
  <div class="grid gap-4">
    <div class="rounded-md border border-red-400/30 bg-red-500/8 px-4 py-3">
      <div class="flex flex-wrap gap-2">
        <StatusBadge label="ADVISORY ONLY" tone="warn" />
        <StatusBadge label="NO PHYSICAL COMMAND" tone="bad" />
        <StatusBadge label="MODEL TEST DOES NOT ENABLE FIRE" tone="bad" />
      </div>
      <p class="mt-2 text-sm text-slate-300">
        Bu ekran görüntü işleme ekibinden gelen model paketini kod değişmeden import/doğrulama/aktif model/test akışına alır. Model çıktısı yalnızca metadata üretir; fiziksel hareket veya atış komutu üretmez.
      </p>
    </div>

    <div class="grid gap-4 xl:grid-cols-[1.2fr_1fr]">
      <DashboardCard title="Import Model Package" subtitle="Incoming klasörü veya fixture path">
        <label class="grid gap-1 text-sm text-slate-300">
          Paket yolu
          <input v-model="models.importPath" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 font-mono text-sm text-white" />
        </label>
        <div class="mt-3 flex flex-wrap gap-2">
          <button class="focus-ring rounded-md bg-cyan-500 px-3 py-2 text-sm font-semibold text-slate-950 disabled:opacity-50" :disabled="models.isBusy" @click="models.importSelected">
            Import package
          </button>
          <button class="focus-ring rounded-md bg-slate-700 px-3 py-2 text-sm font-semibold text-white disabled:opacity-50" :disabled="models.isBusy || !selected" @click="models.validateSelected">
            Validate package
          </button>
          <button class="focus-ring rounded-md bg-emerald-500 px-3 py-2 text-sm font-semibold text-slate-950 disabled:opacity-50" :disabled="models.isBusy || !selected || validation?.valid === false" @click="models.activateSelected">
            {{ activateLabel }}
          </button>
          <button class="focus-ring rounded-md bg-slate-700 px-3 py-2 text-sm font-semibold text-white disabled:opacity-50" :disabled="models.isBusy || !selected" @click="models.deactivateSelected">
            Deactivate
          </button>
        </div>
        <p v-if="models.error" class="mt-3 rounded-md border border-red-400/30 bg-red-400/10 px-3 py-2 text-sm text-red-100">{{ models.error }}</p>
      </DashboardCard>

      <DashboardCard title="Safety Evidence" subtitle="Model handoff invariant">
        <div class="grid gap-2">
          <div v-for="[label, value] in safetyEvidenceRows" :key="label" class="rounded-md border border-white/8 bg-black/18 px-3 py-2">
            <p class="text-xs uppercase tracking-[0.14em] text-slate-500">{{ label }}</p>
            <p class="mt-1 break-words font-mono text-sm text-white">{{ value }}</p>
          </div>
        </div>
        <p class="mt-3 rounded-md border border-amber-400/30 bg-amber-400/10 px-3 py-2 text-sm text-amber-100">
          Bu test yalnızca görüntü işleme çıktısını doğrular; fiziksel komut üretmez.
        </p>
      </DashboardCard>
    </div>

    <div class="grid gap-4 xl:grid-cols-[1.25fr_1fr]">
      <DashboardCard title="Model Package Inventory" subtitle="Registry and package status">
        <div class="overflow-x-auto">
          <table class="w-full min-w-[980px] text-left text-sm">
            <thead class="text-xs uppercase tracking-[0.14em] text-slate-500">
              <tr><th class="py-2">model_id</th><th>Version</th><th>Format</th><th>Package status</th><th>Package validation</th><th>Active state</th><th>Kind</th><th>Checksum</th></tr>
            </thead>
            <tbody>
              <tr
                v-for="item in models.packages"
                :key="`${item.model_id}-${item.version}`"
                class="cursor-pointer border-t border-white/8 hover:bg-white/5"
                :class="{ 'bg-cyan-400/10': selected?.model_id === item.model_id }"
                @click="models.selectedModelId = item.model_id"
              >
                <td class="py-2 font-mono text-cyan-200">{{ item.model_id }}</td>
                <td>{{ item.version }}</td>
                <td>{{ item.metadata?.model_format ?? 'unknown' }}</td>
                <td><StatusBadge :label="item.status === 'validated' ? 'PACKAGE VALID' : item.status" :tone="tone(item.status)" /></td>
                <td><StatusBadge :label="item.validation?.valid ? 'PACKAGE VALID' : 'NOT VALIDATED'" :tone="tone(item.validation?.valid)" /></td>
                <td><StatusBadge :label="item.active ? (item.metadata?.production_ready ? 'PRODUCTION ACTIVE' : 'TEST ADAPTER ACTIVE') : 'PASSIVE'" :tone="item.active ? (item.metadata?.production_ready ? 'good' : 'warn') : 'neutral'" /></td>
                <td><StatusBadge :label="item.metadata?.production_ready ? 'PRODUCTION MODEL' : 'FIXTURE / TEST ONLY'" :tone="item.metadata?.production_ready ? 'good' : 'warn'" /></td>
                <td class="max-w-[180px] truncate font-mono text-xs text-slate-400">{{ item.checksum_sha256 ?? 'none' }}</td>
              </tr>
            </tbody>
          </table>
          <p v-if="models.packages.length === 0" class="py-8 text-center text-sm text-slate-400">Model paketi yüklü değil. Fixture veya vision team paketi import edin.</p>
        </div>
      </DashboardCard>

      <DashboardCard title="Active Model" subtitle="Operator-readable production status">
        <template v-if="selected">
          <div v-if="!isProduction" class="mb-3 rounded-md border border-red-400/40 bg-red-500/12 px-3 py-3 text-sm font-semibold text-red-100">
            Yarışma modeli yüklü değil. Aktif model yalnızca test/fixture adaptörüdür.
          </div>
          <div class="grid gap-2">
            <div v-for="[label, value] in activeModelRows" :key="label" class="rounded-md border border-white/8 bg-black/18 px-3 py-2">
              <p class="text-xs uppercase tracking-[0.14em] text-slate-500">{{ label }}</p>
              <p class="mt-1 break-words font-mono text-sm text-white">{{ value }}</p>
            </div>
          </div>
          <div class="mt-3 flex flex-wrap gap-2">
            <StatusBadge :label="activeBadge" :tone="isProduction ? 'good' : 'warn'" />
            <StatusBadge v-if="!metadata?.production_ready" label="FIXTURE / TEST ONLY" tone="warn" />
          </div>
        </template>
        <p v-else class="text-sm text-slate-400">Aktif model yok.</p>
      </DashboardCard>
    </div>

    <div class="grid gap-4 xl:grid-cols-3">
      <DashboardCard title="Class Mapping Review" subtitle="Competition classes">
        <div class="grid gap-2">
          <div class="grid grid-cols-[70px_1fr_1fr_100px_auto] gap-2 px-3 text-[11px] font-semibold uppercase tracking-[0.12em] text-slate-500">
            <span>raw id</span><span>model class name</span><span>mapped target group</span><span>need</span><span>status</span>
          </div>
          <div
            v-for="item in validation?.class_mapping ?? []"
            :key="`${item.class_id}-${item.class_name}`"
            class="grid grid-cols-[70px_1fr_1fr_100px_auto] items-center gap-2 rounded-md border border-white/8 bg-black/18 px-3 py-2 text-sm"
          >
            <span class="font-mono text-slate-400">{{ item.class_id }}</span>
            <span class="break-words">{{ item.class_name }}</span>
            <span class="break-words">{{ item.mapped_role }}</span>
            <span>{{ item.required ? 'required' : 'optional' }}</span>
            <StatusBadge :label="item.status" :tone="tone(item.status)" />
          </div>
          <p v-if="!validation?.class_mapping?.length" class="text-sm text-slate-400">Class mapping henüz doğrulanmadı.</p>
        </div>
      </DashboardCard>

      <DashboardCard title="Runtime Compatibility" subtitle="Recommended versus current">
        <MetricRow label="recommended_imgsz" :value="metadata?.recommended_imgsz ?? 'missing'" />
        <MetricRow label="recommended_conf" :value="metadata?.recommended_conf ?? 'missing'" />
        <MetricRow label="recommended_iou" :value="metadata?.recommended_iou ?? 'missing'" />
        <MetricRow label="max_det" :value="thresholds?.max_det ?? 'missing'" />
        <MetricRow label="preset" :value="thresholds?.recommended_runtime_preset ?? 'missing'" />
        <MetricRow label="current_runtime" :value="currentRuntimeSummary" />
        <p class="mt-2 text-xs text-slate-400">
          Apply recommended settings yalnızca vision runtime ayarlarını değiştirir; safety state, fire policy ve hardware state değişmez.
        </p>
        <button class="focus-ring mt-3 rounded-md border border-cyan-400/40 bg-cyan-400/12 px-3 py-2 text-sm font-semibold text-cyan-100 disabled:opacity-50" :disabled="models.isBusy || !selected" @click="models.applyRecommended">
          Apply recommended settings
        </button>
      </DashboardCard>

      <DashboardCard title="Dry-run Test and Benchmark" subtitle="No physical output">
        <div class="flex flex-wrap gap-2">
          <button class="focus-ring rounded-md bg-emerald-500 px-3 py-2 text-sm font-semibold text-slate-950 disabled:opacity-50" :disabled="models.isBusy || !selected" @click="models.testSelected">Run model test</button>
          <button class="focus-ring rounded-md bg-slate-700 px-3 py-2 text-sm font-semibold text-white disabled:opacity-50" :disabled="models.isBusy || !selected" @click="models.benchmarkSelected">Benchmark</button>
        </div>
        <pre class="mt-3 max-h-72 overflow-auto rounded-md border border-white/8 bg-black/30 p-3 text-xs text-slate-300">{{ JSON.stringify(models.lastResult ?? selected?.last_test_result ?? {}, null, 2) }}</pre>
      </DashboardCard>
    </div>
  </div>
</template>
