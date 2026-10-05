<script setup lang="ts">
const props = defineProps<{
  open: boolean
  assetLabel: string
  materialLabel: string
  poseLabel: string
}>()

const emit = defineEmits<{
  toggle: []
}>()
</script>

<template>
  <section class="debug-drawer" :class="{ open: props.open }">
    <button class="debug-toggle" @click="emit('toggle')">
      Developer / Debug {{ props.open ? '▲' : '▼' }}
    </button>
    <div v-if="props.open" class="debug-body">
      <div><span>Asset</span><b>{{ props.assetLabel }}</b></div>
      <div><span>Material</span><b>{{ props.materialLabel }}</b></div>
      <div><span>Pose</span><b>{{ props.poseLabel }}</b></div>
      <div><span>Hidden from main</span><b>Asset selector · Edges · Wireframe · X-Ray · Exploded · Geometry details · Calibration details</b></div>
      <p>Debug işlevleri ana operatör ekranını kaplamaz. Ayrıntılı CAD/asset inceleme için /cockpit/world ve CAD Debug modu kullanılabilir.</p>
    </div>
  </section>
</template>

<style scoped>
.debug-drawer { border: 1px solid rgba(148, 163, 184, 0.16); border-radius: 10px; background: rgba(3, 7, 18, 0.58); overflow: hidden; }
.debug-toggle { width: 100%; border: 0; background: rgba(15, 23, 42, 0.76); color: #cbd5e1; padding: 10px 12px; text-align: left; font-size: 0.78rem; font-weight: 900; }
.debug-body { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 8px; padding: 10px; }
.debug-body div { border: 1px solid rgba(148, 163, 184, 0.12); border-radius: 7px; padding: 8px; }
span { display: block; color: #64748b; font-size: 0.64rem; font-weight: 900; letter-spacing: 0.1em; text-transform: uppercase; }
b { display: block; margin-top: 4px; color: #e2e8f0; font-size: 0.75rem; }
p { grid-column: 1 / -1; margin: 0; color: #94a3b8; font-size: 0.76rem; }
@media (max-width: 1200px) { .debug-body { grid-template-columns: 1fr; } }
</style>
