<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import DashboardCard from '../components/dashboard/DashboardCard.vue'
import MetricRow from '../components/dashboard/MetricRow.vue'
import StatusBadge from '../components/shared/StatusBadge.vue'
import Stage3RangeCalibrationPanel from '../components/mission/Stage3RangeCalibrationPanel.vue'
import Stage3IffCalibrationPanel from '../components/mission/Stage3IffCalibrationPanel.vue'
import { useMissionStore } from '../stores/missionStore'
import { useMotionStore } from '../stores/motionStore'
import { useSerialStore } from '../stores/serialStore'
import { useVisionStore } from '../stores/visionStore'
import { evaluateFireRequest } from '../api/decision'
import { fetchStage2Engagement, fetchStage3Engagement } from '../api/mission'
import type { MissionStage, MissionUpdate, Stage1RangeScore, Stage1Target, Stage2EngagementStatus, Stage3EngagementStatus } from '../types/mission'

type TargetType = 'Balistik Füze' | 'Helikopter' | 'F16' | 'Mini/Micro İHA'

const vision = useVisionStore()
const motion = useMotionStore()
const serial = useSerialStore()
const mission = useMissionStore()

let timer: ReturnType<typeof setInterval> | null = null

const state = computed(() => mission.snapshot.state)
const score = computed(() => mission.snapshot.score)
const stage1Order = computed(() => state.value.stage1_order as TargetType[])
const activeStage = computed(() => state.value.active_stage)
const timerRunning = computed(() => state.value.timer_running)
const elapsedS = computed(() => state.value.elapsed_s)
const stage1Hits = computed(() => state.value.stage1_hits)
const stage1WrongHits = computed(() => state.value.stage1_wrong_hits)
const stage1Locked = computed(() => state.value.stage1_order_locked)
const stage1NextTarget = computed(() => score.value.stage1_next_target)
const stage1NextTargetLabel = computed(() => stage1NextTarget.value === 'F16' ? 'Savaş Uçağı' : stage1NextTarget.value)
const stage1RawPoints = computed(() => score.value.stage1_raw_points)
const stage1PenaltyPoints = computed(() => score.value.stage1_penalty_points)
const stage1BonusPoints = computed(() => score.value.stage1_bonus_points)
const stage1AwardedScore = ref<Stage1RangeScore>(20)
const wrongTarget = ref<Stage1Target>('Helikopter')
const manualFireResult = ref('')
const stage2Engagement = ref<Stage2EngagementStatus>({
  current_round: 1,
  fired_track_ids: [],
  pending_track_ids: [],
  confirmed_track_ids: [],
  reengage_track_ids: [],
  ready_to_close: false,
  reason_codes: [],
  updated_at: 0,
})
const stage3Engagement = ref<Stage3EngagementStatus>({
  current_round: 1,
  enemy_class: null,
  enemy_balloon_track_id: null,
  friend_links: [],
  enemy_confirmation_state: null,
  enemy_hit_confirmed: false,
  friend_safety_verified: false,
  friend_hit_suspected: false,
  ready_to_close: false,
  reason_codes: [],
  shot_at: null,
  updated_at: 0,
})
const stage2Round = computed(() => state.value.stage2_round)
const stage2Hits = computed(() => state.value.stage2_hits)
const stage3Round = computed(() => state.value.stage3_round)
const stage3Hits = computed(() => state.value.stage3_hits)
const stage3FriendHits = computed(() => state.value.stage3_friend_or_miss_penalties)

const stageCards = computed(() => [
  {
    id: 'stage1',
    title: 'Aşama 1',
    subtitle: 'Farklı menzillerde duran hedef imhası',
    mode: 'Manuel',
    score: '100 puan',
    rule: '5 dakikada 5/10/15 m hedefleri verilen sırayla kullanıcı komutuyla imha.',
    gate: 'Ateş kullanıcı komutuyla; otomatik ateş yok.',
  },
  {
    id: 'stage2',
    title: 'Aşama 2',
    subtitle: 'Sürü saldırısı ve hedeflerin imhası',
    mode: 'Otonom',
    score: '120 puan',
    rule: '4 tur, 3 yaklaşma kolu, hedefler parkurdan çıkmadan imha.',
    gate: 'Hedef tipi sınıflandırması beklenmiyor; takip ve imha hızı kritik.',
  },
  {
    id: 'stage3',
    title: 'Aşama 3',
    subtitle: 'Farklı katmanlardaki hareketli hedefler',
    mode: 'Otonom + sınıflandırma',
    score: '160 puan',
    rule: '8 tur, 1 düşman + 2 dost; doğru hedef doğru menzilde imha.',
    gate: 'Dost hedef ve menzil dışı hedef fire block üretmeli.',
  },
])

const rangeRules = [
  { target: 'Savaş Uçağı', range: '10-15 m', score: '30 puan', note: '10 m altı puan yok' },
  { target: 'Helikopter', range: '5-15 m', score: '20 puan', note: '5 m altı puan yok' },
  { target: 'Balistik Füze', range: '5-15 m', score: '20 puan', note: '5 m altı puan yok' },
  { target: 'Mini/Micro İHA', range: '0-15 m', score: '10 puan', note: 'yakın menzil geçerli' },
]

const stage2Rounds = computed(() => Array.from({ length: 4 }, (_, index) => ({
  round: index + 1,
  status: vision.visionStatus.balloon_count > 0 && motion.trackingStatus.active ? 'takip hazır' : 'beklemede',
  tone: vision.visionStatus.balloon_count > 0 && motion.trackingStatus.active ? 'good' : 'warn',
})))

const stage3Rounds = computed(() => Array.from({ length: 8 }, (_, index) => ({
  round: index + 1,
  enemy: index % 4 === 0 ? 'Savaş Uçağı' : index % 4 === 1 ? 'Helikopter' : index % 4 === 2 ? 'Balistik Füze' : 'Mini/Micro İHA',
  gate: serial.status.magazine_remaining > 0 ? 'fire gate hazır' : 'şarjör boş',
  tone: serial.status.magazine_remaining > 0 ? 'good' : 'bad',
})))

const activeCard = computed(() => stageCards.value.find((card) => card.id === activeStage.value) ?? stageCards.value[0])
const remainingS = computed(() => score.value.remaining_s)
const timeLabel = computed(() => `${String(Math.floor(remainingS.value / 60)).padStart(2, '0')}:${String(remainingS.value % 60).padStart(2, '0')}`)
const stage2Score = computed(() => score.value.stage2_score)
const activeScore = computed(() => score.value.active_score)

function updateMission(update: MissionUpdate): void {
  void mission.update(update).catch((caught) => {
    mission.error = caught instanceof Error ? caught.message : 'Mission update failed'
  })
}

function setActiveStage(id: string): void {
  if (id === 'stage1' || id === 'stage2' || id === 'stage3') updateMission({ active_stage: id as MissionStage })
}

function startTimer(): void {
  if (timerRunning.value) return
  if (activeStage.value === 'stage1' && !stage1Locked.value) {
    void mission.lockStage1().then(startLocalTimer).catch((caught) => {
      mission.error = caught instanceof Error ? caught.message : 'Aşama 1 başlatılamadı'
    })
  } else {
    updateMission({ timer_running: true })
    startLocalTimer()
  }
}

function startLocalTimer(): void {
  if (timer) return
  timer = setInterval(() => {
    const nextElapsed = elapsedS.value + 1
    updateMission({ elapsed_s: nextElapsed })
    if (nextElapsed >= 300 && activeStage.value === 'stage1') pauseTimer()
  }, 1000)
}

function pauseTimer(): void {
  updateMission({ timer_running: false })
  if (timer) clearInterval(timer)
  timer = null
}

async function resetStage(): Promise<void> {
  pauseTimer()
  await mission.reset()
}

function saveStage1Plan(): void {
  void mission.setStage1Plan([...stage1Order.value]).catch((caught) => {
    mission.error = caught instanceof Error ? caught.message : 'Aşama 1 planı kaydedilemedi'
  })
}

function lockStage1Plan(): void {
  void mission.lockStage1().then(startLocalTimer).catch((caught) => {
    mission.error = caught instanceof Error ? caught.message : 'Aşama 1 planı kilitlenemedi'
  })
}

function recordStage1Hit(): void {
  if (!stage1NextTarget.value) return
  void mission.recordStage1CorrectHit(stage1NextTarget.value, stage1AwardedScore.value).catch((caught) => {
    mission.error = caught instanceof Error ? caught.message : 'Doğru imha kaydedilemedi'
  })
}

function recordStage1Wrong(): void {
  void mission.recordStage1Wrong(wrongTarget.value).catch((caught) => {
    mission.error = caught instanceof Error ? caught.message : 'Yanlış hedef kaydedilemedi'
  })
}

async function requestManualFire(): Promise<void> {
  const result = await evaluateFireRequest(true)
  manualFireResult.value = result.accepted ? 'MANUAL_FIRE_ACK' : result.blocking_reasons.join(' · ') || result.reason
}

function refreshStage2Engagement(): void {
  void fetchStage2Engagement().then((status) => {
    stage2Engagement.value = status
  }).catch((caught) => {
    mission.error = caught instanceof Error ? caught.message : 'Aşama 2 angajman durumu alınamadı'
  })
}

function closeStage2Round(): void {
  void mission.closeStage2().then(() => {
    refreshStage2Engagement()
  }).catch((caught) => {
    mission.error = caught instanceof Error ? caught.message : 'Aşama 2 tur sonucu kaydedilemedi'
  })
}

function refreshStage3Engagement(): void {
  void fetchStage3Engagement().then((status) => {
    stage3Engagement.value = status
  }).catch((caught) => {
    mission.error = caught instanceof Error ? caught.message : 'Aşama 3 angajman durumu alınamadı'
  })
}

function closeStage3Round(): void {
  void mission.closeStage3().then(() => {
    refreshStage3Engagement()
  }).catch((caught) => {
    mission.error = caught instanceof Error ? caught.message : 'Aşama 3 kanonik tur sonucu kaydedilemedi'
  })
}

onMounted(async () => {
  await mission.refresh()
  refreshStage2Engagement()
  refreshStage3Engagement()
  if (timerRunning.value) startLocalTimer()
})

onUnmounted(() => pauseTimer())
</script>

<template>
  <div class="grid gap-4">
    <div class="grid gap-4 xl:grid-cols-3">
      <DashboardCard v-for="card in stageCards" :key="card.id" :title="card.title" :subtitle="card.subtitle">
        <div class="mb-3 flex flex-wrap gap-2">
          <StatusBadge :label="card.mode" :tone="activeStage === card.id ? 'good' : 'neutral'" />
          <StatusBadge :label="card.score" tone="neutral" />
        </div>
        <p class="text-sm text-slate-300">{{ card.rule }}</p>
        <p class="mt-2 rounded-md border border-white/8 bg-black/18 px-3 py-2 text-xs text-slate-400">{{ card.gate }}</p>
        <button class="focus-ring mt-4 rounded-md px-3 py-2 text-sm font-semibold" :class="activeStage === card.id ? 'bg-cyan-500 text-slate-950' : 'bg-slate-700 text-white'" @click="setActiveStage(card.id)">
          Bu aşamayı aç
        </button>
      </DashboardCard>
    </div>

    <DashboardCard :title="activeCard.title + ' Operatör Akışı'" :subtitle="activeCard.subtitle">
      <div class="mb-4 grid gap-3 md:grid-cols-[1fr_1fr_auto_auto_auto]">
        <div class="rounded-md border border-white/8 bg-black/18 px-3 py-2">
          <p class="text-xs uppercase tracking-[0.14em] text-slate-500">Süre</p>
          <p class="font-mono text-2xl font-semibold text-white">{{ timeLabel }}</p>
        </div>
        <div class="rounded-md border border-white/8 bg-black/18 px-3 py-2">
          <p class="text-xs uppercase tracking-[0.14em] text-slate-500">Tahmini puan</p>
          <p class="font-mono text-2xl font-semibold text-cyan-100">{{ activeScore }}</p>
        </div>
        <button class="focus-ring rounded-md bg-cyan-500 px-4 py-2 text-sm font-semibold text-slate-950" @click="startTimer">Başlat</button>
        <button class="focus-ring rounded-md bg-slate-700 px-4 py-2 text-sm font-semibold text-white" @click="pauseTimer">Duraklat</button>
        <button class="focus-ring rounded-md bg-red-500 px-4 py-2 text-sm font-semibold text-white" @click="resetStage">Reset</button>
      </div>

      <div v-if="activeStage === 'stage1'" class="grid gap-4 xl:grid-cols-[0.8fr_1.2fr]">
        <div>
          <MetricRow label="Mod" value="Manuel" />
          <MetricRow label="Süre" value="5 dakika" />
          <MetricRow label="Geçiş barajı" value="30 puan" />
          <MetricRow label="Bonus" value="Kalan süre / 300 × 20" />
          <MetricRow label="Ceza" value="Yanlış hedef -5" />
          <MetricRow label="Doğru imha" :value="stage1Hits" />
          <MetricRow label="Yanlış imha" :value="stage1WrongHits" />
          <MetricRow label="Ham / ceza / bonus" :value="`${stage1RawPoints} / ${stage1PenaltyPoints} / ${stage1BonusPoints}`" />
          <MetricRow label="Sıradaki hedef" :value="stage1NextTargetLabel ?? 'Tamamlandı'" />
          <StatusBadge :label="stage1Locked ? 'PLAN KİLİTLİ' : 'PLAN AÇIK'" :tone="stage1Locked ? 'good' : 'warn'" />
          <div class="mt-3 grid grid-cols-2 gap-2">
            <select v-model.number="stage1AwardedScore" :disabled="!stage1Locked" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm text-white">
              <option :value="5">Yakın: 5 puan</option><option :value="10">Orta: 10 puan</option><option :value="20">Uzak: 20 puan</option>
            </select>
            <button :disabled="!stage1Locked || !stage1NextTarget" class="focus-ring rounded-md bg-emerald-500 px-3 py-2 text-sm font-semibold text-slate-950 disabled:opacity-40" @click="recordStage1Hit">Doğru imha kaydet</button>
            <select v-model="wrongTarget" :disabled="!stage1Locked" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm text-white">
              <option v-for="target in stage1Order" :key="target" :value="target">{{ target }}</option>
            </select>
            <button :disabled="!stage1Locked" class="focus-ring rounded-md bg-red-500 px-3 py-2 text-sm font-semibold text-white disabled:opacity-40" @click="recordStage1Wrong">Yanlış hedef -5</button>
          </div>
          <button :disabled="!stage1Locked || !timerRunning || !stage1NextTarget" class="focus-ring mt-3 w-full rounded-md border border-red-200/40 bg-red-600 px-3 py-3 text-sm font-black text-white disabled:opacity-40" @click="requestManualFire">MANUEL FIRE — {{ stage1NextTargetLabel ?? 'GÖREV TAMAM' }}</button>
          <p v-if="manualFireResult" class="mt-2 font-mono text-xs text-amber-200">{{ manualFireResult }}</p>
        </div>
        <div class="grid gap-2">
          <div v-for="(target, index) in stage1Order" :key="`${target}-${index}`" class="grid grid-cols-[48px_1fr_auto] items-center gap-3 rounded-md border border-white/8 bg-black/18 px-3 py-2">
            <span class="font-mono text-sm text-slate-500">#{{ index + 1 }}</span>
            <select v-model="stage1Order[index]" :disabled="stage1Locked" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm text-white">
              <option>Balistik Füze</option>
              <option>Helikopter</option>
              <option value="F16">Savaş Uçağı</option>
              <option>Mini/Micro İHA</option>
            </select>
            <StatusBadge :label="state.stage1_completed_targets.includes(target as Stage1Target) ? 'tamamlandı' : index === state.stage1_completed_targets.length ? 'sıradaki' : 'bekliyor'" :tone="state.stage1_completed_targets.includes(target as Stage1Target) ? 'good' : index === state.stage1_completed_targets.length ? 'warn' : 'neutral'" />
          </div>
          <div class="mt-2 grid grid-cols-2 gap-2">
            <button :disabled="stage1Locked" class="focus-ring rounded-md bg-slate-700 px-3 py-2 text-sm font-semibold text-white disabled:opacity-40" @click="saveStage1Plan">Planı doğrula</button>
            <button :disabled="stage1Locked" class="focus-ring rounded-md bg-amber-500 px-3 py-2 text-sm font-semibold text-slate-950 disabled:opacity-40" @click="lockStage1Plan">Yarışmayı başlat ve kilitle</button>
          </div>
        </div>
      </div>

      <div v-else-if="activeStage === 'stage2'" class="grid gap-3 md:grid-cols-4">
        <div v-for="round in stage2Rounds" :key="round.round" class="rounded-md border border-white/8 bg-black/18 p-3">
          <div class="mb-2 flex items-center justify-between gap-2">
            <span class="text-sm font-semibold text-white">Tur {{ round.round }}</span>
            <StatusBadge :label="round.status" :tone="round.tone as any" />
          </div>
          <p class="text-xs text-slate-400">3 koldan gelen hedefler parkurdan çıkmadan imha edilmeli.</p>
        </div>
        <div class="md:col-span-4 grid gap-2 md:grid-cols-3">
          <MetricRow label="Aktif tur" :value="stage2Round" />
          <MetricRow label="Toplam imha" :value="stage2Hits" />
          <MetricRow label="Puan" :value="stage2Score" />
          <MetricRow label="Gateway atışı" :value="stage2Engagement.fired_track_ids.length" />
          <MetricRow label="Doğrulanmış hit" :value="`${stage2Engagement.confirmed_track_ids.length}/3`" />
          <MetricRow label="Pending / reengage" :value="`${stage2Engagement.pending_track_ids.length} / ${stage2Engagement.reengage_track_ids.length}`" />
          <button class="focus-ring rounded-md bg-slate-700 px-3 py-2 text-sm font-semibold text-white" @click="refreshStage2Engagement">Angajmanı yenile</button>
          <button :disabled="!stage2Engagement.ready_to_close || state.stage2_completed_rounds >= 4 || state.stage2_failed" class="focus-ring rounded-md bg-emerald-500 px-3 py-2 text-sm font-semibold text-slate-950 disabled:opacity-40" @click="closeStage2Round">Doğrulanmış sonuçla turu kapat</button>
          <MetricRow label="0-hit serisi" :value="state.stage2_zero_hit_streak" />
          <MetricRow label="Aşama durumu" :value="state.stage2_failed ? 'BAŞARISIZ' : score.stage2_passing_threshold_met ? '20+ GEÇİŞ' : 'DEVAM'" />
          <p v-if="stage2Engagement.reason_codes.length" class="md:col-span-3 font-mono text-xs text-amber-200">{{ stage2Engagement.reason_codes.join(' · ') }}</p>
        </div>
      </div>

      <div v-else class="grid gap-3 md:grid-cols-4">
        <div v-for="round in stage3Rounds" :key="round.round" class="rounded-md border border-white/8 bg-black/18 p-3">
          <div class="mb-2 flex items-center justify-between gap-2">
            <span class="text-sm font-semibold text-white">Tur {{ round.round }}</span>
            <StatusBadge :label="round.gate" :tone="round.tone as any" />
          </div>
          <p class="text-xs text-slate-400">Örnek düşman: {{ round.enemy }}. 2 dost hedef fire block üretmeli.</p>
        </div>
        <div class="md:col-span-4 grid gap-2 md:grid-cols-3">
          <MetricRow label="Aktif tur" :value="stage3Round" />
          <MetricRow label="Düşman imha" :value="stage3Hits" />
          <MetricRow label="Dost/kaçan ceza" :value="stage3FriendHits" />
          <MetricRow label="Düşman aday sınıfı" :value="stage3Engagement.enemy_class ?? 'bekleniyor'" />
          <MetricRow label="Düşman kanıtı" :value="stage3Engagement.enemy_confirmation_state ?? 'shot yok'" />
          <MetricRow label="Dost linkleri" :value="`${stage3Engagement.friend_links.length}/2`" />
          <MetricRow label="Dost güvenliği" :value="stage3Engagement.friend_hit_suspected ? 'HIT ŞÜPHESİ' : stage3Engagement.friend_safety_verified ? 'DOĞRULANDI' : 'BEKLENİYOR'" />
          <button class="focus-ring rounded-md bg-slate-700 px-3 py-2 text-sm font-semibold text-white" @click="refreshStage3Engagement">Angajmanı yenile</button>
          <button :disabled="!stage3Engagement.ready_to_close || state.stage3_completed_rounds >= 8 || state.stage3_failed" class="focus-ring rounded-md bg-emerald-500 px-3 py-2 text-sm font-semibold text-slate-950 disabled:opacity-40" @click="closeStage3Round">Kanıtlı sonuçla turu kapat</button>
          <MetricRow label="Miss serisi" :value="state.stage3_miss_streak" />
          <MetricRow label="Aşama durumu" :value="state.stage3_failed ? 'BAŞARISIZ' : score.stage3_award_threshold_met ? '10+ ÖDÜL EŞİĞİ' : 'DEVAM'" />
          <p v-if="stage3Engagement.reason_codes.length" class="md:col-span-3 font-mono text-xs text-amber-200">{{ stage3Engagement.reason_codes.join(' · ') }}</p>
        </div>
        <div class="md:col-span-2"><Stage3IffCalibrationPanel /></div>
        <div class="md:col-span-2"><Stage3RangeCalibrationPanel /></div>
      </div>
    </DashboardCard>

    <DashboardCard title="Aşama 3 Menzil ve Puan Kapıları" subtitle="Doğru hedef doğru mesafede vurulmalı">
      <div class="overflow-x-auto">
        <table class="w-full text-left text-sm">
          <thead class="text-xs uppercase tracking-[0.14em] text-slate-500">
            <tr>
              <th class="py-2">Hedef</th>
              <th class="py-2">Geçerli menzil</th>
              <th class="py-2">Puan</th>
              <th class="py-2">Not</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="rule in rangeRules" :key="rule.target" class="border-t border-white/8">
              <td class="py-2 text-slate-100">{{ rule.target }}</td>
              <td class="py-2 font-mono text-cyan-100">{{ rule.range }}</td>
              <td class="py-2 text-slate-300">{{ rule.score }}</td>
              <td class="py-2 text-slate-400">{{ rule.note }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </DashboardCard>
  </div>
</template>
