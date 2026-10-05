<script setup lang="ts">
import StatusBadge from '../shared/StatusBadge.vue'
import type { CockpitEvent } from './types'
import type { TruthTone } from '../../composables/useRuntimeTruth'

const props = defineProps<{
  selectedTarget: string
  targetClass: string
  confidence: string
  distance: string
  direction: string
  trackingState: string
  fireStatus: string
  fireTone: TruthTone
  events: CockpitEvent[]
}>()
</script>

<template>
  <section class="cockpit-card p-4">
    <div class="panel-title-row mb-3 p-0">
      <div>
        <h3 class="panel-title">Target & Engagement</h3>
        <p class="panel-subtitle">Bearing, depth and blocked fire gate</p>
      </div>
      <StatusBadge :label="props.fireStatus" :tone="props.fireTone" />
    </div>
    <div class="grid grid-cols-2 gap-2 text-sm">
      <div class="metric-tile"><span>Target</span><b>{{ props.selectedTarget }}</b></div>
      <div class="metric-tile"><span>Class</span><b>{{ props.targetClass }}</b></div>
      <div class="metric-tile"><span>Conf</span><b>{{ props.confidence }}</b></div>
      <div class="metric-tile"><span>Depth</span><b>{{ props.distance }}</b></div>
      <div class="metric-tile"><span>Direction</span><b>{{ props.direction }}</b></div>
      <div class="metric-tile"><span>Fire Gate</span><b>{{ props.fireStatus }}</b></div>
    </div>
    <p class="mt-3 rounded-md border border-cyan-300/18 bg-cyan-300/6 px-3 py-2 text-xs text-cyan-100">
      Tracking: {{ props.trackingState }} · visualization only
    </p>
  </section>
</template>
