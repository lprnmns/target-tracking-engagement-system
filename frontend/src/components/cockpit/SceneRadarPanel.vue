<script setup lang="ts">
import type { BalloonDetection } from '../../types/vision'

const props = defineProps<{
  targets: BalloonDetection[]
  selectedTargetId: number | null
  frameWidth: number
  frameHeight: number
}>()

const emit = defineEmits<{
  selectTarget: [target: { id: number, center_x: number, center_y: number }]
}>()

function areaRatio(target: BalloonDetection): number {
  return (target.bbox.w * target.bbox.h) / Math.max(1, props.frameWidth * props.frameHeight)
}

function depthRatio(target: BalloonDetection): number {
  const area = areaRatio(target)
  const nearArea = 0.031
  const farArea = 0.00045
  const size = Math.sqrt(Math.max(farArea, Math.min(nearArea, area)))
  const closeness = (size - Math.sqrt(farArea)) / (Math.sqrt(nearArea) - Math.sqrt(farArea))
  return 1 - Math.max(0, Math.min(1, closeness))
}

function radarPoint(target: BalloonDetection): { x: number, y: number, depth: string, bearing: string } {
  const xNorm = target.center_x / Math.max(1, props.frameWidth)
  const depth = depthRatio(target)
  const x = 100 + (xNorm - 0.5) * 132 * (0.45 + depth * 0.55)
  const y = 170 - depth * 132
  return {
    x,
    y,
    depth: depth < 0.34 ? 'near' : depth < 0.67 ? 'mid' : 'far',
    bearing: xNorm > 0.62 ? 'RIGHT' : xNorm < 0.38 ? 'LEFT' : 'MID',
  }
}

</script>

<template>
  <section class="operator-card radar-card">
    <div class="operator-card-header">
      <div>
        <h3>Sahne Planı</h3>
        <p>Radar-like top-down detection map</p>
      </div>
      <span>{{ props.targets.length }} target</span>
    </div>

    <svg viewBox="0 0 200 190" class="radar">
      <path d="M100 176 L28 22 L172 22 Z" fill="rgba(34,211,238,0.06)" stroke="#22d3ee" stroke-width="1.5" stroke-dasharray="6 6" />
      <line x1="100" y1="176" x2="100" y2="22" stroke="#67e8f9" stroke-width="1" opacity="0.65" />
      <path d="M64 108 A52 28 0 0 0 136 108" fill="none" stroke="#334155" stroke-width="1" />
      <path d="M45 68 A80 44 0 0 0 155 68" fill="none" stroke="#334155" stroke-width="1" />
      <circle cx="100" cy="176" r="10" fill="#020617" stroke="#a7f3d0" stroke-width="1.6" />
      <rect x="88" y="164" width="24" height="7" rx="2" fill="#0f172a" stroke="#facc15" stroke-width="1" />
      <g v-for="target in props.targets" :key="target.id" class="radar-target" @click="emit('selectTarget', target)">
        <circle
          :cx="radarPoint(target).x"
          :cy="radarPoint(target).y"
          :r="target.id === props.selectedTargetId ? 7 : 5"
          :fill="target.id === props.selectedTargetId ? '#facc15' : '#ef4444'"
          :stroke="target.id === props.selectedTargetId ? '#fde68a' : '#fecaca'"
          stroke-width="2"
        />
      </g>
    </svg>

    <div class="target-list">
      <button
        v-for="target in props.targets.slice(0, 5)"
        :key="target.id"
        :class="{ selected: target.id === props.selectedTargetId }"
        @click="emit('selectTarget', target)"
      >
        <b>Hedef #{{ target.id }}</b>
        <span>{{ Math.round(target.confidence * 100) }}% · {{ radarPoint(target).bearing }} · {{ radarPoint(target).depth }}</span>
      </button>
      <p v-if="!props.targets.length">Canlı hedef yok.</p>
    </div>
  </section>
</template>

<style scoped>
.operator-card { border: 1px solid rgba(34, 211, 238, 0.16); border-radius: 10px; background: rgba(3, 7, 18, 0.72); padding: 12px; color: #e2e8f0; }
.operator-card-header { display: flex; justify-content: space-between; gap: 12px; align-items: flex-start; }
h3 { margin: 0; font-size: 0.95rem; font-weight: 800; color: #f8fafc; }
p { margin: 3px 0 0; font-size: 0.72rem; color: #94a3b8; }
.operator-card-header span { color: #67e8f9; font-size: 0.72rem; font-weight: 900; }
.radar { width: 100%; height: 220px; margin-top: 8px; border-radius: 8px; background: radial-gradient(circle at 50% 100%, rgba(34, 211, 238, 0.12), rgba(2, 6, 23, 0.92)); }
.radar-target { cursor: pointer; }
.target-list { display: grid; gap: 6px; margin-top: 8px; }
.target-list button { display: flex; justify-content: space-between; gap: 8px; border: 1px solid rgba(148, 163, 184, 0.14); border-radius: 7px; background: rgba(15, 23, 42, 0.66); padding: 7px 8px; color: #cbd5e1; font-size: 0.74rem; }
.target-list button.selected { border-color: rgba(250, 204, 21, 0.42); color: #fde68a; }
.target-list p { text-align: center; }
</style>
