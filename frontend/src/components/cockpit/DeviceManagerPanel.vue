<script setup lang="ts">
import StatusBadge from '../shared/StatusBadge.vue'
import type { CockpitBadge } from './types'

const props = defineProps<{
  items: CockpitBadge[]
}>()
const emit = defineEmits<{ refresh: [] }>()
</script>

<template>
  <section class="cockpit-card p-3">
    <div class="panel-title-row mb-2 p-0">
      <div>
        <h3 class="panel-title">Sistem Sağlığı</h3>
        <p class="panel-subtitle">Kamera, mikrodenetleyici, dijital ikiz ve komut yolu</p>
      </div>
      <button class="mini-button" @click="emit('refresh')">Yenile</button>
    </div>
    <div class="grid gap-1">
      <div v-for="item in props.items" :key="item.label" class="operator-row compact-row">
        <span class="truncate text-xs text-slate-300">{{ item.label }}</span>
        <StatusBadge :label="item.tone === 'good' ? 'OK' : item.tone === 'warn' ? 'CHECK' : item.tone.toUpperCase()" :tone="item.tone" />
      </div>
    </div>
  </section>
</template>
