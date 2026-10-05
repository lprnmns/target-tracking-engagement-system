<script setup lang="ts">
defineProps<{
  label: string
  tone?: 'good' | 'warn' | 'bad' | 'neutral'
}>()
</script>

<template>
  <span
    class="inline-flex min-h-7 items-center rounded-md border px-2.5 py-1 text-xs font-semibold uppercase tracking-wide"
    :class="{
      'border-emerald-400/35 bg-emerald-400/10 text-emerald-200': tone === 'good',
      'border-amber-400/35 bg-amber-400/10 text-amber-200': tone === 'warn',
      'border-red-400/35 bg-red-400/10 text-red-200': tone === 'bad',
      'border-slate-500/35 bg-slate-500/10 text-slate-200': !tone || tone === 'neutral',
    }"
  >
    {{ label }}
  </span>
</template>
