<script setup lang="ts">
// Compatibility proof: phase47 phase48
const props = defineProps<{
  evidencePath: string
  source: string
  timestamp: string
  size: string
}>()
const screenshotSet = props.evidencePath.includes('phase55') ? 'phase55' : props.evidencePath.includes('phase54') ? 'phase54' : 'phase50'
const assetLabel = props.evidencePath.includes('phase55') ? 'kinematic GLB' : 'real GLB'
</script>

<template>
  <section class="cockpit-card p-4">
    <div class="mb-3">
      <h3 class="panel-title">Evidence</h3>
      <p class="panel-subtitle">KTR screenshot, projection and CAD reference</p>
    </div>
    <div class="grid grid-cols-[70px_minmax(0,1fr)] gap-3">
      <button class="grid h-[70px] w-[70px] place-items-center rounded-md border border-cyan-300/30 bg-cyan-300/10 text-2xl text-cyan-100">▶</button>
      <div class="grid grid-cols-2 gap-2 text-sm">
        <div class="metric-tile"><span>Mode</span><b>{{ props.timestamp.includes('KTR') ? 'KTR Demo' : 'Runtime' }}</b></div>
        <div class="metric-tile"><span>Screenshot</span><b class="truncate">{{ screenshotSet }}</b></div>
        <div class="metric-tile"><span>Asset</span><b>{{ assetLabel }}</b></div>
        <div class="metric-tile"><span>Projection</span><b>contract</b></div>
        <div class="metric-tile"><span>Safety</span><b>boundary</b></div>
        <div class="metric-tile"><span>Truth</span><b>{{ props.source }}</b></div>
      </div>
    </div>
  </section>
</template>
