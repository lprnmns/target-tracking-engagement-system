<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import DashboardCard from '../components/dashboard/DashboardCard.vue'
import MetricRow from '../components/dashboard/MetricRow.vue'
import StatusBadge from '../components/shared/StatusBadge.vue'
import { useSelfTestStore } from '../stores/selfTestStore'
import { useFirstRunStore } from '../stores/firstRunStore'

const selfTest = useSelfTestStore()
const firstRun = useFirstRunStore()
const statusFilter = ref('all')
const categoryFilter = ref('all')
const latest = computed(() => selfTest.latestRun)
const criticalFailures = computed(() => latest.value?.summary.critical_failures ?? 0)
const warnings = computed(() => latest.value?.summary.warning ?? 0)
const statusTone = computed(() => {
  if (!latest.value) return 'neutral'
  if (latest.value.status === 'failed') return 'bad'
  if (latest.value.status === 'warning') return 'warn'
  if (latest.value.status === 'passed') return 'good'
  return 'warn'
})
const failedOrWarningSteps = computed(() => latest.value?.steps.filter((step) => ['failed', 'warning'].includes(step.status)) ?? [])
const categories = computed(() => ['all', ...Array.from(new Set(latest.value?.steps.map((step) => step.category) ?? [])).sort()])
const filteredSteps = computed(() => (latest.value?.steps ?? []).filter((step) => {
  const matchesStatus = statusFilter.value === 'all' || step.status === statusFilter.value || (statusFilter.value === 'critical' && step.severity === 'critical')
  const matchesCategory = categoryFilter.value === 'all' || step.category === categoryFilter.value
  return matchesStatus && matchesCategory
}))
const groupedSteps = computed(() => {
  const groups: Record<string, typeof filteredSteps.value> = {}
  for (const step of filteredSteps.value) {
    groups[step.category] ??= []
    groups[step.category].push(step)
  }
  return groups
})

function stepTone(status: string): 'good' | 'warn' | 'bad' | 'neutral' {
  if (status === 'passed') return 'good'
  if (status === 'warning' || status === 'skipped' || status === 'running') return 'warn'
  if (status === 'failed') return 'bad'
  return 'neutral'
}

onMounted(() => {
  void selfTest.refresh()
  void firstRun.refresh()
})
</script>

<template>
  <div class="grid gap-4">
    <div class="rounded-md border border-red-400/30 bg-red-500/10 px-4 py-3 text-sm text-red-100">
      Self-test readiness does not enable physical fire. No motor, servo, fire, or physical serial command is generated.
    </div>
    <div class="rounded-md border border-amber-400/30 bg-amber-400/10 px-4 py-3 text-sm text-amber-100">
      Release candidate: can pass with test adapter/mock camera. Competition rehearsal: requires production YOLO, verified Pico telemetry and real camera profile.
    </div>

    <div class="grid gap-4 xl:grid-cols-3">
      <DashboardCard title="Overall Readiness" subtitle="Dry-run acceptance status">
        <div class="mb-4 flex flex-wrap gap-2">
          <StatusBadge :label="latest?.status ?? 'not run'" :tone="statusTone" />
          <StatusBadge :label="latest?.readiness_level ?? 'not_ready'" :tone="latest?.overall_ready ? 'good' : 'bad'" />
          <StatusBadge label="NO PHYSICAL COMMAND" tone="bad" />
        </div>
        <MetricRow label="Run ID" :value="latest?.run_id ?? 'none'" />
        <MetricRow label="Critical failures" :value="criticalFailures" />
        <MetricRow label="Warnings" :value="warnings" />
        <MetricRow label="Last run" :value="latest?.ended_at ? new Date(latest.ended_at * 1000).toLocaleString() : 'not run'" />
      </DashboardCard>

      <DashboardCard title="Controls" subtitle="Synchronous backend self-test">
        <div class="flex flex-wrap gap-2">
          <button class="focus-ring rounded-md bg-cyan-500 px-3 py-2 text-sm font-semibold text-slate-950" :disabled="selfTest.isRunning" @click="selfTest.run">
            Run self-test
          </button>
          <button class="focus-ring rounded-md bg-slate-700 px-3 py-2 text-sm font-semibold text-white" :disabled="!selfTest.isRunning" @click="selfTest.cancel">
            Cancel
          </button>
          <a v-if="latest?.report_path" class="focus-ring rounded-md bg-emerald-500 px-3 py-2 text-sm font-semibold text-slate-950" :href="selfTest.reportUrl(latest.run_id)" target="_blank">
            View report
          </a>
          <button class="focus-ring rounded-md border border-amber-400/40 bg-amber-400/12 px-3 py-2 text-sm font-semibold text-amber-100" :disabled="firstRun.isChecking" @click="firstRun.check()">
            Run first-run acceptance
          </button>
        </div>
        <div class="mt-4 h-3 overflow-hidden rounded-full bg-white/10">
          <div class="h-full bg-cyan-400 transition-all" :style="{ width: `${selfTest.progress}%` }" />
        </div>
        <p class="mt-2 text-xs text-slate-400">{{ selfTest.progress }}% complete</p>
        <p v-if="selfTest.error" class="mt-3 rounded-md border border-red-400/30 bg-red-500/10 p-2 text-sm text-red-100">{{ selfTest.error }}</p>
      </DashboardCard>

      <DashboardCard title="Safety Evidence" subtitle="Readiness is not authorization">
        <MetricRow label="dry_run" :value="latest?.dry_run ?? true" />
        <MetricRow label="hardware_enabled" :value="latest?.hardware_enabled ?? false" />
        <MetricRow label="No physical command" :value="latest?.no_physical_command_generated ?? true" />
        <MetricRow label="Report path" :value="latest?.report_path ?? 'none'" />
      </DashboardCard>
    </div>

    <DashboardCard title="Category Summary" subtitle="Step counts by subsystem">
      <div class="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        <div v-for="(summary, category) in selfTest.categorySummary" :key="category" class="rounded-md border border-white/8 bg-black/18 p-3">
          <p class="text-sm font-semibold uppercase tracking-[0.12em] text-cyan-200">{{ category }}</p>
          <div class="mt-3 flex flex-wrap gap-1.5">
            <StatusBadge :label="`pass ${summary.passed}`" tone="good" />
            <StatusBadge :label="`warn ${summary.warning}`" tone="warn" />
            <StatusBadge :label="`fail ${summary.failed}`" tone="bad" />
            <StatusBadge :label="`skip ${summary.skipped}`" tone="neutral" />
          </div>
        </div>
        <p v-if="Object.keys(selfTest.categorySummary).length === 0" class="text-sm text-slate-400">Run self-test to populate category summary.</p>
      </div>
    </DashboardCard>

    <DashboardCard title="Warnings and Suggested Actions" subtitle="Operator attention list">
      <div class="grid gap-2">
        <div v-for="step in failedOrWarningSteps" :key="step.step_id" class="rounded-md border border-white/8 bg-black/18 p-3 text-sm">
          <div class="flex flex-wrap items-center gap-2">
            <StatusBadge :label="step.status" :tone="stepTone(step.status)" />
            <StatusBadge :label="step.severity" :tone="step.severity === 'critical' ? 'bad' : step.severity === 'warning' ? 'warn' : 'neutral'" />
            <span class="font-semibold text-white">{{ step.name }}</span>
          </div>
          <p class="mt-2 text-slate-300">{{ step.message }}</p>
          <p v-if="step.suggested_action" class="mt-1 text-amber-100">{{ step.suggested_action }}</p>
        </div>
        <p v-if="failedOrWarningSteps.length === 0" class="text-sm text-slate-400">No warnings or failures in latest run.</p>
      </div>
    </DashboardCard>

    <DashboardCard title="Step Timeline" subtitle="Full acceptance checklist">
      <div class="mb-3 flex flex-wrap gap-2">
        <select v-model="statusFilter" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm text-white">
          <option value="all">all statuses</option>
          <option value="critical">critical severity</option>
          <option value="failed">failed</option>
          <option value="warning">warning</option>
          <option value="passed">passed</option>
          <option value="skipped">skipped</option>
        </select>
        <select v-model="categoryFilter" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm text-white">
          <option v-for="category in categories" :key="category" :value="category">{{ category }}</option>
        </select>
      </div>
      <div class="mb-4 grid gap-2">
        <details v-for="(steps, category) in groupedSteps" :key="category" open class="rounded-md border border-white/8 bg-black/18 p-3">
          <summary class="cursor-pointer text-sm font-semibold uppercase tracking-[0.12em] text-cyan-200">{{ category }} · {{ steps.length }} steps</summary>
          <div class="mt-3 grid gap-2">
            <div v-for="step in steps" :key="step.step_id" class="grid gap-2 rounded-md border border-white/8 bg-black/24 p-3 text-sm md:grid-cols-[1fr_120px_120px_1.5fr]">
              <span>
                <span class="font-semibold text-white">{{ step.name }}</span>
                <p class="font-mono text-xs text-slate-500">{{ step.step_id }}</p>
              </span>
              <StatusBadge :label="step.status" :tone="stepTone(step.status)" />
              <span class="text-slate-300">{{ step.severity }}</span>
              <span class="text-slate-300">{{ step.message }}</span>
            </div>
          </div>
        </details>
      </div>
      <div class="overflow-x-auto">
        <table class="w-full min-w-[940px] text-left text-sm">
          <thead class="text-xs uppercase tracking-[0.14em] text-slate-500">
            <tr><th class="py-2">Step</th><th>Category</th><th>Status</th><th>Severity</th><th>Duration</th><th>Message</th></tr>
          </thead>
          <tbody>
            <tr v-for="step in filteredSteps" :key="step.step_id" class="border-t border-white/8">
              <td class="py-2">
                <span class="font-semibold text-white">{{ step.name }}</span>
                <p class="font-mono text-xs text-slate-500">{{ step.step_id }}</p>
              </td>
              <td>{{ step.category }}</td>
              <td><StatusBadge :label="step.status" :tone="stepTone(step.status)" /></td>
              <td>{{ step.severity }}</td>
              <td>{{ step.duration_ms ?? 0 }} ms</td>
              <td class="max-w-[360px] text-slate-300">{{ step.message }}</td>
            </tr>
          </tbody>
        </table>
        <p v-if="!latest" class="py-3 text-sm text-slate-400">No self-test run yet.</p>
      </div>
    </DashboardCard>
  </div>
</template>
