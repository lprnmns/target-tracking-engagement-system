import { computed, readonly, ref } from 'vue'

export interface GamepadProfile {
  enabled: boolean
  preferredDeviceId: string
  axisX: number
  axisY: number
  invertX: boolean
  invertY: boolean
  deadzone: number
  responseExponent: number
  maxSpeedX: number
  maxSpeedY: number
  triggerButton: number
  zoomButton: number
}

export interface VisibleGamepad {
  index: number
  id: string
  mapping: string
  axes: number
  buttons: number
  connected: boolean
  logitechExtreme3D: boolean
}

// v2 changes the field-verified Y direction. A new key intentionally avoids
// retaining the older inverted-Y default already persisted in browsers.
// Y20b (1 Eki): v4 = dar olu bolge + yumusak egri + minimum hiz. Eski ayar v2 anahtarinda durur.
const STORAGE_KEY = 'istiklal_gamepad_profile_v4'
// Y20: olu bolgeyi gecen her sapma en az maksimumun %5'i kadar hareket verir (600 -> 30 adim/s).
const MIN_OUTPUT_FRACTION = 0.05
const LOGITECH_EXTREME_3D_PATTERN = /(logitech.*extreme.*3d|046d[^a-z0-9]*c215|vendor:\s*046d.*product:\s*c215)/i
const DEFAULT_PROFILE: GamepadProfile = {
  enabled: true,
  preferredDeviceId: '',
  axisX: 0,
  axisY: 1,
  invertX: false,
  // Field test (16 Aug): positive physical command follows the raw Logitech
  // axis direction on this turret installation; no browser-side inversion.
  invertY: false,
  deadzone: 0.08,
  responseExponent: 1.3,
  maxSpeedX: 600,
  maxSpeedY: 600,
  triggerButton: 0,
  zoomButton: 1,
}

function loadProfile(): GamepadProfile {
  if (typeof window === 'undefined') return { ...DEFAULT_PROFILE }
  try {
    const parsed = JSON.parse(window.localStorage.getItem(STORAGE_KEY) ?? '{}') as Partial<GamepadProfile>
    return sanitizeProfile({ ...DEFAULT_PROFILE, ...parsed })
  } catch {
    return { ...DEFAULT_PROFILE }
  }
}

function sanitizeProfile(candidate: GamepadProfile): GamepadProfile {
  const index = (value: number, fallback: number) => Number.isFinite(value) ? Math.max(0, Math.min(31, Math.trunc(value))) : fallback
  const speed = (value: number) => Number.isFinite(value) ? Math.max(1, Math.min(1000, Math.trunc(value))) : 600
  return {
    enabled: Boolean(candidate.enabled),
    preferredDeviceId: String(candidate.preferredDeviceId ?? ''),
    axisX: index(candidate.axisX, 0),
    axisY: index(candidate.axisY, 1),
    invertX: Boolean(candidate.invertX),
    invertY: Boolean(candidate.invertY),
    deadzone: Number.isFinite(candidate.deadzone) ? Math.max(0, Math.min(0.45, candidate.deadzone)) : 0.10,
    responseExponent: Number.isFinite(candidate.responseExponent) ? Math.max(1, Math.min(3, candidate.responseExponent)) : 1.6,
    maxSpeedX: speed(candidate.maxSpeedX),
    maxSpeedY: speed(candidate.maxSpeedY),
    triggerButton: index(candidate.triggerButton, 0),
    zoomButton: index(candidate.zoomButton, 1),
  }
}

const profile = ref<GamepadProfile>(loadProfile())
const devices = ref<VisibleGamepad[]>([])
const activeIndex = ref<number | null>(null)
const rawAxisX = ref(0)
const rawAxisY = ref(0)
const triggerPressed = ref(false)
const zoomPressed = ref(false)
const lastInputAt = ref(0)
const supported = ref(typeof navigator !== 'undefined' && typeof navigator.getGamepads === 'function')
let animationFrame: number | null = null
let subscribers = 0

function visibleGamepads(): Gamepad[] {
  if (!supported.value) return []
  return Array.from(navigator.getGamepads?.() ?? []).filter((item): item is Gamepad => Boolean(item?.connected))
}

function isExtreme3D(id: string): boolean {
  return LOGITECH_EXTREME_3D_PATTERN.test(id)
}

function selectActive(list: Gamepad[]): Gamepad | null {
  const preferred = profile.value.preferredDeviceId
  return list.find((item) => preferred && item.id === preferred)
    ?? list.find((item) => isExtreme3D(item.id))
    ?? list[0]
    ?? null
}

function refreshDevices(): VisibleGamepad[] {
  const list = visibleGamepads()
  devices.value = list.map((item) => ({
    index: item.index,
    id: item.id,
    mapping: item.mapping || 'raw',
    axes: item.axes.length,
    buttons: item.buttons.length,
    connected: item.connected,
    logitechExtreme3D: isExtreme3D(item.id),
  }))
  const active = selectActive(list)
  activeIndex.value = active?.index ?? null
  if (active && !profile.value.preferredDeviceId && isExtreme3D(active.id)) {
    profile.value = { ...profile.value, preferredDeviceId: active.id }
    persistProfile()
  }
  return devices.value
}

function poll(): void {
  const list = visibleGamepads()
  const active = selectActive(list)
  if (active) {
    activeIndex.value = active.index
    rawAxisX.value = Number(active.axes[profile.value.axisX] ?? 0)
    rawAxisY.value = Number(active.axes[profile.value.axisY] ?? 0)
    const zoomBtn = profile.value.zoomButton ?? 1
    triggerPressed.value = Boolean(active.buttons[profile.value.triggerButton]?.pressed)
    zoomPressed.value = Boolean(
      active.buttons[zoomBtn]?.pressed ||
      active.buttons[1]?.pressed ||
      active.buttons[2]?.pressed
    )
    if (Math.abs(rawAxisX.value) > 0.015 || Math.abs(rawAxisY.value) > 0.015 || triggerPressed.value || zoomPressed.value) {
      lastInputAt.value = performance.now()
    }
  } else {
    activeIndex.value = null
    rawAxisX.value = 0
    rawAxisY.value = 0
    triggerPressed.value = false
    zoomPressed.value = false
  }
  animationFrame = window.requestAnimationFrame(poll)
}

function persistProfile(): void {
  if (typeof window === 'undefined') return
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify(profile.value))
}

function updateProfile(update: Partial<GamepadProfile>): void {
  profile.value = sanitizeProfile({ ...profile.value, ...update })
  persistProfile()
  refreshDevices()
}

function selectDevice(id: string): void {
  updateProfile({ preferredDeviceId: id })
}

function shapedAxis(value: number, invert: boolean, maximum: number): number {
  const deadzone = profile.value.deadzone
  const magnitude = Math.abs(Number.isFinite(value) ? value : 0)
  if (magnitude <= deadzone || !profile.value.enabled) return 0
  const normalized = Math.min(1, (magnitude - deadzone) / Math.max(0.001, 1 - deadzone))
  const shaped = MIN_OUTPUT_FRACTION + (1 - MIN_OUTPUT_FRACTION) * normalized ** profile.value.responseExponent
  const direction = Math.sign(value) * (invert ? -1 : 1)
  return Math.trunc(direction * shaped * maximum)
}

function scan(): VisibleGamepad[] {
  supported.value = typeof navigator.getGamepads === 'function'
  return refreshDevices()
}

function start(): void {
  subscribers += 1
  if (subscribers > 1 || typeof window === 'undefined') return
  supported.value = typeof navigator.getGamepads === 'function'
  window.addEventListener('gamepadconnected', refreshDevices)
  window.addEventListener('gamepaddisconnected', refreshDevices)
  refreshDevices()
  animationFrame = window.requestAnimationFrame(poll)
}

function stop(): void {
  subscribers = Math.max(0, subscribers - 1)
  if (subscribers > 0 || typeof window === 'undefined') return
  window.removeEventListener('gamepadconnected', refreshDevices)
  window.removeEventListener('gamepaddisconnected', refreshDevices)
  if (animationFrame !== null) window.cancelAnimationFrame(animationFrame)
  animationFrame = null
  rawAxisX.value = 0
  rawAxisY.value = 0
  triggerPressed.value = false
  zoomPressed.value = false
}

const activeDevice = computed(() => devices.value.find((item) => item.index === activeIndex.value) ?? null)
const speedX = computed(() => shapedAxis(rawAxisX.value, profile.value.invertX, profile.value.maxSpeedX))
const speedY = computed(() => shapedAxis(rawAxisY.value, profile.value.invertY, profile.value.maxSpeedY))
const moving = computed(() => speedX.value !== 0 || speedY.value !== 0)

export function useGamepadController() {
  return {
    profile,
    devices: readonly(devices),
    activeDevice,
    activeIndex: readonly(activeIndex),
    rawAxisX: readonly(rawAxisX),
    rawAxisY: readonly(rawAxisY),
    speedX,
    speedY,
    moving,
    triggerPressed: readonly(triggerPressed),
    zoomPressed: readonly(zoomPressed),
    lastInputAt: readonly(lastInputAt),
    supported: readonly(supported),
    scan,
    start,
    stop,
    updateProfile,
    selectDevice,
  }
}
