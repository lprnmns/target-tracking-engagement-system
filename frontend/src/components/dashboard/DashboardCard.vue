<script setup lang="ts">
defineProps<{
  title: string
  subtitle?: string
}>()
</script>

<template>
  <section class="rounded-md border border-white/10 bg-[#14181d] p-4 shadow-sm shadow-black/20">
    <div class="mb-4">
      <h3 class="text-base font-semibold text-white">{{ title }}</h3>
      <p v-if="subtitle" class="mt-1 text-xs text-slate-400">{{ subtitle }}</p>
    </div>
    <slot />
  </section>
</template>
