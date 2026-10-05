<script setup lang="ts">
defineProps<{
  label: string
  value: string | number | boolean | null
}>()
</script>

<template>
  <div class="flex items-center justify-between gap-4 border-t border-white/8 py-2 text-sm first:border-t-0">
    <span class="text-slate-400">{{ label }}</span>
    <span class="text-right font-mono text-slate-100">{{ value }}</span>
  </div>
</template>
