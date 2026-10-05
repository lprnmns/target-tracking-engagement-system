<script setup lang="ts">
import { computed, onMounted } from 'vue'
import DashboardCard from '../components/dashboard/DashboardCard.vue'
import MetricRow from '../components/dashboard/MetricRow.vue'
import StatusBadge from '../components/shared/StatusBadge.vue'
import { useDecisionStore } from '../stores/decisionStore'
import { useSystemStore } from '../stores/systemStore'
import { dedupe, gateGroup, gateLabel, readableReasonText, reasonLabel } from '../utils/safetyLabels'

const decisionStore = useDecisionStore()
const systemStore = useSystemStore()

const rangeRules = [
  'f16: 10.0-15.0 m',
  'helicopter: 5.0-15.0 m',
  'ballistic_missile: 5.0-15.0 m',
  'mini_micro_uav: 0.0-15.0 m',
]

const friendWarning = computed(() => decisionStore.decision.blocking_reasons.includes('target_is_friend'))
const groupedGates = computed(() => {
  const groups = ['System Gates', 'Target Gates', 'Motion Gates', 'Advisory/Mock Gates'] as const
  return groups.map((group) => ({
    group,
    gates: decisionStore.decision.gates.filter((gate) => gateGroup(gate.name) === group),
  })).filter((item) => item.gates.length > 0)
})
const blockingReasons = computed(() => dedupe(decisionStore.decision.blocking_reasons))
const decisionReason = computed(() => readableReasonText(decisionStore.decision.decision_reason))
const fireBlockingReasons = computed(() => dedupe(decisionStore.latestFireResult?.blocking_reasons ?? []))
const fireGateSummary = computed(() => {
  const gates = decisionStore.latestFireResult?.gates ?? []
  return {
    pass: gates.filter((gate) => gate.status === 'pass').length,
    fail: gates.filter((gate) => gate.status === 'fail').length,
    warning: gates.filter((gate) => gate.status === 'warning').length,
    notApplicable: gates.filter((gate) => gate.status === 'not_applicable').length,
  }
})

function toneFor(status: string): 'good' | 'warn' | 'bad' | 'neutral' {
  if (status === 'pass') return 'good'
  if (status === 'warning' || status === 'not_applicable') return 'warn'
  return 'bad'
}

onMounted(() => {
  void decisionStore.refresh()
})
</script>

<template>
  <div class="grid gap-4">
    <div class="rounded-md border border-red-400/30 bg-red-400/10 px-4 py-3 text-sm text-red-100">
      No physical fire command is generated in Phase 6.
    </div>
    <div v-if="friendWarning" class="rounded-md border border-red-500 bg-red-500/20 px-4 py-3 text-sm font-semibold text-red-100">
      NO_FIRE: target classified as friend.
    </div>

    <div class="grid gap-4 xl:grid-cols-3">
      <DashboardCard title="Current Decision" subtitle="Decision engine output">
        <MetricRow label="State" :value="decisionStore.decision.decision_state" />
        <MetricRow label="Reason" :value="decisionReason" />
        <MetricRow label="Active target" :value="decisionStore.decision.active_target_id" />
        <MetricRow label="Updated" :value="decisionStore.decision.updated_at ? new Date(decisionStore.decision.updated_at * 1000).toLocaleTimeString() : 'never'" />
      </DashboardCard>

      <DashboardCard title="Fire Policy" subtitle="Reject-by-default">
        <MetricRow label="Policy" :value="decisionStore.decision.fire_policy" />
        <MetricRow label="System mode" :value="systemStore.systemState.mode" />
        <MetricRow label="Armed" :value="systemStore.systemState.armed" />
        <MetricRow label="dry_run" :value="systemStore.systemState.dry_run" />
        <MetricRow label="hardware_enabled" :value="systemStore.systemState.hardware_enabled" />
      </DashboardCard>

      <DashboardCard title="Controls" subtitle="Dry-run evaluation only">
        <div class="flex flex-wrap gap-2">
          <button
            class="focus-ring rounded-md bg-amber-500 px-3 py-2 text-sm font-semibold text-slate-950 disabled:cursor-not-allowed disabled:opacity-60"
            :disabled="systemStore.systemState.armed"
            @click="decisionStore.arm"
          >
            {{ systemStore.systemState.armed ? 'Armed Dry-run Active' : 'Arm Dry-run' }}
          </button>
          <button class="focus-ring rounded-md bg-slate-700 px-3 py-2 text-sm font-semibold text-white" @click="decisionStore.disarm">Disarm</button>
          <button v-if="systemStore.systemState.armed" class="focus-ring rounded-md bg-amber-500/70 px-3 py-2 text-sm font-semibold text-slate-950" @click="decisionStore.arm">
            Re-evaluate Arm Readiness
          </button>
          <button class="focus-ring rounded-md bg-red-500 px-3 py-2 text-sm font-semibold text-white" @click="decisionStore.fireDryRun">Fire Request Evaluation</button>
        </div>
        <p class="mt-4 text-sm text-slate-400">Fire Request button only evaluates gates; it does not send serial, motor or servo commands.</p>
      </DashboardCard>
    </div>

    <div class="grid gap-4 xl:grid-cols-2">
      <DashboardCard title="Active Target Summary" subtitle="Vision-derived advisory data">
        <MetricRow label="Class" :value="decisionStore.decision.target_class" />
        <MetricRow label="Team" :value="decisionStore.decision.target_team" />
        <MetricRow label="Range" :value="decisionStore.decision.range_m === null ? 'not_available' : `${decisionStore.decision.range_m} m`" />
        <MetricRow label="Stable frames" :value="`${decisionStore.decision.stable_frames}/${decisionStore.decision.required_stable_frames}`" />
        <MetricRow label="Body detection" :value="decisionStore.decision.selected_body_detection_id" />
        <MetricRow label="Balloon detection" :value="decisionStore.decision.selected_balloon_detection_id" />
      </DashboardCard>

      <DashboardCard title="Range Rules" subtitle="Config defaults">
        <div class="grid gap-2">
          <div v-for="rule in rangeRules" :key="rule" class="rounded-md border border-white/8 bg-black/18 px-3 py-2 font-mono text-sm text-slate-200">{{ rule }}</div>
        </div>
      </DashboardCard>
    </div>

    <DashboardCard title="Safety Gates Matrix" subtitle="Grouped operational gates">
      <div class="grid gap-4">
        <section v-for="group in groupedGates" :key="group.group" class="rounded-md border border-white/8 bg-black/14 p-3">
          <h3 class="mb-3 text-sm font-semibold text-cyan-100">{{ group.group }}</h3>
          <div class="grid gap-2 lg:grid-cols-2 2xl:grid-cols-3">
            <div v-for="gate in group.gates" :key="gate.name" class="rounded-md border border-white/8 bg-black/18 p-3">
              <div class="mb-2 flex items-center justify-between gap-2">
                <div>
                  <span class="text-sm font-semibold text-white">{{ gateLabel(gate.name) }}</span>
                  <p class="mt-0.5 font-mono text-[11px] text-slate-500">{{ gate.name }}</p>
                </div>
                <StatusBadge :label="gate.status" :tone="toneFor(gate.status)" />
              </div>
              <p class="text-xs text-slate-400">{{ readableReasonText(gate.reason) }}</p>
            </div>
          </div>
        </section>
      </div>
    </DashboardCard>

    <div class="grid gap-4 xl:grid-cols-2">
      <DashboardCard title="Blocking Reasons" subtitle="Why FIRE_READY is unavailable">
        <div class="flex flex-wrap gap-2">
          <StatusBadge v-for="reason in blockingReasons" :key="reason" :label="reasonLabel(reason)" tone="bad" />
          <p v-if="blockingReasons.length === 0" class="text-sm text-emerald-200">No blocking reasons.</p>
        </div>
      </DashboardCard>

      <DashboardCard title="Latest Decision Events" subtitle="Safety WebSocket events">
        <div class="grid gap-2">
          <div v-for="(event, index) in decisionStore.events" :key="`${event.type}-${index}-${event.summary}`" class="rounded-md border border-white/8 bg-black/18 p-3 text-sm">
            <span class="font-mono text-cyan-200">{{ event.type }}</span>
            <p class="mt-1 text-xs text-slate-400">{{ readableReasonText(event.summary) }}</p>
          </div>
          <p v-if="decisionStore.events.length === 0" class="text-sm text-slate-400">No safety events yet.</p>
        </div>
      </DashboardCard>
    </div>

    <DashboardCard v-if="decisionStore.latestFireResult" title="Fire Request Evaluation Response" subtitle="Structured dry-run response">
      <div class="mb-4 flex flex-wrap gap-2">
        <StatusBadge :label="decisionStore.latestFireResult.accepted ? 'ACCEPTED_DRY_RUN' : 'REJECTED'" :tone="decisionStore.latestFireResult.accepted ? 'good' : 'bad'" />
        <StatusBadge label="Physical command generated: NO" tone="bad" />
      </div>
      <div class="grid gap-4 xl:grid-cols-2">
        <div>
          <MetricRow label="Decision state" :value="decisionStore.latestFireResult.decision_state" />
          <MetricRow label="Dry-run" :value="decisionStore.latestFireResult.dry_run" />
          <MetricRow label="Timestamp" :value="decisionStore.latestFireResultAt ? new Date(decisionStore.latestFireResultAt).toLocaleTimeString() : 'not recorded'" />
          <MetricRow label="Reason" :value="readableReasonText(decisionStore.latestFireResult.reason)" />
        </div>
        <div>
          <MetricRow label="Gate pass" :value="fireGateSummary.pass" />
          <MetricRow label="Gate fail" :value="fireGateSummary.fail" />
          <MetricRow label="Gate warning" :value="fireGateSummary.warning" />
          <MetricRow label="Not applicable" :value="fireGateSummary.notApplicable" />
        </div>
      </div>
      <div class="mt-4 flex flex-wrap gap-2">
        <StatusBadge v-for="reason in fireBlockingReasons" :key="reason" :label="reasonLabel(reason)" tone="bad" />
        <p v-if="fireBlockingReasons.length === 0" class="text-sm text-emerald-200">No blocking reasons.</p>
      </div>
    </DashboardCard>
  </div>
</template>
