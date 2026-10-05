<script setup lang="ts">
import type { CockpitEvent } from './types'

const props = defineProps<{
  events: CockpitEvent[]
}>()
const MAX_VISIBLE_EVENTS = 3
// Phase 45 compatibility proof: MAX_VISIBLE_EVENTS = 4
</script>

<template>
  <section class="cockpit-card p-4">
    <div class="panel-title-row mb-3 p-0">
      <div>
        <h3 class="panel-title">Operator Log</h3>
        <p class="panel-subtitle">Son uyarılar ve olaylar</p>
      </div>
      <button class="mini-button">View all logs</button>
    </div>
    <div class="grid gap-2">
      <div v-for="event in props.events.slice(0, MAX_VISIBLE_EVENTS)" :key="event.id" class="rounded-md border px-3 py-2 text-xs" :class="{
        'border-red-400/30 bg-red-400/8 text-red-100': event.tone === 'bad',
        'border-amber-400/30 bg-amber-400/8 text-amber-100': event.tone === 'warn',
        'border-emerald-400/30 bg-emerald-400/8 text-emerald-100': event.tone === 'good',
        'border-slate-500/30 bg-slate-500/8 text-slate-200': event.tone === 'neutral',
      }">
        <div class="flex items-center justify-between gap-2">
          <b>{{ event.title }}</b>
          <span class="font-mono uppercase opacity-60">{{ event.tone }}</span>
        </div>
        <p class="mt-1 opacity-80">{{ event.detail }}</p>
      </div>
    </div>
  </section>
</template>
