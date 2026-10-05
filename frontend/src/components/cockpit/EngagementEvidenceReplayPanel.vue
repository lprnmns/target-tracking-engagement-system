<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { engagementEvidenceMediaUrl } from '../../api/engagementEvidence'
import type { EngagementEvidenceStatus, EngagementEvidenceSummary } from '../../types/engagementEvidence'
import type { EngagementReplayControl } from '../../types/engagementReplay'

const props = defineProps<{
  status: EngagementEvidenceStatus | null
  records: EngagementEvidenceSummary[]
}>()
const emit = defineEmits<{
  loadTwinReplay: [engagementId: string]
  replayControl: [control: EngagementReplayControl]
}>()

const selectedId = ref<string | null>(null)
const video = ref<HTMLVideoElement | null>(null)
const replayRate = ref(1)
const allRecords = computed(() => {
  const byId = new Map<string, EngagementEvidenceSummary>()
  if (props.status?.active) byId.set(props.status.active.engagement_id, props.status.active)
  for (const item of props.status?.recent ?? []) byId.set(item.engagement_id, item)
  for (const item of props.records) byId.set(item.engagement_id, item)
  return [...byId.values()].sort((a, b) => b.updated_at - a.updated_at)
})
const selected = computed(() => allRecords.value.find((item) => item.engagement_id === selectedId.value) ?? allRecords.value[0] ?? null)
const reviewUrl = computed(() => selected.value ? engagementEvidenceMediaUrl(selected.value.engagement_id, 'camera_review.mp4') : '')
const outcomeTone = (outcome: EngagementEvidenceSummary['outcome']) => outcome === 'HIT_CONFIRMED' ? 'good' : outcome === 'MISS_CONFIRMED' ? 'warn' : outcome === 'UNCONFIRMED' ? 'bad' : 'neutral'

function publishReplayControl(): void {
  if (!selected.value || !video.value) return
  emit('replayControl', {
    engagementId: selected.value.engagement_id,
    positionMs: Math.round(video.value.currentTime * 1000),
    playing: !video.value.paused && !video.value.ended,
    playbackRate: video.value.playbackRate,
  })
}

function setReplayRate(): void {
  if (!video.value) return
  video.value.playbackRate = replayRate.value
  publishReplayControl()
}

watch(allRecords, (items) => {
  if (!selectedId.value && items[0]) selectedId.value = items[0].engagement_id
})
</script>

<template>
  <section class="cockpit-card evidence-replay-panel p-4">
    <div class="mb-3 flex items-start justify-between gap-3">
      <div>
        <h3 class="panel-title">Atış Olay Kayıtları</h3>
        <p class="panel-subtitle">LOCK → ACK → görsel sonuç · replay salt-okunur</p>
      </div>
      <span class="rounded border border-cyan-300/25 bg-cyan-300/10 px-2 py-1 font-mono text-[10px] text-cyan-100">{{ allRecords.length }} kayıt</span>
    </div>
    <div v-if="!allRecords.length" class="empty">Henüz LOCK ile başlayan olay kaydı yok.</div>
    <div v-else class="evidence-layout">
      <div class="record-list">
        <button
          v-for="item in allRecords"
          :key="item.engagement_id"
          class="record"
          :class="{ selected: selected?.engagement_id === item.engagement_id }"
          @click="selectedId = item.engagement_id"
        >
          <b>{{ item.target_class ?? 'hedef' }} · B{{ item.balloon_track_id ?? '?' }}</b>
          <span>{{ new Date(item.created_at * 1000).toLocaleString('tr-TR') }}</span>
          <em :class="`tone-${outcomeTone(item.outcome)}`">{{ item.outcome }}</em>
        </button>
      </div>
      <div v-if="selected" class="review">
        <div class="review-meta">
          <b>{{ selected.mission_stage.toUpperCase() }} · {{ selected.command_profile }}</b>
          <span>{{ selected.camera_capture_status }} · {{ selected.association_state }}</span>
        </div>
        <video ref="video" :src="reviewUrl" controls preload="metadata" class="review-video" @play="publishReplayControl" @pause="publishReplayControl" @seeked="publishReplayControl" @timeupdate="publishReplayControl" @ratechange="publishReplayControl">
          Kamera review videosu hazır değil; kare dizisi/telemetri kaydı korunuyor.
        </video>
        <div class="replay-actions">
          <button class="twin-replay" @click="emit('loadTwinReplay', selected.engagement_id)">3B ikiz replay yükle</button>
          <label>Hız
            <select v-model.number="replayRate" @change="setReplayRate">
              <option :value="0.25">0.25×</option><option :value="0.5">0.5×</option><option :value="1">1×</option><option :value="2">2×</option>
            </select>
          </label>
        </div>
        <p class="reason">{{ selected.reason_codes.at(-1) ?? 'PENDING' }}</p>
      </div>
    </div>
  </section>
</template>

<style scoped>
.evidence-layout { display: grid; grid-template-columns: minmax(190px, .8fr) minmax(260px, 1.2fr); gap: 10px; }
.record-list { display: grid; max-height: 240px; overflow: auto; gap: 6px; }
.record { display: grid; gap: 2px; border: 1px solid rgba(148,163,184,.2); border-radius: 7px; background: rgba(2,6,23,.56); color: #dbeafe; padding: 8px; text-align: left; cursor: pointer; }
.record.selected { border-color: rgba(34,211,238,.65); background: rgba(8,47,73,.38); }
.record span, .review-meta span, .reason { color: #94a3b8; font-size: 11px; }
.record em { font-style: normal; font-size: 10px; font-weight: 800; }
.tone-good { color: #86efac; }.tone-warn { color: #fde68a; }.tone-bad { color: #fca5a5; }.tone-neutral { color: #cbd5e1; }
.review { min-width: 0; }.review-meta { display: grid; gap: 2px; margin-bottom: 7px; font-size: 12px; }.review-video { width: 100%; max-height: 210px; border-radius: 7px; background: #020617; }.empty { color: #94a3b8; font-size: 12px; }
.replay-actions { display: flex; align-items: center; gap: 8px; margin-top: 7px; }.twin-replay { border: 1px solid rgba(34,211,238,.35); border-radius: 6px; background: rgba(8,47,73,.46); color: #cffafe; padding: 6px 8px; font-size: 11px; font-weight: 700; cursor: pointer; }.replay-actions label { color: #94a3b8; font-size: 11px; }.replay-actions select { margin-left: 4px; border: 1px solid rgba(148,163,184,.3); border-radius: 5px; background: #0f172a; color: #e2e8f0; padding: 4px; }
@media (max-width: 760px) { .evidence-layout { grid-template-columns: 1fr; } }
</style>
