<script setup lang="ts">
import { Play, Square, RotateCcw, FastForward, Radar } from '@lucide/vue'
import { computed } from 'vue'

const props = defineProps<{
  stage: 'STAGE_1' | 'STAGE_2' | 'STAGE_3'
  controlMode?: string
  firePermission?: string
  patrolState?: string
  patrolEnabled?: boolean
  activePath?: number
  path1Deg?: number
  path2Deg?: number
  path3Deg?: number
  currentRound?: number
  busy?: boolean
}>()

const emit = defineEmits<{
  startStage2: []
  stopStage2: []
  startStage3Round: []
  nextStage3Round: []
  resetStage3Rounds: []
  capturePathAngle: [path: number]
  openRadarModal: []
}>()

const isPatrolling = computed(() => Boolean(props.patrolEnabled))
const round = computed(() => props.currentRound || 1)
</script>

<template>
  <div class="competition-stage-bar" :class="`stage-${stage.toLowerCase()}`">
    <!-- AŞAMA 1: MANUEL OPERATÖR -->
    <div v-if="stage === 'STAGE_1'" class="stage-content stage-1-layout">
      <div class="stage-info">
        <span class="stage-badge stage-badge-blue">AŞAMA 1</span>
        <span class="stage-title">MANUEL NİŞAN & BAĞIMSIZ HEDEF TAKİBİ</span>
        <span class="stage-sub">Hava araçları ve balonlar bağımsızdır. Operatör serbest hedefleme yapar.</span>
      </div>
      <div class="stage-status-pills">
        <span class="pill pill-cyan">MANUEL MOD</span>
        <span class="pill pill-slate">KURAL 1 & 2 DEVRE DIŞI</span>
      </div>
    </div>

    <!-- AŞAMA 2: OTONOM BALON AVLAMA -->
    <div v-if="stage === 'STAGE_2'" class="stage-content stage-2-layout">
      <div class="stage-info">
        <span class="stage-badge stage-badge-emerald">AŞAMA 2</span>
        <span class="stage-title">OTONOM BALON AVLAMA</span>
      </div>

      <!-- Radar Süpürme Durumu -->
      <div class="radar-sweep-bar">
        <span class="sweep-pill" :class="{ sweeping: isPatrolling }">
          <Radar :size="14" />
          {{ isPatrolling ? 'RADAR: 135° ± 15° SÜPÜRÜLÜYOR (9.4°/s)' : 'RADAR: 135° ± 15° SİNÜSOİDAL SÜPÜRME' }}
        </span>
      </div>

      <!-- Görev Butonları -->
      <div class="stage-actions">
        <button
          v-if="!isPatrolling"
          type="button"
          class="btn-action btn-start"
          :disabled="busy"
          @click="emit('startStage2')"
        >
          <Play :size="15" /> GÖREVİ BAŞLAT
        </button>
        <button
          v-else
          type="button"
          class="btn-action btn-stop"
          :disabled="busy"
          @click="emit('stopStage2')"
        >
          <Square :size="15" /> GÖREVİ DURDUR
        </button>
      </div>
    </div>

    <!-- AŞAMA 3: BİRLİKTE TAKİP (15m, 3 KOL, 8 TUR) -->
    <div v-if="stage === 'STAGE_3'" class="stage-content stage-3-layout">
      <div class="stage-info">
        <span class="stage-badge stage-badge-amber">AŞAMA 3</span>
        <span class="stage-title">BİRLİKTE TAKİP (3 KOL · 8 TUR)</span>
      </div>

      <!-- 8 Tur Göstergesi -->
      <div class="rounds-tracker" role="group" aria-label="8 Tur Göstergesi">
        <span class="round-header">TUR {{ round }} / 8:</span>
        <div class="round-dots">
          <span
            v-for="r in 8"
            :key="r"
            class="round-dot"
            :class="{ active: r === round, completed: r < round }"
          >
            {{ r }}
          </span>
        </div>
      </div>

      <!-- Radar Süpürme Durumu -->
      <div class="radar-sweep-bar">
        <span class="sweep-pill" :class="{ sweeping: isPatrolling }">
          <Radar :size="14" />
          {{ isPatrolling ? 'RADAR: 135° ± 15° SÜPÜRÜLÜYOR (9.4°/s)' : 'RADAR: 135° ± 15° SİNÜSOİDAL SÜPÜRME' }}
        </span>
      </div>

      <!-- Tur Eylemleri -->
      <div class="stage-actions">
        <button
          type="button"
          class="btn-action btn-start"
          :disabled="busy"
          @click="emit('startStage3Round')"
        >
          <Play :size="15" /> TURU BAŞLAT
        </button>
        <button
          type="button"
          class="btn-action btn-next"
          :disabled="busy || round >= 8"
          @click="emit('nextStage3Round')"
        >
          <FastForward :size="15" /> SONRAKİ TUR
        </button>
        <button
          type="button"
          class="btn-action btn-reset"
          :disabled="busy"
          title="Turları sıfırla"
          @click="emit('resetStage3Rounds')"
        >
          <RotateCcw :size="14" />
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.competition-stage-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
  padding: 6px 14px;
  background: rgba(8, 14, 22, 0.94);
  border-bottom: 1px solid rgba(56, 189, 248, 0.18);
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, monospace;
}

.stage-content {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
  gap: 12px;
}

.stage-info {
  display: flex;
  align-items: center;
  gap: 8px;
}

.stage-badge {
  font-size: 11px;
  font-weight: 800;
  padding: 2px 7px;
  border-radius: 4px;
  letter-spacing: 0.5px;
}
.stage-badge-blue { background: rgba(14, 165, 233, 0.2); color: #38bdf8; border: 1px solid rgba(56, 189, 248, 0.4); }
.stage-badge-emerald { background: rgba(16, 185, 129, 0.2); color: #34d399; border: 1px solid rgba(52, 211, 153, 0.4); }
.stage-badge-amber { background: rgba(245, 158, 11, 0.2); color: #fbbf24; border: 1px solid rgba(251, 191, 36, 0.4); }

.stage-title {
  font-size: 12px;
  font-weight: 700;
  color: #e2e8f0;
  letter-spacing: 0.3px;
}
.stage-sub {
  font-size: 11px;
  color: #94a3b8;
}

.stage-status-pills {
  display: flex;
  gap: 6px;
}
.pill {
  font-size: 10px;
  font-weight: 700;
  padding: 2px 6px;
  border-radius: 3px;
}
.pill-cyan { background: rgba(6, 182, 212, 0.15); color: #22d3ee; border: 1px solid rgba(34, 211, 238, 0.3); }
.pill-slate { background: rgba(100, 116, 139, 0.15); color: #94a3b8; border: 1px solid rgba(148, 163, 184, 0.2); }

.radar-paths {
  display: flex;
  align-items: center;
  gap: 6px;
}
.radar-label {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 11px;
  font-weight: 700;
  color: #64748b;
}
.path-pill {
  font-size: 10px;
  font-weight: 600;
  padding: 3px 8px;
  border-radius: 4px;
  background: rgba(15, 23, 42, 0.8);
  color: #94a3b8;
  border: 1px solid rgba(51, 65, 85, 0.8);
  cursor: pointer;
  transition: all 0.15s ease;
}
.path-pill:hover {
  background: rgba(30, 41, 59, 0.9);
  color: #cbd5e1;
}
.path-pill.active {
  background: rgba(14, 165, 233, 0.25);
  color: #38bdf8;
  border-color: #0284c7;
  font-weight: 700;
}
.radar-sweep-bar {
  display: flex;
  align-items: center;
}
.sweep-pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 11px;
  font-weight: 700;
  padding: 4px 10px;
  border-radius: 6px;
  background: rgba(15, 23, 42, 0.85);
  color: #38bdf8;
  border: 1px solid rgba(56, 189, 248, 0.3);
  letter-spacing: 0.3px;
  transition: all 0.2s ease;
}
.sweep-pill.sweeping {
  background: rgba(16, 185, 129, 0.18);
  border-color: rgba(52, 211, 153, 0.6);
  color: #34d399;
  box-shadow: 0 0 12px rgba(16, 185, 129, 0.25);
  animation: sweep-glow 1.5s infinite alternate ease-in-out;
}
@keyframes sweep-glow {
  0% { box-shadow: 0 0 6px rgba(16, 185, 129, 0.2); }
  100% { box-shadow: 0 0 14px rgba(16, 185, 129, 0.45); }
}

.rounds-tracker {
  display: flex;
  align-items: center;
  gap: 8px;
}
.round-header {
  font-size: 11px;
  font-weight: 700;
  color: #fbbf24;
}
.round-dots {
  display: flex;
  gap: 4px;
}
.round-dot {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 19px;
  height: 19px;
  border-radius: 3px;
  font-size: 10px;
  font-weight: 700;
  background: rgba(30, 41, 59, 0.8);
  color: #64748b;
  border: 1px solid rgba(51, 65, 85, 0.6);
}
.round-dot.active {
  background: #f59e0b;
  color: #0f172a;
  border-color: #fbbf24;
  box-shadow: 0 0 6px rgba(245, 158, 11, 0.6);
}
.round-dot.completed {
  background: rgba(16, 185, 129, 0.2);
  color: #34d399;
  border-color: rgba(52, 211, 153, 0.4);
}

.stage-actions {
  display: flex;
  align-items: center;
  gap: 6px;
}
.btn-action {
  display: flex;
  align-items: center;
  gap: 5px;
  padding: 4px 12px;
  font-size: 11px;
  font-weight: 700;
  border-radius: 4px;
  cursor: pointer;
  transition: all 0.15s ease;
  border: 1px solid transparent;
}
.btn-start {
  background: #059669;
  color: #ffffff;
  border-color: #10b981;
}
.btn-start:hover:not(:disabled) {
  background: #10b981;
  box-shadow: 0 0 8px rgba(16, 185, 129, 0.5);
}
.btn-stop {
  background: #dc2626;
  color: #ffffff;
  border-color: #ef4444;
}
.btn-stop:hover:not(:disabled) {
  background: #ef4444;
  box-shadow: 0 0 8px rgba(239, 68, 68, 0.5);
}
.btn-next {
  background: rgba(59, 130, 246, 0.25);
  color: #60a5fa;
  border-color: #2563eb;
}
.btn-next:hover:not(:disabled) {
  background: rgba(59, 130, 246, 0.4);
}
.btn-reset {
  background: rgba(51, 65, 85, 0.5);
  color: #cbd5e1;
  border-color: #475569;
  padding: 4px 8px;
}
.btn-reset:hover:not(:disabled) {
  background: rgba(71, 85, 105, 0.7);
}
.btn-action:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}
.btn-config-radar {
  display: flex;
  align-items: center;
  gap: 5px;
  padding: 4px 10px;
  font-size: 11px;
  font-weight: 700;
  border-radius: 4px;
  background: rgba(14, 165, 233, 0.15);
  border: 1px solid rgba(56, 189, 248, 0.4);
  color: #38bdf8;
  cursor: pointer;
  transition: all 0.15s ease;
}
.btn-config-radar:hover {
  background: rgba(14, 165, 233, 0.3);
  color: #e0f2fe;
}
</style>
