<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{
  targetX: number
  targetY: number
  personSafetyActive: boolean
  xNorm: string
  yNorm: string
  areaRatio: string
  depth: string
  poseSource: string
  offsetLabel: string
}>()
const bearing = computed(() => {
  const x = Number(props.xNorm)
  if (x > 0.62) return 'RIGHT'
  if (x < 0.38) return 'LEFT'
  return 'MID'
})
const elevation = computed(() => {
  const y = Number(props.yNorm)
  if (y < 0.42) return 'UP'
  if (y > 0.58) return 'DOWN'
  return 'MID'
})
</script>

<template>
  <section class="cockpit-card p-4">
    <div class="mb-3">
      <h3 class="panel-title">Engagement Geometry</h3>
      <p class="panel-subtitle">FOV, bearing, offset ve dry-run fire gate</p>
    </div>
    <svg viewBox="0 0 320 180" class="h-[180px] w-full rounded-md border border-white/10 bg-[#050910]">
      <defs>
        <linearGradient id="fovFill" x1="0" x2="1">
          <stop offset="0" stop-color="#22d3ee" stop-opacity="0.22" />
          <stop offset="1" stop-color="#22d3ee" stop-opacity="0.02" />
        </linearGradient>
      </defs>
      <path d="M160 155 L45 20 L275 20 Z" fill="url(#fovFill)" stroke="#22d3ee" stroke-width="2" stroke-dasharray="8 7" />
      <circle cx="160" cy="155" r="16" fill="#0f766e" stroke="#67e8f9" stroke-width="2" />
      <path d="M160 155 L160 32" stroke="#67e8f9" stroke-width="2" />
      <circle :cx="props.targetX" :cy="props.targetY" r="9" fill="#facc15" stroke="#fde68a" stroke-width="3" />
      <circle v-if="props.personSafetyActive" cx="160" cy="82" r="58" fill="rgba(239,68,68,0.13)" stroke="#ef4444" stroke-width="2" stroke-dasharray="9 7" />
      <text x="14" y="166" fill="#94a3b8" font-size="12">TURRET</text>
      <text x="206" y="35" fill="#fde68a" font-size="12">TARGET</text>
    </svg>
    <div class="mt-3 rounded-md border border-cyan-300/18 bg-cyan-300/6 p-3">
      <h4 class="text-xs font-bold uppercase tracking-[0.14em] text-cyan-100">2D Detection → Tactical FOV Mapping</h4>
      <span class="sr-only">2D Detection → 3D Digital Twin Mapping · bbox center · bbox area · relative depth · pose source · read-only visualization</span>
      <div class="mt-2 grid grid-cols-2 gap-2 text-[11px]">
        <div class="metric-tile"><span>Target bearing</span><b>{{ bearing }}</b></div>
        <div class="metric-tile"><span>Elevation</span><b>{{ elevation }}</b></div>
        <div class="metric-tile"><span>Relative depth</span><b>{{ props.depth }}</b></div>
        <div class="metric-tile"><span>Offset comp.</span><b>{{ props.offsetLabel }} active</b></div>
        <div class="metric-tile"><span>Fire gate</span><b>{{ props.personSafetyActive ? 'PERSON BLOCKED' : 'BLOCKED / NO TX' }}</b></div>
        <div class="metric-tile"><span>Projection</span><b>x={{ props.xNorm }} / y={{ props.yNorm }}</b></div>
      </div>
    </div>
  </section>
</template>
