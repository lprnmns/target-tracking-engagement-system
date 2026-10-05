<script setup lang="ts">
import type { PinValidationResult } from '../../types/pico'
import StatusBadge from '../shared/StatusBadge.vue'

defineProps<{
  result: PinValidationResult | null
}>()

function toneFor(level: string): 'good' | 'warn' | 'bad' | 'neutral' {
  if (level === 'INFO') return 'good'
  if (level === 'WARNING') return 'warn'
  return 'bad'
}
</script>

<template>
  <section class="rounded-md border border-white/10 bg-[#14181d] p-4">
    <div class="mb-4 flex flex-wrap items-center justify-between gap-2">
      <div>
        <h3 class="text-base font-semibold text-white">Pin Validation</h3>
        <p class="mt-1 text-xs text-slate-400">Backend validation result for preview profile</p>
      </div>
      <StatusBadge
        v-if="result"
        :label="result.valid ? 'VALID' : 'INVALID'"
        :tone="result.valid ? 'good' : 'bad'"
      />
    </div>

    <div v-if="!result" class="text-sm text-slate-400">No validation has been run yet.</div>
    <div v-else class="grid gap-2">
      <div
        v-for="issue in result.issues"
        :key="`${issue.code}-${issue.pin_name ?? issue.function ?? 'global'}`"
        class="rounded-md border border-white/8 bg-black/18 p-3"
      >
        <div class="mb-2 flex flex-wrap items-center gap-2">
          <StatusBadge :label="issue.level" :tone="toneFor(issue.level)" />
          <span class="font-mono text-xs text-cyan-200">{{ issue.code }}</span>
          <span v-if="issue.pin_name" class="font-mono text-xs text-slate-400">{{ issue.pin_name }}</span>
        </div>
        <p class="text-sm text-slate-200">{{ issue.message }}</p>
      </div>
    </div>
  </section>
</template>
