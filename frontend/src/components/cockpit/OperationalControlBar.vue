<script setup lang="ts">
import {
  Camera,
  Crosshair,
  Flame,
  Gamepad2,
  Keyboard,
  Pause,
  Play,
  ShieldAlert,
  Target,
  X,
} from '@lucide/vue'
import { computed } from 'vue'
import type { TargetPolicy } from '../../types/operation'

export type OperationalControlMode = 'AUTO' | 'MANUAL'

const props = defineProps<{
  mode: OperationalControlMode
  targetPolicy: TargetPolicy
  bboxLockEnabled: boolean
  triggerArmed: boolean
  triggerConfirmed: boolean
  manualMotionEnabled: boolean
  gamepadConnected?: boolean
  gamepadLabel?: string
  trackingActive: boolean
  selectedTarget: string
  motionReady: boolean
  fireReady: boolean
  blocker: string
  magazineEmpty: boolean
  magazineRemaining: number
  magazineCapacity: number
  busy?: boolean
}>()

const emit = defineEmits<{
  setMode: [mode: OperationalControlMode]
  setTargetPolicy: [policy: TargetPolicy]
  toggleBboxLock: []
  toggleTrigger: []
  toggleManualMotion: []
  toggleTracking: []
  openCameraSettings: []
  safeStop: []
  releaseTarget: []
  fire: []
}>()

const hasTarget = computed(() => props.selectedTarget !== 'Hedef yok')
const canStartTracking = computed(() => props.mode === 'AUTO')
const isMotionAllowed = computed(() => props.motionReady || props.mode === 'MANUAL' || props.mode === 'AUTO')
const isFireAllowed = computed(() => (props.fireReady || props.triggerArmed || props.triggerConfirmed) && !props.magazineEmpty)
const canManualFire = computed(() => isFireAllowed.value)

const readinessLabel = computed(() => {
  if (props.magazineEmpty) return 'MERMİ YOK · FIRE ENGELLİ'
  if (isFireAllowed.value) return 'HAREKET VE FIRE HAZIR'
  if (isMotionAllowed.value) return 'HAREKET HAZIR'
  return 'HAREKET HAZIR'
})
const readinessCode = computed(() => {
  if (props.magazineEmpty) return 'MAGAZINE_EMPTY'
  if (isFireAllowed.value || isMotionAllowed.value) return 'READY'
  return 'READY'
})
</script>

<template>
  <section class="operational-control-bar" aria-label="Operatör kontrolü">
    <div class="control-context">
      <div class="context-target" :class="{ selected: hasTarget }">
        <Target :size="17" />
        <div>
          <span>SEÇİLİ HEDEF</span>
          <b>{{ props.selectedTarget }}</b>
        </div>
        <button v-if="hasTarget" type="button" aria-label="Seçili hedefi bırak" title="Hedefi bırak" @click="emit('releaseTarget')">
          <X :size="14" />
        </button>
      </div>
      <div class="context-readiness" :class="props.magazineEmpty ? 'blocked' : isFireAllowed ? 'ready' : isMotionAllowed ? 'motion-ready' : 'ready'">
        <span class="readiness-dot" aria-hidden="true"></span>
        <div>
          <span>{{ readinessLabel }}</span>
          <b>{{ readinessCode }}</b>
        </div>
        <small>{{ props.magazineRemaining }}/{{ props.magazineCapacity }}</small>
      </div>
    </div>

    <div class="control-actions">
      <div class="control-cluster mode-cluster">
        <span class="cluster-label">KONTROL</span>
        <div class="segmented-control" role="group" aria-label="Kontrol modu">
          <button type="button" :class="{ active: props.mode === 'AUTO' }" :disabled="props.busy" title="Seçili hedefi otonom takip et" @click="emit('setMode', 'AUTO')">
            <Play :size="14" /> OTONOM
          </button>
          <button type="button" :class="{ active: props.mode === 'MANUAL' }" :disabled="props.busy" title="Yön tuşlarıyla manuel hareket" @click="emit('setMode', 'MANUAL')">
            <Gamepad2 :size="14" /> MANUEL
          </button>
        </div>
      </div>

      <div class="control-cluster policy-cluster">
        <span class="cluster-label">HEDEF POLİTİKASI</span>
        <div class="segmented-control target-policy-control" role="group" aria-label="Hedef politikası">
          <button type="button" :class="{ active: props.targetPolicy === 'STAGE1_INDEPENDENT' }" :disabled="props.busy" title="Aşama 1: Hava aracı ve balonlar birbirinden tamamen bağımsız" @click="emit('setTargetPolicy', 'STAGE1_INDEPENDENT')">BAĞIMSIZ</button>
          <button type="button" :class="{ active: props.targetPolicy === 'BALLOON' }" :disabled="props.busy" title="Yalnız balon bbox'ları seçilebilir" @click="emit('setTargetPolicy', 'BALLOON')">BALON</button>
          <button type="button" :class="{ active: props.targetPolicy === 'AIRCRAFT' }" :disabled="props.busy" title="Yalnız hava aracı bbox'ları seçilebilir" @click="emit('setTargetPolicy', 'AIRCRAFT')">HAVA ARACI</button>
          <button type="button" :class="{ active: props.targetPolicy === 'BALLOON_AIRCRAFT' }" :disabled="props.busy" title="Hava aracı ve bağlı balonu tek hedef olarak kullan" @click="emit('setTargetPolicy', 'BALLOON_AIRCRAFT')">BİRLİKTE</button>
        </div>
        <button class="bbox-lock-toggle" :class="{ active: props.bboxLockEnabled }" type="button" :disabled="props.busy" :aria-pressed="props.bboxLockEnabled" title="BBox içindeki %30 takip kilit bölgesini aç veya kapat" @click="emit('toggleBboxLock')">
          %30 KİLİT {{ props.bboxLockEnabled ? 'AÇIK' : 'KAPALI' }}
        </button>
      </div>

      <div class="control-cluster compact-cluster">
        <span class="cluster-label">ATIŞ · TETİK {{ (props.triggerArmed || props.triggerConfirmed) ? 'ARMED' : 'DISARMED' }}</span>
        <button class="control-button" :class="props.triggerArmed || props.triggerConfirmed ? 'trigger-on' : 'trigger-off'" type="button" :disabled="props.busy" :aria-pressed="props.triggerArmed || props.triggerConfirmed" :title="`Tetik durumu: ${(props.triggerArmed || props.triggerConfirmed) ? 'AÇIK (ARMED)' : 'KAPALI (DISARMED)'}`" @click="emit('toggleTrigger')">
          <Flame :size="15" /><span>TETİK {{ (props.triggerArmed || props.triggerConfirmed) ? 'AÇIK' : 'KAPALI' }}</span>
        </button>
      </div>

      <div class="control-cluster compact-cluster">
        <span class="cluster-label">HAREKET{{ props.gamepadConnected ? ' · USB BAĞLI' : '' }}</span>
        <button class="control-button" :class="props.manualMotionEnabled ? 'motion-on' : 'motion-off'" type="button" :disabled="props.busy" :aria-pressed="props.manualMotionEnabled" :title="props.manualMotionEnabled ? (props.gamepadConnected ? `${props.gamepadLabel ?? 'USB kumanda'} ve yön tuşları aktif` : 'Yön tuşları aktif') : 'Manuel hareket kapalı'" @click="emit('toggleManualMotion')">
          <Gamepad2 v-if="props.gamepadConnected" :size="15" /><Keyboard v-else :size="15" /><span>MANUEL {{ props.manualMotionEnabled ? 'AÇIK' : 'KAPALI' }}</span>
        </button>
      </div>

      <div class="primary-actions">
        <button class="control-button tracking-button" :class="{ active: props.trackingActive }" type="button" :disabled="props.busy || (!props.trackingActive && !canStartTracking)" :title="props.trackingActive ? 'Takibi durdur' : props.mode !== 'AUTO' ? 'Önce OTONOM modunu seç' : 'Takibi başlat'" @click="emit('toggleTracking')">
          <Pause v-if="props.trackingActive" :size="15" /><Crosshair v-else :size="15" />
          <span>{{ props.trackingActive ? 'TAKİBİ DURDUR' : 'TAKİBİ BAŞLAT' }}</span>
        </button>
        <button class="control-button fire-button" :class="{ 'fire-empty': props.magazineEmpty }" type="button" :disabled="props.busy || !canManualFire" :title="props.magazineEmpty ? 'Mermi yok · Şarjörü doldurun' : (props.triggerArmed || props.triggerConfirmed || props.fireReady) ? 'Manuel FIRE gönder' : 'Tetik kapalı'" @click="emit('fire')">
          <Flame :size="15" /><span>FIRE</span><kbd>F</kbd>
        </button>
        <button class="control-button camera-button" type="button" :disabled="props.busy" title="Kamera seçimi ve görüntü ayarları" @click="emit('openCameraSettings')">
          <Camera :size="15" /><span>KAMERA</span>
        </button>
        <button class="control-button safe-button" type="button" title="Yazılımsal hareket ve tetik çıkışını durdur" @click="emit('safeStop')">
          <ShieldAlert :size="15" /><span>DURDUR</span>
        </button>
      </div>
    </div>
  </section>
</template>

<style scoped>
.operational-control-bar{display:grid;grid-template-columns:minmax(330px,.72fr) minmax(0,2fr);align-items:center;gap:12px;min-height:86px;padding:9px 11px;border:1px solid rgba(94,234,255,.2);border-radius:15px;background:linear-gradient(180deg,rgba(8,25,40,.98),rgba(3,11,21,.99));box-shadow:0 -10px 32px rgba(0,0,0,.3),inset 0 1px rgba(255,255,255,.025)}
.control-context{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.15fr);align-items:stretch;gap:8px;min-width:0}.context-target,.context-readiness{display:flex;align-items:center;gap:8px;min-width:0;padding:9px 10px;border:1px solid rgba(148,163,184,.14);border-radius:10px;background:rgba(3,12,23,.74)}.context-target{color:#8be8f7}.context-target.selected{border-color:rgba(94,234,255,.3);background:linear-gradient(135deg,rgba(8,47,73,.48),rgba(3,12,23,.8))}.context-target>div,.context-readiness>div{display:grid;min-width:0}.context-target span,.context-readiness span{color:#7892a4;font-size:.62rem;font-weight:900;letter-spacing:.12em;line-height:1.15}.context-target b,.context-readiness b{overflow:hidden;margin-top:3px;color:#e1f5fb;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.76rem;text-overflow:ellipsis;white-space:nowrap}.context-target>button{display:grid;place-items:center;width:25px;height:25px;flex:none;margin-left:auto;border:1px solid rgba(148,163,184,.18);border-radius:7px;background:rgba(15,23,42,.68);color:#9fb5c2;cursor:pointer}.context-target>button:hover{border-color:rgba(103,232,249,.54);color:#e8fbff}.context-readiness>div{flex:1}.context-readiness>div>span{color:#91aabd}.context-readiness small{align-self:flex-start;border:1px solid rgba(103,232,249,.18);border-radius:999px;padding:3px 6px;color:#c9f7ff;font-family:ui-monospace,monospace;font-size:.62rem;font-weight:900}.context-readiness.ready{border-color:rgba(52,211,153,.3)}.context-readiness.ready b{color:#9af2c3}.context-readiness.motion-ready{border-color:rgba(94,234,255,.28)}.context-readiness.motion-ready b{color:#a5f3fc}.context-readiness.blocked{border-color:rgba(248,113,113,.38)}.context-readiness.blocked b{color:#fecaca}.readiness-dot{width:9px;height:9px;flex:none;border-radius:999px;background:#fbbf24;box-shadow:0 0 0 4px rgba(251,191,36,.1)}.ready .readiness-dot{background:#34d399;box-shadow:0 0 0 4px rgba(52,211,153,.1)}.motion-ready .readiness-dot{background:#22d3ee;box-shadow:0 0 0 4px rgba(34,211,238,.1)}.blocked .readiness-dot{background:#f87171;box-shadow:0 0 0 4px rgba(248,113,113,.1)}
.control-actions{display:flex;align-items:flex-end;justify-content:flex-end;gap:7px;min-width:0}.control-cluster{display:grid;gap:4px;min-width:0}.cluster-label{padding-left:5px;color:#627f91;font-size:.55rem;font-weight:950;letter-spacing:.14em;white-space:nowrap}.segmented-control{display:flex;align-items:stretch;padding:2px;border:1px solid rgba(103,232,249,.18);border-radius:10px;background:rgba(2,8,18,.66)}.segmented-control button,.control-button{display:inline-flex;align-items:center;justify-content:center;gap:6px;min-height:36px;border:1px solid transparent;border-radius:8px;background:transparent;color:#b7cbd7;padding:8px 9px;font-size:.70rem;font-weight:900;letter-spacing:.025em;white-space:nowrap;cursor:pointer;transition:background .16s,border-color .16s,color .16s,transform .16s,box-shadow .16s}.segmented-control button:hover:not(:disabled),.control-button:hover:not(:disabled){border-color:rgba(103,232,249,.46);background:rgba(8,74,99,.5);color:#e8fbff;transform:translateY(-1px)}.segmented-control button.active{border-color:rgba(94,234,255,.38);background:linear-gradient(180deg,rgba(8,92,118,.7),rgba(8,55,78,.72));color:#e1fbff;box-shadow:inset 0 1px rgba(255,255,255,.04)}.target-policy-control{border-color:rgba(167,139,250,.22)}.target-policy-control button.active{border-color:rgba(94,234,255,.38);background:linear-gradient(180deg,rgba(8,92,118,.72),rgba(8,55,78,.74))}.control-button{border-color:rgba(148,163,184,.16);background:rgba(8,23,37,.72)}.control-button:disabled,.segmented-control button:disabled{opacity:.38;cursor:not-allowed;transform:none}.trigger-on{border-color:rgba(245,158,11,.54);background:linear-gradient(180deg,rgba(120,72,8,.36),rgba(74,44,8,.28));color:#ffe0a3;box-shadow:inset 3px 0 #f59e0b}.trigger-off{color:#9fb0bc}.trigger-mismatch{border-color:rgba(248,113,113,.7)!important;background:rgba(127,29,29,.28)!important;color:#fecaca!important;box-shadow:inset 3px 0 #ef4444!important}.motion-on{border-color:rgba(34,211,238,.34);color:#b6f5ff;box-shadow:inset 3px 0 #22d3ee}.motion-off{color:#8093a3}.primary-actions{display:flex;align-items:stretch;gap:6px}.tracking-button{border-color:rgba(103,232,249,.28);color:#c9f7ff}.tracking-button.active{border-color:rgba(52,211,153,.44);background:rgba(6,78,59,.34);color:#a7f3d0}.camera-button{color:#a5f3fc}.fire-button{border-color:rgba(245,158,11,.46);background:rgba(120,72,8,.27);color:#ffdc94}.fire-button kbd{border:1px solid rgba(255,255,255,.17);border-radius:4px;padding:1px 4px;background:rgba(0,0,0,.25);font:800 .58rem ui-monospace,monospace}.fire-button.fire-empty{border-color:rgba(248,113,113,.5);background:rgba(127,29,29,.28);color:#fecaca}.safe-button{border-color:rgba(251,113,133,.42);background:rgba(127,29,29,.32);color:#fecdd3}.safe-button:hover:not(:disabled){border-color:rgba(251,113,133,.82);background:rgba(153,27,27,.58)}
.bbox-lock-toggle{min-height:24px;border:1px solid rgba(148,163,184,.2);border-radius:7px;background:rgba(2,8,18,.72);color:#8297a5;font-size:.56rem;font-weight:950;letter-spacing:.08em;cursor:pointer}.bbox-lock-toggle.active{border-color:rgba(52,211,153,.4);background:rgba(6,78,59,.3);color:#a7f3d0}.bbox-lock-toggle:disabled{cursor:not-allowed;opacity:.4}
@media(max-width:1560px){.operational-control-bar{grid-template-columns:1fr;min-height:0}.control-context{grid-template-columns:minmax(220px,.65fr) minmax(260px,1fr)}.control-actions{justify-content:flex-start;flex-wrap:wrap}.cluster-label{display:none}}
@media(max-width:900px){.control-context{grid-template-columns:1fr}.control-actions{display:grid;grid-template-columns:1fr 1fr}.mode-cluster,.policy-cluster,.primary-actions{grid-column:1/-1}.segmented-control,.primary-actions{display:grid;grid-template-columns:repeat(2,minmax(0,1fr))}.target-policy-control{grid-template-columns:repeat(3,minmax(0,1fr))}.control-button span{white-space:normal}}
</style>
