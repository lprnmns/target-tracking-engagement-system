<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import DashboardCard from '../components/dashboard/DashboardCard.vue'
import MetricRow from '../components/dashboard/MetricRow.vue'
import StatusBadge from '../components/shared/StatusBadge.vue'
import { useFirstRunStore } from '../stores/firstRunStore'
import { useReleaseStore } from '../stores/releaseStore'

const firstRun = useFirstRunStore()
const release = useReleaseStore()
const selectedStepId = ref<string | null>(null)

const wizardSteps = [
  'Welcome / Mode',
  'Dependency check',
  'Device discovery',
  'Camera selection',
  'Model / adapter selection',
  'Safety invariant',
  'Self-test',
  'Report export',
]

const selectedStep = computed(() => firstRun.currentReport?.steps.find((step) => step.step_id === selectedStepId.value) ?? firstRun.currentReport?.steps[0] ?? null)
const profileChecklist = computed(() => firstRun.currentReport?.profile_checklists?.[firstRun.currentProfileId as keyof typeof firstRun.currentReport.profile_checklists] ?? [])
const profileStatus = computed(() => firstRun.currentProfileEvaluationStatus)
const firstRunTone = computed(() => firstRun.displayStatus === 'PASSED' ? 'good' : firstRun.displayStatus === 'FAILED' ? 'bad' : firstRun.displayStatus === 'WARNING' ? 'warn' : 'warn')

function tone(status: string): 'good' | 'warn' | 'bad' | 'neutral' {
  if (status === 'passed') return 'good'
  if (status === 'failed') return 'bad'
  if (status === 'warning') return 'warn'
  return 'neutral'
}

onMounted(() => {
  void firstRun.refresh()
  void release.coldStartCheck()
})
</script>

<template>
  <div class="grid gap-4">
    <DashboardCard title="First Run Wizard" subtitle="Portable startup acceptance">
      <div class="flex flex-wrap gap-2">
        <StatusBadge :label="`ENV: ${firstRun.status.mode.toUpperCase()}`" tone="neutral" />
        <StatusBadge :label="firstRun.displayBadge" :tone="firstRunTone" />
        <StatusBadge label="NO PHYSICAL COMMAND" tone="bad" />
      </div>
      <div class="mt-4 grid gap-4 md:grid-cols-3">
        <MetricRow label="Passed" :value="firstRun.passedCount" />
        <MetricRow label="Warnings" :value="firstRun.warningCount" />
        <MetricRow label="Failed" :value="firstRun.failedCount" />
      </div>
      <div class="mt-4 grid gap-3 md:grid-cols-[240px_1fr]">
        <select :value="firstRun.currentProfileId" disabled class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm text-white disabled:opacity-80">
          <option value="development_ready">development_ready</option>
          <option value="demo_ready">demo_ready</option>
          <option value="field_dry_run_ready">field_dry_run_ready</option>
          <option value="hardware_telemetry_ready">hardware_telemetry_ready</option>
          <option value="competition_rehearsal_ready">competition_rehearsal_ready</option>
          <option value="release_candidate_ready">release_candidate_ready</option>
        </select>
        <StatusBadge :label="`PROFILE STATUS: ${String(profileStatus).replace('_', ' ').toUpperCase()}`" :tone="profileStatus === 'passed' ? 'good' : profileStatus === 'failed' || profileStatus === 'blocked' ? 'bad' : profileStatus === 'not_evaluated' ? 'neutral' : 'warn'" />
      </div>
      <div class="mt-3 grid gap-2 rounded-md border border-white/8 bg-black/18 p-3 text-sm text-slate-300 md:grid-cols-2">
        <p><span class="font-semibold text-cyan-100">Release candidate</span> can run without hardware.</p>
        <p><span class="font-semibold text-amber-100">Competition rehearsal</span> requires production model and verified telemetry.</p>
      </div>
      <p class="mt-3 rounded-md border border-cyan-400/30 bg-cyan-400/10 px-3 py-2 text-sm text-cyan-100">
        Release candidate profili, yazılımın taşınabilir/demo çalışmasını doğrular; production YOLO ve gerçek Pico telemetry yarışma provası için ayrıca gerekir.
      </p>
      <div class="mt-4 flex flex-wrap gap-2">
        <button class="focus-ring rounded-md border border-cyan-400/40 bg-cyan-400/12 px-3 py-2 text-sm font-semibold text-cyan-100 disabled:opacity-50" :disabled="firstRun.isChecking" @click="firstRun.check()">
          Run first-run acceptance
        </button>
        <button class="focus-ring rounded-md border border-emerald-400/40 bg-emerald-400/12 px-3 py-2 text-sm font-semibold text-emerald-100" @click="firstRun.complete()">
          Mark complete
        </button>
        <button class="focus-ring rounded-md border border-white/15 px-3 py-2 text-sm text-slate-200" @click="firstRun.reset()">
          Reset
        </button>
      </div>
      <p v-if="firstRun.error" class="mt-3 rounded-md border border-red-400/30 bg-red-400/10 px-3 py-2 text-sm text-red-100">{{ firstRun.error }}</p>
    </DashboardCard>

    <DashboardCard title="Cold-start Evidence" subtitle="First install release proof">
      <div class="mb-3 flex flex-wrap gap-2">
        <StatusBadge :label="`COLD START: ${release.status.status.toUpperCase()}`" :tone="release.status.status === 'passed' ? 'good' : release.status.status === 'failed' ? 'bad' : 'warn'" />
        <StatusBadge label="CAN RUN WITHOUT HARDWARE" tone="warn" />
        <StatusBadge label="NO PHYSICAL COMMAND" tone="bad" />
      </div>
      <div class="grid gap-3 md:grid-cols-2">
        <MetricRow label="Python" :value="release.status.python_version ?? 'unknown'" />
        <MetricRow label="Platform" :value="release.status.platform ?? 'unknown'" />
        <MetricRow label="Frontend dist" :value="release.status.frontend_static_available" />
        <MetricRow label="Logs/exports writable" :value="release.status.writable_runtime_dirs" />
        <MetricRow label="Model kind" :value="String(release.status.cold_start_evidence.active_model_kind ?? 'unknown')" />
        <MetricRow label="Camera source" :value="String(release.status.cold_start_evidence.camera_source ?? 'unknown')" />
        <MetricRow label="Pico state" :value="String(release.status.cold_start_evidence.pico_state ?? 'unknown')" />
        <MetricRow label="Safety invariant" :value="release.status.safety_invariant_ok" />
      </div>
      <div class="mt-3 grid gap-2 rounded-md border border-white/8 bg-black/18 p-3 text-sm text-slate-300 md:grid-cols-2">
        <p><span class="font-semibold text-cyan-100">Release candidate</span> can run without hardware and can use mock camera or a declared test adapter.</p>
        <p><span class="font-semibold text-amber-100">Competition rehearsal</span> requires production YOLO, verified Pico telemetry and a real camera profile.</p>
      </div>
      <p class="mt-3 rounded-md border border-amber-400/30 bg-amber-400/10 px-3 py-2 text-sm text-amber-100">
        Test adapter veya mock camera yeşil yarışma hazırlığı değildir; yalnızca release-candidate/demo limitation olarak değerlendirilir.
      </p>
      <button class="focus-ring mt-3 rounded-md border border-cyan-400/40 bg-cyan-400/12 px-3 py-2 text-sm font-semibold text-cyan-100" @click="release.coldStartCheck()">
        Run cold-start evidence
      </button>
    </DashboardCard>

    <DashboardCard title="Profile-specific Checklist" subtitle="Readiness profile is not a single global pass">
      <div class="grid gap-2">
        <div v-for="step in profileChecklist" :key="step.step_id" class="grid gap-2 rounded-md border border-white/8 bg-black/18 p-3 text-sm md:grid-cols-[180px_1fr_180px]">
          <StatusBadge :label="step.status" :tone="tone(step.status)" />
          <div>
            <p class="font-semibold text-white">{{ step.title }}</p>
            <p class="text-xs text-slate-400">{{ step.explanation }}</p>
          </div>
          <p class="text-xs text-amber-100">{{ step.suggested_fix ?? 'no action' }}</p>
        </div>
        <p v-if="profileChecklist.length === 0" class="text-sm text-slate-400">Run first-run acceptance to evaluate readiness profiles.</p>
      </div>
    </DashboardCard>

    <div class="grid gap-4 xl:grid-cols-[320px_1fr]">
      <DashboardCard title="Wizard Steps" subtitle="Operator flow">
        <div class="grid gap-2">
          <div v-for="(step, index) in wizardSteps" :key="step" class="rounded-md border border-white/8 bg-black/18 p-3">
            <p class="text-sm font-semibold text-white">{{ index + 1 }}. {{ step }}</p>
            <p class="mt-1 text-xs text-slate-400">Status is derived from the acceptance checks on the right.</p>
          </div>
        </div>
      </DashboardCard>

      <DashboardCard title="Acceptance Checks" subtitle="Dependency, device and safety readiness">
        <div class="grid gap-2">
          <button
            v-for="step in firstRun.currentReport?.steps ?? []"
            :key="step.step_id"
            class="focus-ring grid gap-2 rounded-md border border-white/8 bg-black/18 p-3 text-left md:grid-cols-[180px_1fr_160px]"
            :class="{ 'border-cyan-400/50 bg-cyan-400/8': selectedStep?.step_id === step.step_id }"
            @click="selectedStepId = step.step_id"
          >
            <StatusBadge :label="step.status" :tone="tone(step.status)" />
            <div>
              <p class="text-sm font-semibold text-white">{{ step.title }}</p>
              <p class="text-xs text-slate-400">{{ step.explanation }}</p>
            </div>
            <p class="text-xs text-slate-500">{{ step.step_id }}</p>
          </button>
          <p v-if="!firstRun.currentReport" class="text-sm text-slate-400">Run first-run acceptance to populate checks.</p>
        </div>
      </DashboardCard>
    </div>

    <DashboardCard title="Check Detail" subtitle="Suggested fix and raw detail">
      <template v-if="selectedStep">
        <div class="flex flex-wrap gap-2">
          <StatusBadge :label="selectedStep.status" :tone="tone(selectedStep.status)" />
          <StatusBadge :label="selectedStep.blocking ? 'BLOCKING' : 'NON-BLOCKING'" :tone="selectedStep.blocking ? 'bad' : 'neutral'" />
        </div>
        <MetricRow label="Step" :value="selectedStep.title" />
        <MetricRow label="Suggested fix" :value="selectedStep.suggested_fix ?? 'none'" />
        <details class="mt-3 rounded-md border border-white/8 bg-black/24 p-3">
          <summary class="cursor-pointer text-sm font-semibold text-slate-200">Raw detail</summary>
          <pre class="mt-3 overflow-auto text-xs text-slate-300">{{ JSON.stringify(selectedStep.detail, null, 2) }}</pre>
        </details>
      </template>
      <p v-else class="text-sm text-slate-400">Select a check to inspect details.</p>
    </DashboardCard>
  </div>
</template>
