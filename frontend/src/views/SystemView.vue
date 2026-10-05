<script setup lang="ts">
import DashboardCard from '../components/dashboard/DashboardCard.vue'
import MetricRow from '../components/dashboard/MetricRow.vue'
import { useSystemStore } from '../stores/systemStore'

const store = useSystemStore()
</script>

<template>
  <DashboardCard title="System" subtitle="Backend system state">
    <MetricRow label="Mode" :value="store.systemState.mode" />
    <MetricRow label="Armed" :value="store.systemState.armed" />
    <MetricRow label="Fire policy" :value="store.systemState.fire_policy" />
    <MetricRow label="dry_run" :value="store.systemState.dry_run" />
    <MetricRow label="hardware_enabled" :value="store.systemState.hardware_enabled" />
    <MetricRow label="Ready" :value="store.systemState.ready" />
    <div class="mt-4 rounded-md border border-white/10 bg-black/20 p-3">
      <p class="text-sm text-slate-300">{{ store.systemState.reason }}</p>
    </div>
  </DashboardCard>
</template>
