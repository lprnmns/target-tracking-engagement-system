<script setup lang="ts">
defineProps<{
  selectedTarget: string
  targetClass: string
  confidence: string
  bearing: string
  elevation: string
  depth: string
  axisError: string
  state: string
}>()
</script>

<template>
  <section class="operator-card">
    <div class="operator-card-header">
      <div>
        <h3>Engagement Preview</h3>
        <p>Visualization only · Fire Gate: BLOCKED / NO TX</p>
      </div>
      <span>NO TX</span>
    </div>
    <div class="summary-grid">
      <div><span>Selected target</span><b>{{ selectedTarget }}</b></div>
      <div><span>Class</span><b>{{ targetClass }}</b></div>
      <div><span>Confidence</span><b>{{ confidence }}</b></div>
      <div><span>Bearing</span><b>{{ bearing }}</b></div>
      <div><span>Elevation</span><b>{{ elevation }}</b></div>
      <div><span>Depth</span><b>{{ depth }}</b></div>
      <div><span>Axis error</span><b>{{ axisError }}</b></div>
      <div><span>State</span><b>{{ state }}</b></div>
    </div>
  </section>
</template>

<style scoped>
.operator-card { border: 1px solid rgba(34, 211, 238, 0.16); border-radius: 10px; background: rgba(3, 7, 18, 0.72); padding: 12px; color: #e2e8f0; }
.operator-card-header { display: flex; justify-content: space-between; gap: 12px; align-items: flex-start; margin-bottom: 10px; }
h3 { margin: 0; font-size: 0.95rem; font-weight: 800; color: #f8fafc; }
p { margin: 3px 0 0; font-size: 0.72rem; color: #94a3b8; }
.operator-card-header span { border: 1px solid rgba(16,185,129,.32); border-radius: 999px; padding: 5px 8px; color: #a7f3d0; font-size: .7rem; font-weight: 900; }
.summary-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }
.summary-grid div { border: 1px solid rgba(148, 163, 184, 0.12); border-radius: 7px; background: rgba(15, 23, 42, 0.58); padding: 7px 8px; }
span { display: block; color: #64748b; font-size: 0.64rem; font-weight: 900; letter-spacing: 0.1em; text-transform: uppercase; }
b { display: block; margin-top: 4px; color: #e2e8f0; font-size: 0.82rem; }
</style>
