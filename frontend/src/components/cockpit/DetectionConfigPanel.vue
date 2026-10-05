<script setup lang="ts">
import { computed, ref, watch } from 'vue'

const props = defineProps<{
  activeConfidence: number
  yoloEnabled: boolean
  busy: boolean
  lastAppliedAt?: string
  status?: string
}>()

const emit = defineEmits<{
  apply: [confidence: number]
  revert: []
  toggleYolo: []
}>()

const pendingConfidence = ref(props.activeConfidence)

watch(() => props.activeConfidence, (value) => {
  pendingConfidence.value = value
})

const pendingLabel = computed(() => pendingConfidence.value.toFixed(2))
const activeLabel = computed(() => props.activeConfidence.toFixed(2))
const changed = computed(() => Math.abs(pendingConfidence.value - props.activeConfidence) > 0.0005)

function clampConfidence(): void {
  pendingConfidence.value = Math.max(0.01, Math.min(0.95, Number(pendingConfidence.value) || props.activeConfidence))
}

function revertPending(): void {
  pendingConfidence.value = props.activeConfidence
  emit('revert')
}
</script>

<template>
  <section class="operator-card detection-config">
    <div class="operator-card-header">
      <div>
        <h3>Algılama Eşiği</h3>
        <p>YOLO çalışma eşiği · canlı runtime</p>
      </div>
      <button class="pill-button" :class="props.yoloEnabled ? 'on' : 'off'" @click="emit('toggleYolo')">
        {{ props.yoloEnabled ? 'YOLO ON' : 'YOLO OFF' }}
      </button>
    </div>

    <div class="confidence-row">
      <input v-model.number="pendingConfidence" type="range" min="0.01" max="0.95" step="0.01" @change="clampConfidence">
      <input v-model.number="pendingConfidence" class="number-input" type="number" min="0.01" max="0.95" step="0.01" @change="clampConfidence">
    </div>

    <div class="status-row">
      <span>Aktif: <b>{{ activeLabel }}</b></span>
      <span :class="changed ? 'pending' : ''">Yeni: <b>{{ pendingLabel }}</b></span>
      <span>Son uygulama: <b>{{ props.lastAppliedAt ?? 'Henüz uygulanmadı' }}</b></span>
      <span>Durum: <b>{{ props.status ?? 'Preview only' }}</b></span>
    </div>

    <div class="action-row">
      <button class="primary-action" :disabled="props.busy || !changed" @click="emit('apply', pendingConfidence)">
        {{ props.busy ? 'Uygulanıyor...' : 'Uygula' }}
      </button>
      <button class="secondary-action" :disabled="props.busy || !changed" @click="revertPending">Geri al</button>
      <span class="safe-note">Fiziksel komut kapalı</span>
    </div>
  </section>
</template>

<style scoped>
.operator-card { border: 1px solid rgba(34, 211, 238, 0.16); border-radius: 10px; background: rgba(3, 7, 18, 0.72); padding: 12px; color: #e2e8f0; }
.operator-card-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
h3 { margin: 0; font-size: 0.95rem; font-weight: 800; color: #f8fafc; }
p { margin: 3px 0 0; font-size: 0.72rem; color: #94a3b8; }
.pill-button { border: 1px solid rgba(148, 163, 184, 0.22); border-radius: 8px; padding: 7px 10px; font-size: 0.75rem; font-weight: 900; }
.pill-button.on { background: rgba(16, 185, 129, 0.14); color: #a7f3d0; border-color: rgba(16, 185, 129, 0.36); }
.pill-button.off { background: rgba(245, 158, 11, 0.13); color: #fde68a; border-color: rgba(245, 158, 11, 0.36); }
.confidence-row { display: grid; grid-template-columns: 1fr 76px; gap: 10px; margin-top: 14px; align-items: center; }
input[type="range"] { accent-color: #22d3ee; }
.number-input { border: 1px solid rgba(103, 232, 249, 0.25); border-radius: 7px; background: rgba(2, 6, 23, 0.85); color: #f8fafc; padding: 7px; font-weight: 800; }
.status-row, .action-row { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; margin-top: 10px; font-size: 0.76rem; color: #94a3b8; }
.status-row b { color: #cffafe; }
.pending b { color: #fde68a; }
.primary-action, .secondary-action { border-radius: 7px; padding: 7px 10px; font-size: 0.75rem; font-weight: 900; }
.primary-action { border: 1px solid rgba(34, 211, 238, 0.36); background: rgba(34, 211, 238, 0.18); color: #cffafe; }
.secondary-action { border: 1px solid rgba(148, 163, 184, 0.22); background: rgba(15, 23, 42, 0.72); color: #cbd5e1; }
button:disabled { cursor: not-allowed; opacity: 0.45; }
.safe-note { margin-left: auto; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 0.68rem; color: #86efac; }
</style>
