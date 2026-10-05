<script setup lang="ts">
const props = defineProps<{
  activeModel: string
  classes: string
  confidence: string
  iou: string
  tracker: string
  sourceMode: string
}>()
</script>

<template>
  <section class="cockpit-card p-4">
    <div class="mb-3">
      <h3 class="panel-title">Perception</h3>
      <p class="panel-subtitle">Detector, classes and truth source</p>
    </div>
    <div class="grid grid-cols-2 gap-2 text-sm">
      <div class="metric-tile"><span>Detector</span><b>{{ props.activeModel }}</b></div>
      <div class="metric-tile"><span>Classes</span><b>{{ props.classes }}</b></div>
      <div class="metric-tile"><span>Conf</span><b>{{ props.confidence }}</b></div>
      <div class="metric-tile"><span>Tracker</span><b>{{ props.tracker }}</b></div>
      <div class="metric-tile"><span>Truth Source</span><b>{{ props.sourceMode }}</b></div>
    </div>
  </section>
</template>
