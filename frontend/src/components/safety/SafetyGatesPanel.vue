<script setup lang="ts">
import { computed } from 'vue'
import type { SafetyState, SystemState } from '../../types/system'
import { dedupe, reasonLabel } from '../../utils/safetyLabels'
import StatusBadge from '../shared/StatusBadge.vue'

const props = defineProps<{
  system: SystemState
  safety: SafetyState
}>()

const blockingReasons = computed(() => dedupe(props.safety.blocking_reasons))

const gateLabels: Array<[keyof SafetyState['gates'], string]> = [
  ['armed', 'Armed'],
  ['estop_released', 'E-stop released'],
  ['pico_heartbeat', 'Pico heartbeat'],
  ['track_stable', 'Track stable'],
  ['target_enemy', 'Target enemy'],
  ['balloon_detected', 'Balloon detected'],
  ['range_valid', 'Range valid'],
  ['aim_point_valid', 'Aim point valid'],
  ['zone_valid', 'Zone valid'],
  ['operator_or_auto_permission', 'Permission'],
  ['dry_run', 'Dry run'],
  ['hardware_enabled', 'Hardware enabled'],
  ['motion_soft_limits', 'Motion soft limits'],
  ['motion_estop', 'Motion E-stop'],
  ['motion_fault_clear', 'Motion fault clear'],
  ['motion_driver', 'Motion driver'],
  ['motion_dry_run', 'Motion dry run'],
]
</script>

<template>
  <section class="rounded-md border border-red-400/25 bg-[#15171a] p-4">
    <div class="mb-4 flex flex-wrap items-center justify-between gap-2">
      <div>
        <h3 class="text-base font-semibold text-white">Safety Gates</h3>
        <p class="mt-1 text-xs text-slate-400">{{ safety.reason }}</p>
      </div>
      <div class="flex flex-wrap gap-2">
        <StatusBadge :label="system.armed ? 'ARMED' : 'DISARMED'" :tone="system.armed ? 'warn' : 'bad'" />
        <StatusBadge :label="safety.decision" :tone="safety.decision === 'FIRE_READY' ? 'good' : 'bad'" />
      </div>
    </div>

    <div class="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
      <div
        v-for="[key, label] in gateLabels"
        :key="key"
        class="flex min-h-11 items-center justify-between rounded-md border border-white/8 bg-black/18 px-3 py-2"
      >
        <span class="text-sm text-slate-300">{{ label }}</span>
        <StatusBadge
          :label="String(safety.gates[key])"
          :tone="safety.gates[key] ? (key === 'hardware_enabled' ? 'warn' : 'good') : 'bad'"
        />
      </div>
    </div>

    <div class="mt-4 rounded-md border border-white/10 bg-black/20 p-3">
      <p class="text-xs font-semibold uppercase tracking-[0.18em] text-slate-500">Blocking reasons</p>
      <div class="mt-3 flex flex-wrap gap-2">
        <StatusBadge
          v-for="reason in blockingReasons"
          :key="reason"
          :label="reasonLabel(reason)"
          tone="bad"
        />
      </div>
    </div>
  </section>
</template>
