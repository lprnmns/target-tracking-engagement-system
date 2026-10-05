<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { runCommandPreflight, selectCommandProfile, connectGatewayPico, type GatewayPreflightResult } from '../api/safety'
import { discoverPico, testHardwareJog, testHardwareTrigger, type HardwareMotionTestResult } from '../api/hardware'
import { startCameraPreview } from '../api/vision'
import { applyDeviceProfile, fetchDeviceProfiles } from '../api/deviceProfiles'
import { useOperationalReadiness, type OperationalReadinessItem } from '../composables/useOperationalReadiness'
import { useDeviceRuntimeStore } from '../stores/deviceRuntimeStore'
import type { DeviceProfile } from '../types/deviceProfile'
import { preloadDigitalTwinAssets } from '../digitalTwin/preload'

const router = useRouter()
const readiness = useOperationalReadiness()
const runtime = useDeviceRuntimeStore()
const readinessItems = readiness.items
const readinessLoading = readiness.loading
const readinessError = readiness.error
const primaryBlocker = readiness.primaryBlocker
const busy = ref(false)
const feedback = ref('')
const profiles = ref<DeviceProfile[]>([])
const profilesLoading = ref(false)
const selectedProfileId = ref(localStorage.getItem('istiklal_active_profile_id') ?? '')
const startupPreflight = ref<GatewayPreflightResult | null>(null)
const startupWarnings = ref<string[]>([])
// `ACTUATOR_NOT_ARMED` is an internal, fail-closed Gateway state while the
// landing checks are still proving the camera/motion path.  It is not an
// operator action on this screen (the explicit empty-chamber trigger check
// owns the arm transition), so keep the machine code in the Gateway response
// but do not surface it as a misleading landing warning.
const HIDDEN_LANDING_REASON_CODES = new Set(['ACTUATOR_NOT_ARMED'])
type StartupCheckState = 'idle' | 'checking' | 'ready' | 'blocked'
type StartupCheckKey = 'camera' | 'pico' | 'motion' | 'trigger'
type DirectionKey = 'left' | 'right' | 'up' | 'down'
interface StartupCheck {
  title: string
  state: StartupCheckState
  detail: string
  reasonCode: string | null
}
const startupChecks = reactive<Record<StartupCheckKey, StartupCheck>>({
  camera: { title: 'Kamera', state: 'idle', detail: 'Profil seçildiğinde kamera kimliği aranır.', reasonCode: null },
  pico: { title: 'Pico bağlantısı', state: 'idle', detail: 'Profil seçildiğinde Pico portu aranır.', reasonCode: null },
  motion: { title: 'Yön testi', state: 'idle', detail: 'Sol, sağ, yukarı ve aşağı komutlarını gönderin.', reasonCode: null },
  trigger: { title: 'Tetik testi', state: 'idle', detail: 'İsteğe bağlıdır; canlı sistem başlatıldıktan sonra da çalıştırılabilir.', reasonCode: null },
})
const summaryCheckKeys: StartupCheckKey[] = ['camera', 'pico']
const directionTests = reactive<Record<DirectionKey, HardwareMotionTestResult | null>>({ left: null, right: null, up: null, down: null })
const lastMotionResult = ref<HardwareMotionTestResult | null>(null)
const lastTriggerResult = ref<HardwareMotionTestResult | null>(null)
const profileCheckBusy = ref(false)
const preflightOpen = ref(false)
let profileCheckSequence = 0
let startupFlowStartedAt = 0
const now = ref(new Date())
let timer: ReturnType<typeof setInterval> | null = null
let refreshTimer: ReturnType<typeof setInterval> | null = null

const timeLabel = computed(() => now.value.toLocaleTimeString('tr-TR', { hour: '2-digit', minute: '2-digit', second: '2-digit' }))
const selectedProfile = computed(() => profiles.value.find((item) => item.profile_id === selectedProfileId.value) ?? null)
const visiblePreflightReasonCodes = computed(() => (startupPreflight.value?.reason_codes ?? []).filter((code) => !HIDDEN_LANDING_REASON_CODES.has(code)))
const startupReady = computed(() => startupChecks.camera.state === 'ready'
  && startupChecks.pico.state === 'ready'
  && startupChecks.motion.state === 'ready')
const canRunHardwareChecks = computed(() => Boolean(selectedProfileId.value) && !profileCheckBusy.value && !busy.value)
const profileSummary = computed(() => {
  const current = selectedProfile.value
  if (!current) return 'Profil seçilmedi; mevcut çalışma ayarları kullanılacak.'
  const camera = current.selected_camera_name || current.camera_profile?.device_id || 'Kamera bekleniyor'
  const cameraMode = current.camera_profile
    ? `${current.camera_profile.width}×${current.camera_profile.height} @ ${current.camera_profile.fps} FPS`
    : 'kamera modu bekleniyor'
  const inference = current.vision_runtime_profile
    ? `${current.vision_runtime_profile.device.toUpperCase()} · YOLO ${current.vision_runtime_profile.imgsz}px`
    : 'algılama profili bekleniyor'
  const balloon = current.vision_config?.balloon_model_path?.split(/[\\/]/).pop() || 'Balon modeli kapalı'
  const body = current.vision_config?.body_model_path?.split(/[\\/]/).pop() || 'Hava aracı modeli kapalı'
  return `${camera} · ${cameraMode} · ${inference} · ${body} + ${balloon}`
})
const visiblePrimaryBlockerReason = computed(() => {
  const blocker = primaryBlocker.value
  if (!blocker || profileCheckBusy.value) return null
  if (blocker.key === 'camera' && startupChecks.camera.state === 'ready') return null
  if (blocker.key === 'pico_estop' && startupChecks.pico.state === 'ready') return null
  if (blocker.key === 'motion_actuator' && startupChecks.motion.state === 'ready') return null
  return visibleReasonCode(blocker.reasonCode)
})

function updateCheck(key: StartupCheckKey, state: StartupCheckState, detail: string, reasonCode: string | null = null): void {
  startupChecks[key] = { ...startupChecks[key], state, detail, reasonCode }
}

function visibleReasonCode(code: string | null | undefined): string | null {
  return code && !HIDDEN_LANDING_REASON_CODES.has(code) ? code : null
}

function visibleReasonCodes(codes: string[] | null | undefined): string[] {
  return (codes ?? []).filter((code) => !HIDDEN_LANDING_REASON_CODES.has(code))
}

function clearDirectionTests(): void {
  directionTests.left = null
  directionTests.right = null
  directionTests.up = null
  directionTests.down = null
  lastMotionResult.value = null
  lastTriggerResult.value = null
  updateCheck('motion', 'idle', 'Sol, sağ, yukarı ve aşağı komutlarını gönderin.', 'MOTION_TEST_REQUIRED')
  updateCheck('trigger', 'idle', 'İsteğe bağlıdır; canlı sistem başlatıldıktan sonra da çalıştırılabilir.', null)
}

function profileCameraDescription(profile: DeviceProfile): string {
  const camera = profile.selected_camera_name || profile.camera_profile?.device_id || 'kayıtlı kamera'
  const port = profile.camera_profile?.device_path || profile.selected_camera_id || 'port bilgisi yok'
  return `${camera} · ${port}`
}

function cameraIdentityMatches(profile: DeviceProfile): boolean {
  const active = runtime.cameraStatus.profile
  const expectedStable = profile.selected_camera_stable_path || profile.camera_profile?.stable_path
  const expectedId = profile.selected_camera_id || profile.camera_profile?.device_id
  const normalise = (value: string | null | undefined) => value?.replaceAll('\\', '/').trim().toLowerCase() ?? ''
  const usbSignature = (value: string | null | undefined) => {
    const match = value?.match(/vid_([0-9a-f]{4})&pid_([0-9a-f]{4})/i)
    return match ? `${match[1].toLowerCase()}:${match[2].toLowerCase()}` : ''
  }
  if (expectedStable) {
    const exact = normalise(active.stable_path) === normalise(expectedStable)
    const repluggedSameDevice = Boolean(
      usbSignature(active.stable_path)
      && usbSignature(active.stable_path) === usbSignature(expectedStable),
    )
    return exact || repluggedSameDevice
  }
  if (expectedId && active.device_id) return normalise(active.device_id) === normalise(expectedId)
  if (expectedId) return false
  return true
}

async function waitForCamera(profile: DeviceProfile, sequence: number): Promise<boolean> {
  updateCheck('camera', 'checking', `Kamera aranıyor: ${profileCameraDescription(profile)}`)
  const current = runtime.cameraStatus
  const currentFrameIsFresh = current.running
    && current.is_real_camera_evidence
    && current.last_frame_age_ms !== null
    && current.last_frame_age_ms < 1200
    && cameraIdentityMatches(profile)
  const currentLumaHealthy = current.frame_brightness_state === 'OK'
    && current.frame_mean_luma !== null
    && current.frame_mean_luma >= 18
    && current.frame_mean_luma <= 245
  // A fresh frame already owned by the selected camera is sufficient proof.
  // Waiting for one additional sequence used to add ~450 ms on every start.
  if (currentFrameIsFresh && currentLumaHealthy) {
    updateCheck('camera', 'ready', `Kamera hazır: ${current.selected_device ?? profileCameraDescription(profile)} · ${current.resolution} · frame ${current.frame_sequence}`, null)
    return true
  }
  // Capture the sequence before starting a stopped worker.  If start_preview
  // publishes its first frame while the request is in flight, that frame is
  // accepted instead of forcing the UI to wait for a second one.
  const baselineFrameSequence = current.frame_sequence
  if (!currentFrameIsFresh) {
    try {
      await startCameraPreview()
    } catch {
      // The status poll below contains the authoritative error and reason code.
    }
  }
  for (let attempt = 0; attempt < 25; attempt += 1) {
    if (sequence !== profileCheckSequence) return false
    await runtime.refresh()
    const status = runtime.cameraStatus
    const frameAdvanced = status.frame_sequence > baselineFrameSequence
    const fresh = status.running
      && status.is_real_camera_evidence
      && frameAdvanced
      && status.last_frame_age_ms !== null
      && status.last_frame_age_ms < 1200
    const lumaHealthy = status.frame_brightness_state === 'OK'
      && status.frame_mean_luma !== null
      && status.frame_mean_luma >= 18
      && status.frame_mean_luma <= 245
    if (cameraIdentityMatches(profile) && fresh && lumaHealthy) {
      updateCheck('camera', 'ready', `Bulundu ve sağlıklı: ${status.selected_device ?? profileCameraDescription(profile)} · ${status.resolution} · frame ${status.frame_sequence}`, null)
      return true
    }
    const progress = !cameraIdentityMatches(profile)
      ? `Profil kimliği eşleşmedi · beklenen ${profileCameraDescription(profile)} · bulunan ${status.selected_device ?? 'bilinmiyor'}`
      : !frameAdvanced
        ? `Yeni frame bekleniyor · son kare #${status.frame_sequence}`
        : status.last_frame_age_ms === null
      ? 'Gerçek frame bekleniyor…'
      : status.frame_brightness_state === 'DARK'
        ? `Frame geldi; görüntü çok karanlık (luma ${status.frame_mean_luma ?? 'n/a'})`
        : status.frame_brightness_state === 'OVEREXPOSED'
          ? `Frame geldi; görüntü aşırı parlak (luma ${status.frame_mean_luma ?? 'n/a'})`
        : `Frame doğrulanıyor · ${status.resolution} · ${status.last_frame_age_ms} ms`
    updateCheck('camera', 'checking', `Kamera bulundu, sağlık kontrolü sürüyor: ${progress}`)
    await wait(120)
  }
  const status = runtime.cameraStatus
  const dark = status.frame_brightness_state === 'DARK' || (status.frame_mean_luma !== null && status.frame_mean_luma < 18)
  const identityMismatch = !cameraIdentityMatches(profile)
  const reason = identityMismatch
    ? 'Profildeki kamera kimliği bu bilgisayarda bulunamadı veya farklı bir cihaz açıldı.'
    : dark
      ? 'Frame geliyor ancak görüntü karanlık; kamera ışığı/pozlaması kontrol edilmeli.'
      : status.frame_brightness_state === 'OVEREXPOSED'
        ? 'Frame geliyor ancak görüntü aşırı parlak; kamera pozlaması kontrol edilmeli.'
        : 'Kameradan ilerleyen, güncel ve kullanılabilir frame alınamadı.'
  const code = identityMismatch ? 'CAMERA_IDENTITY_MISMATCH' : dark ? 'CAMERA_FRAME_DARK' : status.frame_brightness_state === 'OVEREXPOSED' ? 'CAMERA_FRAME_OVEREXPOSED' : 'CAMERA_FRAME_UNAVAILABLE'
  updateCheck('camera', 'blocked', reason, code)
  return false
}

async function verifyPico(profile: DeviceProfile, sequence: number): Promise<boolean> {
  updateCheck('pico', 'checking', `Pico aranıyor: ${profile.selected_pico_port || 'profil portu yok; otomatik tarama başlıyor'}`)
  await runtime.refreshInventory()
  let port = profile.selected_pico_port
  // A profile saved while the serial handle was temporarily reconnecting may
  // have no port string even though Device Manager already sees one verified
  // Pico. Prefer that live inventory truth before opening the same COM port a
  // second time through the slower discovery probe (Windows reports it busy).
  if (!port) {
    const verified = runtime.inventory.pico_candidates.filter((item) => (
      item.permissions_ok
      && (item.identity_verified || item.connected)
    ))
    if (verified.length === 1) port = verified[0].device_path
  }
  const candidate = port ? runtime.inventory.pico_candidates.find((item) => item.device_path === port) : null
  if (!candidate) {
    const discovered = await discoverPico()
    if (discovered.found && discovered.port) port = discovered.port
    else {
      updateCheck('pico', 'blocked', discovered.detail, discovered.reason_code || 'PICO_NOT_FOUND')
      return false
    }
  }
  if (sequence !== profileCheckSequence || !port) return false
  const connected = await connectGatewayPico(port, profile.selected_pico_baudrate || 460800)
  if (!connected.connected) {
    updateCheck('pico', 'blocked', `Port bulundu (${port}) ancak bağlantı kurulamadı: ${connected.reason_code}`, connected.reason_code || 'PICO_CONNECT_FAILED')
    return false
  }
  // A fresh page starts in DRY_RUN.  Calling /preflight directly in that
  // state only reaffirms DRY_RUN_ACTIVE and never exercises the Pico.  Select
  // the visible motion-authority profile first; this performs the safe
  // PING/STAT/ARM,0 handshake without arming FIRE.
  const preflight = await selectCommandProfile('LIVE_TEST', false)
  startupPreflight.value = preflight
  const handshake = preflight.gates.find((gate) => gate.code === 'PICO_HANDSHAKE_OK')?.ready
  const estop = preflight.gates.find((gate) => gate.code === 'ESTOP_RELEASED')?.ready
  if (!handshake || !estop) {
    const code = preflight.reason_codes[0] || 'PICO_HEARTBEAT_STALE'
    updateCheck('pico', 'blocked', `Pico bulundu (${port}) ancak handshake/heartbeat doğrulanamadı: ${preflight.reason_codes.join(' · ') || code}`, code)
    return false
  }
  updateCheck('pico', 'ready', `Pico bulundu: ${port} · handshake OK · heartbeat OK`, null)
  return true
}

async function validateSelectedProfile(profileId = selectedProfileId.value): Promise<void> {
  const profile = profiles.value.find((item) => item.profile_id === profileId)
  if (!profile) return
  const sequence = ++profileCheckSequence
  profileCheckBusy.value = true
  feedback.value = ''
  startupWarnings.value = []
  startupPreflight.value = null
  clearDirectionTests()
  updateCheck('camera', 'checking', `Profil uygulanıyor; ${profileCameraDescription(profile)} aranıyor…`)
  updateCheck('pico', 'checking', `Profilde kayıtlı Pico aranıyor…`)
  try {
    const applied = await applyDeviceProfile(profile.profile_id, true)
    if (!applied.accepted) throw new Error(applied.reason)
    startupWarnings.value = applied.warnings
    localStorage.setItem('istiklal_active_profile_id', applied.profile.profile_id)
    await runtime.refresh()
    let cameraOk = false
    const cameraWarning = applied.warnings.find((warning) => /camera|kamera|PROFILE_CAMERA/i.test(warning))
    const hasCameraBinding = Boolean(
      profile.selected_camera_stable_path
      || profile.selected_camera_id
      || profile.camera_profile?.stable_path
      || profile.camera_profile?.device_id
      || profile.camera_profile?.device_path,
    )
    if (!hasCameraBinding) {
      updateCheck('camera', 'blocked', 'Bu profil için doğrulanmış kamera kimliği kayıtlı değil; Kurulum ve ayarlar bölümünden kamera profili kaydedin.', 'PROFILE_CAMERA_NOT_CONFIGURED')
    } else if (cameraWarning) {
      updateCheck('camera', 'blocked', `Profil kamerası uygulanamadı: ${cameraWarning}`, 'PROFILE_CAMERA_NOT_FOUND')
    } else {
      try {
        cameraOk = await waitForCamera(profile, sequence)
      } catch (caught) {
        const detail = caught instanceof Error ? caught.message : 'Kamera sağlık kontrolü başarısız.'
        updateCheck('camera', 'blocked', detail, 'CAMERA_HEALTH_CHECK_FAILED')
      }
    }
    if (sequence !== profileCheckSequence) return
    let picoOk = false
    try {
      picoOk = await verifyPico(profile, sequence)
    } catch (caught) {
      const detail = caught instanceof Error ? caught.message : 'Pico doğrulaması başarısız.'
      updateCheck('pico', 'blocked', detail, 'PICO_HEALTH_CHECK_FAILED')
    }
    if (sequence !== profileCheckSequence) return
    if (cameraOk && picoOk) {
      const preflight = await selectCommandProfile('LIVE_TEST', false)
      startupPreflight.value = preflight
      if (preflight.physical_motion_enabled) updateCheck('motion', 'checking', 'Pico hazır. Sol, sağ, yukarı ve aşağı testlerini gönderin.', 'MOTION_TEST_REQUIRED')
      else updateCheck('motion', 'blocked', `Hareket ön kontrolü hazır değil: ${preflight.reason_codes.join(' · ')}`, preflight.reason_codes[0] || 'MOTION_PREFLIGHT_FAILED')
    } else {
      updateCheck('motion', 'blocked', 'Kamera ve Pico doğrulanmadan hareket testi açılamaz.', 'PREFLIGHT_DEPENDENCY_MISSING')
    }
    await readiness.refresh()
  } catch (caught) {
    const detail = caught instanceof Error ? caught.message : 'Profil doğrulanamadı.'
    updateCheck('camera', 'blocked', detail, 'PROFILE_APPLY_FAILED')
    updateCheck('pico', 'blocked', 'Profil uygulanamadığı için Pico aranamadı.', 'PROFILE_APPLY_FAILED')
    updateCheck('motion', 'blocked', 'Önce profil cihazlarını doğrulayın.', 'PROFILE_APPLY_FAILED')
    feedback.value = detail
  } finally {
    if (sequence === profileCheckSequence) profileCheckBusy.value = false
  }
}

async function runDirectionTest(direction: DirectionKey, speedX: number, speedY: number, label: string): Promise<void> {
  if (startupChecks.pico.state !== 'ready' || startupChecks.camera.state !== 'ready') {
    updateCheck('motion', 'blocked', 'Önce kamera ve Pico doğrulamasını tamamlayın.', 'PREFLIGHT_DEPENDENCY_MISSING')
    return
  }
  updateCheck('motion', 'checking', `${label} hareket komutu gönderiliyor…`)
  const result = await testHardwareJog({ speed_x: speedX, speed_y: speedY, duration_ms: 500 })
  directionTests[direction] = result
  lastMotionResult.value = result
  const picoAckPresent = Boolean(result.driver_ack || (result.pico_response && /ACK|OK[, ]/i.test(result.pico_response)))
  const passed = motionResultPassed(result)
  const count = (Object.values(directionTests) as Array<HardwareMotionTestResult | null>).filter(motionResultPassed).length
  if (!passed) {
    const reason = result.reason_codes.join(' · ') || (!picoAckPresent ? 'PICO_ACK_MISSING' : !result.safe_stop_response ? 'SAFE_STOP_ACK_MISSING' : result.message)
    updateCheck('motion', 'blocked', `${label} testi başarısız: ${reason}`, result.reason_codes[0] || (!picoAckPresent ? 'PICO_ACK_MISSING' : !result.safe_stop_response ? 'SAFE_STOP_ACK_MISSING' : 'MOTION_TEST_FAILED'))
    return
  }
  if (count === 4) updateCheck('motion', 'ready', `4/4 yön testi geçti · son komut: ${label} · Pico ACK alındı`, null)
  else updateCheck('motion', 'checking', `${count}/4 yön testi geçti · ${label} başarılı; kalan yönleri gönderin.`, 'MOTION_TEST_REQUIRED')
}

async function runTriggerCheck(): Promise<void> {
  if (startupChecks.motion.state !== 'ready') {
    updateCheck('trigger', 'blocked', 'Önce dört yön testini tamamlayın.', 'MOTION_TEST_REQUIRED')
    return
  }
  updateCheck('trigger', 'checking', 'Pico tetik ve boş hazne ACK testi gönderiliyor…')
  try {
    const armed = await runCommandPreflight(true)
    startupPreflight.value = armed
    if (!armed.physical_fire_enabled) {
      const reasons = visibleReasonCodes(armed.reason_codes)
      updateCheck('trigger', 'blocked', `Tetik ön kontrolü hazır değil${reasons.length ? `: ${reasons.join(' · ')}` : '.'}`, reasons[0] || 'FIRE_PREFLIGHT_FAILED')
      return
    }
    const result = await testHardwareTrigger()
    lastTriggerResult.value = result
    if (!result.accepted || !result.command_sent || !result.pico_response || !result.driver_ack) {
      const reasons = visibleReasonCodes(result.reason_codes)
      const reason = reasons.join(' · ') || (!result.pico_response || !result.driver_ack ? 'PICO_ACK_MISSING' : result.message)
      updateCheck('trigger', 'blocked', `Tetik testi başarısız: ${reason}`, reasons[0] || 'PICO_ACK_MISSING')
      return
    }
    updateCheck('trigger', 'ready', `Boş hazne tetik testi geçti · ${result.command ?? 'komut'} · Pico ACK alındı`, null)
  } catch (caught) {
    const detail = caught instanceof Error ? caught.message : 'Tetik testi başarısız.'
    updateCheck('trigger', 'blocked', detail, 'FIRE_PREFLIGHT_FAILED')
  }
}

function openFix(item: OperationalReadinessItem): void {
  if (item.action === 'refresh') {
    void readiness.refresh()
    return
  }
  const step = item.action === 'setup-camera' ? 'hardware' : item.action === 'setup-pico' ? 'hardware' : 'control'
  void router.push(`/setup?intent=live&step=${step}`)
}

function startupCheckFor(item: OperationalReadinessItem): StartupCheck | null {
  if (item.key === 'camera') return startupChecks.camera
  if (item.key === 'pico_estop') return startupChecks.pico
  return null
}

function panelState(item: OperationalReadinessItem): string {
  const check = startupCheckFor(item)
  if (!check || check.state === 'idle') return item.state.toLowerCase()
  return check.state === 'ready' ? 'ready' : check.state === 'blocked' ? 'blocked' : 'checking'
}

function panelMessage(item: OperationalReadinessItem): string {
  return startupCheckFor(item)?.detail || item.message
}

function panelReason(item: OperationalReadinessItem): string | null {
  return visibleReasonCode(startupCheckFor(item)?.reasonCode || item.reasonCode)
}

function openSetup(): void { void router.push('/setup?intent=live') }

function openPreflight(): void {
  preflightOpen.value = true
}

function closePreflight(): void {
  preflightOpen.value = false
}

function wait(milliseconds: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds))
}

function motionResultPassed(result: HardwareMotionTestResult | null): boolean {
  if (!result) return false
  const picoAckPresent = Boolean(result.driver_ack || (result.pico_response && /ACK|OK[, ]/i.test(result.pico_response)))
  return result.accepted && result.command_sent && picoAckPresent && Boolean(result.safe_stop_response)
}

async function prepareGateway(actuatorArm: boolean): Promise<GatewayPreflightResult> {
  let result = await selectCommandProfile('LIVE_TEST', actuatorArm)
  startupPreflight.value = result
  let cameraRecoveryAttempted = false
  const transientCodes = new Set(['CAMERA_STALE', 'PICO_HEARTBEAT_STALE', 'PICO_CONNECTION_FAULT', 'ESTOP_STATE_UNKNOWN'])
  for (let attempt = 0; attempt < 3; attempt += 1) {
    const authorized = actuatorArm ? result.physical_fire_enabled : result.physical_motion_enabled
    if (authorized) return result
    if (result.reason_codes.includes('CAMERA_STALE') && !cameraRecoveryAttempted) {
      cameraRecoveryAttempted = true
      const profile = selectedProfile.value
      if (profile) await waitForCamera(profile, profileCheckSequence)
      else {
        try { await startCameraPreview() } catch { /* next preflight exposes the authoritative reason */ }
      }
      continue
    }
    // Only retry conditions that can change while the worker/heartbeat warms.
    // Hard safety/configuration failures return immediately with their exact
    // Gateway reason code instead of adding several opaque seconds.
    if (!result.reason_codes.some((code) => transientCodes.has(code))) return result
    await wait(180 * (attempt + 1))
    result = await runCommandPreflight(actuatorArm)
    startupPreflight.value = result
  }
  return result
}

async function continueFlow(): Promise<void> {
  busy.value = true
  startupFlowStartedAt = performance.now()
  feedback.value = ''
  startupWarnings.value = []
  try {
    if (!selectedProfileId.value) throw new Error('Önce bir kurulum profili seçin veya Kurulum ve ayarlar bölümünden profil oluşturun.')
    if (!startupReady.value) {
      await validateSelectedProfile(selectedProfileId.value)
      if (!startupReady.value) throw new Error('Ön kontrol tamamlanmadı. Kamera, Pico ve dört yön testini tamamlayın.')
    }
    // The selected profile was already applied and accepted by the visible
    // camera/Pico checks above. Re-applying it here used to release the same
    // Windows capture owner a second time and was the dominant startup delay.
    // waitForCamera below remains a lightweight fresh-frame guard and still
    // recovers a genuinely stopped worker.
    const profile = profiles.value.find((item) => item.profile_id === selectedProfileId.value)
    if (!profile || !(await waitForCamera(profile, profileCheckSequence))) {
      feedback.value = 'Kamera yeniden başlatıldı ancak güncel frame doğrulanamadı. Kamera kontrolünü yenileyip tekrar deneyin.'
      await readiness.refresh()
      return
    }

    // Landing completes the visible live acceptance path, including the
    // empty-chamber trigger ACK. Cockpit still exposes the TEST/LIVE toggle so
    // the operator can disarm or re-arm the trigger at any time.
    const preflight = await prepareGateway(true)
    const authorized = preflight.physical_fire_enabled
    if (!authorized) {
      const codes = visibleReasonCodes(preflight.reason_codes)
      feedback.value = codes.length
        ? `Sistem henüz hazır değil: ${codes.join(' · ')}`
        : 'Sistem henüz hazır değil. Aşağıdaki ön kontrol maddelerini düzeltip yeniden deneyin.'
      await readiness.refresh()
      return
    }

    localStorage.setItem('istiklal_startup_intent', 'LIVE_HARDWARE')
    const elapsed = Math.round(performance.now() - startupFlowStartedAt)
    console.info('[ISTIKLAL] live startup ready', { profile: selectedProfileId.value, elapsed_ms: elapsed })
    await router.push('/cockpit?live=1&fire=1&autotrack=1')
  } catch (caught) {
    feedback.value = caught instanceof Error ? caught.message : 'Mod seçimi uygulanamadı.'
  } finally {
    busy.value = false
  }
}

async function loadProfiles(): Promise<void> {
  profilesLoading.value = true
  try {
    const previousSelectedId = selectedProfileId.value
    const result = await fetchDeviceProfiles()
    profiles.value = result.profiles.filter((item) => item.updated_at > 0)
    // The backend profile is the machine-wide runtime authority. Browser
    // localStorage can survive weeks and previously resurrected the obsolete
    // 640x480/one-model profile after the verified 1080p profile was saved.
    const requestedId = result.active_profile_id || selectedProfileId.value
    const selected = profiles.value.find((item) => item.profile_id === requestedId)
    if (selected) {
      selectedProfileId.value = selected.profile_id
      localStorage.setItem('istiklal_active_profile_id', selected.profile_id)
    } else if (selectedProfileId.value) {
      selectedProfileId.value = ''
      localStorage.removeItem('istiklal_active_profile_id')
    }
    // If the persisted id was already selected before the profile list
    // arrived, the watcher has no value transition to observe; validate it
    // explicitly. A newly assigned active id is handled by the watcher.
    if (selectedProfileId.value && selectedProfileId.value === previousSelectedId) {
      void validateSelectedProfile(selectedProfileId.value)
    }
  } catch (caught) {
    feedback.value = caught instanceof Error ? caught.message : 'Profiller yüklenemedi.'
  } finally {
    profilesLoading.value = false
  }
}

watch(selectedProfileId, (profileId) => {
  const selected = profiles.value.find((item) => item.profile_id === profileId)
  if (!selected) return
  localStorage.setItem('istiklal_active_profile_id', selected.profile_id)
  feedback.value = ''
  startupPreflight.value = null
  startupWarnings.value = []
  void validateSelectedProfile(selected.profile_id)
})

onMounted(() => {
  void readiness.refresh()
  void loadProfiles()
  // Warm the operator Three.js chunk and optimized GLB only during browser
  // idle time. This never starts hardware and never blocks live preflight.
  preloadDigitalTwinAssets()
  timer = setInterval(() => { now.value = new Date() }, 1000)
  refreshTimer = setInterval(() => { void readiness.refresh() }, 3000)
})

onBeforeUnmount(() => {
  if (timer) clearInterval(timer)
  if (refreshTimer) clearInterval(refreshTimer)
})
</script>

<template>
  <main class="landing">
    <img class="background" src="/assets/startup/ilk_acilis_ekrani.png?v=20260716" alt="İSTİKLAL taret sistemi" />
    <div class="veil" />
    <header class="header">
      <div>
        <p class="eyebrow">İSTİKLAL</p>
        <h1>Hava Savunma Sistemi</h1>
      </div>
      <div class="header-actions">
        <button class="setup-link" type="button" @click="openSetup">Kurulum ve ayarlar</button>
        <div class="clock" aria-label="Sistem saati">
          <b>{{ timeLabel }}</b>
          <span>{{ readinessItems[0]?.state === 'READY' ? 'MERKEZ SİSTEM BAĞLI' : 'MERKEZ SİSTEM BEKLİYOR' }}</span>
        </div>
      </div>
    </header>

    <section class="content">
      <aside class="readiness-card" aria-label="Sistem hazırlığı" @click="openPreflight">
        <div class="card-heading">
          <div>
            <p class="eyebrow">CANLI DURUM</p>
            <h2>Sistem durumu</h2>
            <small class="card-hint">Başlangıç ön kontrolünü aç</small>
          </div>
          <button class="refresh" type="button" :disabled="readinessLoading" @click.stop="readiness.refresh()">
            {{ readinessLoading ? 'Kontrol ediliyor' : 'Yenile' }}
          </button>
        </div>
        <button v-for="item in readinessItems" :key="item.key" class="readiness-row" type="button" @click.stop="openFix(item)">
          <span class="state-dot" :class="panelState(item)" />
          <span class="row-copy"><b>{{ item.title }}</b><small>{{ panelMessage(item) }}</small></span>
          <span class="row-action">{{ panelState(item) === 'checking' ? 'Kontrol ediliyor' : panelState(item) === 'ready' ? 'Ayrıntı' : item.action === 'refresh' ? 'Yenile' : 'Düzelt' }}</span>
          <code v-if="panelReason(item)">{{ panelReason(item) }}</code>
        </button>
        <p v-if="readinessError" class="error">{{ readinessError }}</p>
      </aside>

      <section class="decision" aria-label="Canlı sistemi başlat">
        <p class="eyebrow">CANLI SİSTEM</p>
        <h2>Operasyona hazırla</h2>
        <p class="decision-copy">Canlı sistem varsayılan akıştır. Kamera, Pico ve yön testleri tamamlandığında cockpit açılır; tetik testi isteğe bağlıdır ve cockpit içinde de çalıştırılabilir.</p>
        <div class="profile-picker">
          <div><b>Kurulum profili</b><small>{{ profileSummary }}</small></div>
          <select v-model="selectedProfileId" :disabled="profilesLoading">
            <option value="">{{ profilesLoading ? 'Profiller yükleniyor…' : 'Bir profil seçin' }}</option>
            <option v-for="item in profiles" :key="item.profile_id" :value="item.profile_id">{{ item.display_name }}</option>
          </select>
        </div>
        <div class="selection-summary">
          <span>Canlı sistem varsayılan · tetik cockpit içinde TEST/CANLI değiştirilebilir</span>
          <code v-if="visiblePreflightReasonCodes.length">{{ visiblePreflightReasonCodes.join(' · ') }}</code>
          <code v-else-if="visiblePrimaryBlockerReason">{{ visiblePrimaryBlockerReason }}</code>
        </div>
        <p class="decision-hint">Başlangıç ön kontrolünü görmek için sağdaki <b>CANLI DURUM</b> kartına tıklayın.</p>
        <p v-if="startupWarnings.length" class="warning">{{ startupWarnings.join(' · ') }}</p>
        <p v-if="feedback" class="error">{{ feedback }}</p>
      </section>
    </section>

    <Teleport to="body">
      <div v-if="preflightOpen" class="preflight-backdrop" role="presentation" @click.self="closePreflight" @keydown.esc="closePreflight">
        <section class="preflight-modal" role="dialog" aria-modal="true" aria-labelledby="preflight-title">
          <header class="preflight-modal-head">
            <div>
              <p class="eyebrow">BAŞLANGIÇ ÖN KONTROLÜ</p>
              <h2 id="preflight-title">Canlı sistemi hazırla</h2>
              <p>Kayıtlı profilin kamerası ve Pico bağlantısı aranır; ardından dört güvenli yön komutu ACK ile doğrulanır.</p>
            </div>
            <button class="modal-close" type="button" aria-label="Ön kontrolü kapat" @click="closePreflight">×</button>
          </header>

          <div class="modal-profile-line">
            <span>Aktif profil</span>
            <b>{{ selectedProfile?.display_name ?? 'Profil seçilmedi' }}</b>
            <button class="check-refresh" type="button" :disabled="!selectedProfileId || profileCheckBusy" @click="validateSelectedProfile()">
              {{ profileCheckBusy ? 'Kontrol ediliyor…' : 'Yeniden doğrula' }}
            </button>
          </div>

          <div class="check-grid">
            <article v-for="key in summaryCheckKeys" :key="key" class="startup-check" :class="startupChecks[key].state">
              <span class="check-dot" />
              <div><b>{{ startupChecks[key].title }}</b><small>{{ startupChecks[key].detail }}</small><code v-if="visibleReasonCode(startupChecks[key].reasonCode)">{{ visibleReasonCode(startupChecks[key].reasonCode) }}</code></div>
            </article>
          </div>
          <article class="startup-check wide" :class="startupChecks.motion.state">
            <span class="check-dot" />
            <div class="wide-check-copy"><b>Yön testi</b><small>{{ startupChecks.motion.detail }}</small><code v-if="visibleReasonCode(startupChecks.motion.reasonCode)">{{ visibleReasonCode(startupChecks.motion.reasonCode) }}</code></div>
            <div class="direction-actions">
              <button type="button" :disabled="!canRunHardwareChecks || startupChecks.pico.state !== 'ready'" @click="runDirectionTest('left', -140, 0, 'Sol')">← Sol <em v-if="directionTests.left">{{ directionTests.left.accepted ? 'ACK' : 'HATA' }}</em></button>
              <button type="button" :disabled="!canRunHardwareChecks || startupChecks.pico.state !== 'ready'" @click="runDirectionTest('right', 140, 0, 'Sağ')">Sağ → <em v-if="directionTests.right">{{ directionTests.right.accepted ? 'ACK' : 'HATA' }}</em></button>
              <button type="button" :disabled="!canRunHardwareChecks || startupChecks.pico.state !== 'ready'" @click="runDirectionTest('up', 0, 140, 'Yukarı')">↑ Yukarı <em v-if="directionTests.up">{{ directionTests.up.accepted ? 'ACK' : 'HATA' }}</em></button>
              <button type="button" :disabled="!canRunHardwareChecks || startupChecks.pico.state !== 'ready'" @click="runDirectionTest('down', 0, -140, 'Aşağı')">↓ Aşağı <em v-if="directionTests.down">{{ directionTests.down.accepted ? 'ACK' : 'HATA' }}</em></button>
            </div>
            <div v-if="lastMotionResult" class="hardware-result"><b>{{ lastMotionResult.accepted ? 'HAREKET KABUL EDİLDİ' : 'HAREKET ENGELLENDİ' }}</b><span>Gönderildi: {{ lastMotionResult.command_sent ? 'EVET' : 'HAYIR' }} · {{ lastMotionResult.command ?? 'komut yok' }}</span><span>Pico: {{ lastMotionResult.pico_response ?? 'yanıt yok' }} · ACK: {{ lastMotionResult.driver_ack ?? 'yok' }}</span><code v-if="visibleReasonCodes(lastMotionResult.reason_codes).length">{{ visibleReasonCodes(lastMotionResult.reason_codes).join(' · ') }}</code></div>
          </article>

          <article class="startup-check wide trigger-check" :class="startupChecks.trigger.state">
            <span class="check-dot" />
            <div class="wide-check-copy"><b>Tetik testi</b><small>{{ startupChecks.trigger.detail }}</small><code v-if="visibleReasonCode(startupChecks.trigger.reasonCode)">{{ visibleReasonCode(startupChecks.trigger.reasonCode) }}</code></div>
            <button class="trigger-check-button" type="button" :disabled="!canRunHardwareChecks || startupChecks.motion.state !== 'ready' || startupChecks.pico.state !== 'ready'" @click="runTriggerCheck">
              {{ startupChecks.trigger.state === 'checking' ? 'Kontrol ediliyor…' : 'Tetik testi' }}
            </button>
            <div v-if="lastTriggerResult" class="hardware-result"><b>{{ lastTriggerResult.accepted ? 'TETİK ACK ALINDI' : 'TETİK ENGELLENDİ' }}</b><span>Gönderildi: {{ lastTriggerResult.command_sent ? 'EVET' : 'HAYIR' }} · {{ lastTriggerResult.command ?? 'komut yok' }}</span><span>Pico: {{ lastTriggerResult.pico_response ?? 'yanıt yok' }} · ACK: {{ lastTriggerResult.driver_ack ?? 'yok' }}</span><code v-if="visibleReasonCodes(lastTriggerResult.reason_codes).length">{{ visibleReasonCodes(lastTriggerResult.reason_codes).join(' · ') }}</code></div>
          </article>

          <div v-if="visiblePreflightReasonCodes.length" class="preflight-reasons">
            <b>Kontrol notu</b><code>{{ visiblePreflightReasonCodes.join(' · ') }}</code>
          </div>
          <p v-if="startupWarnings.length" class="warning">{{ startupWarnings.join(' · ') }}</p>
          <p v-if="feedback" class="error">{{ feedback }}</p>
          <footer class="preflight-modal-foot">
            <span>Başlatma için kamera, Pico ve 4/4 yön testi yeşil olmalı. Tetik testi isteğe bağlıdır.</span>
            <button class="continue" type="button" :disabled="busy || profileCheckBusy || !startupReady" @click="continueFlow">
              {{ busy ? 'Sistem başlatılıyor…' : 'Sistemi başlat' }}
            </button>
          </footer>
        </section>
      </div>
    </Teleport>
  </main>
</template>

<style scoped>
.landing{position:relative;min-height:100vh;overflow:hidden;background:#020812;color:#edf8ff;font-family:Inter,ui-sans-serif,system-ui,sans-serif}.background,.veil{position:absolute;inset:0;width:100%;height:100%;object-fit:cover}.background{opacity:.96;object-position:center}.veil{background:radial-gradient(ellipse at 55% 44%,rgba(4,11,20,.04) 0%,rgba(2,8,16,.18) 48%,rgba(1,7,16,.82) 100%),linear-gradient(90deg,rgba(1,7,16,.66),transparent 52%,rgba(1,7,16,.35));pointer-events:none}.header,.content{position:relative;z-index:1}.header{display:flex;align-items:flex-start;justify-content:space-between;padding:38px clamp(24px,5vw,72px)}.eyebrow{margin:0;color:#62e8ff;font-size:.7rem;font-weight:900;letter-spacing:.25em}.header h1{margin:7px 0 4px;font-size:clamp(1.8rem,3vw,3.2rem);text-transform:uppercase;letter-spacing:.02em}.header-actions{display:flex;align-items:center;gap:10px}.setup-link{border:1px solid #70dff455;border-radius:10px;background:#071b2ac9;color:#c5f6ff;padding:11px 13px;font-size:.75rem;font-weight:900}.clock{display:grid;gap:4px;min-width:180px;padding:13px 16px;border:1px solid #5ec4d744;border-radius:14px;background:#061321c9;text-align:right}.clock b{font-size:1.2rem;letter-spacing:.08em}.clock span{color:#75dcec;font-size:.68rem;font-weight:800;letter-spacing:.12em}.content{display:grid;grid-template-columns:1fr minmax(350px,410px);grid-template-rows:1fr auto;gap:24px;min-height:calc(100vh - 145px);padding:0 clamp(24px,5vw,72px) 32px}.readiness-card,.decision{border:1px solid #65cbdf35;border-radius:18px;background:#06101dcc;box-shadow:0 22px 65px #0008;backdrop-filter:blur(14px)}.readiness-card{grid-column:2;grid-row:1;align-self:start;margin-top:4px;padding:20px}.card-heading{display:flex;justify-content:space-between;align-items:center;margin-bottom:12px}.card-heading h2{margin:5px 0;font-size:1.3rem}.refresh{border:1px solid #80dff644;border-radius:9px;background:#0b2335;color:#b7f3ff;padding:8px 10px;font-weight:800}.readiness-row{display:grid;grid-template-columns:9px 1fr auto;gap:10px;align-items:center;width:100%;padding:15px 4px;border:0;border-top:1px solid #ffffff10;background:transparent;color:inherit;text-align:left;cursor:pointer}.readiness-row code{grid-column:2/4;color:#ffc66d;font-size:.62rem}.state-dot{width:9px;height:9px;border-radius:99px;background:#8da0b4}.state-dot.ready{background:#4fe39d;box-shadow:0 0 14px #4fe39d}.state-dot.blocked{background:#fb6974;box-shadow:0 0 14px #fb6974}.state-dot.checking{background:#f5bd55;box-shadow:0 0 14px #f5bd55}.state-dot.degraded,.state-dot.unknown{background:#f5bd55}.row-copy{display:grid;gap:3px}.row-copy b{font-size:.9rem}.row-copy small{color:#a6bacb;font-size:.74rem;line-height:1.35}.row-action{color:#79e7fb;font-size:.7rem;font-weight:800}.decision{grid-column:1/-1;grid-row:2;justify-self:center;width:min(660px,calc(100vw - 48px));padding:16px 20px}.mode-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:10px}.mode-card{display:grid;grid-template-columns:auto 1fr;column-gap:9px;align-content:center;min-height:94px;padding:13px;border:1px solid #ffffff20;border-radius:13px;background:#071827cc;color:#e7f6ff;text-align:left;cursor:pointer;transition:.18s}.mode-card:hover,.mode-card.selected{border-color:#69e5fb;background:#0b2a3bbd}.mode-card.live.selected{border-color:#ffbd5b;background:#302113cc}.mode-icon{grid-row:span 3;font-size:1.15rem;color:#72e9ff}.mode-card.live .mode-icon{color:#ffbc59}.mode-card b{font-size:.9rem;letter-spacing:.08em}.mode-card small{margin-top:4px;color:#b9c8d4;font-size:.72rem}.mode-card em{margin-top:5px;font-style:normal;color:#7deab4;font-size:.66rem;font-weight:800}.mode-card.live em{color:#ffce7f}.selection-summary{display:flex;justify-content:space-between;align-items:center;margin:10px 0 7px;color:#b7c8d8;font-size:.74rem}.selection-summary code{color:#ffc66d}.continue{width:100%;border:0;border-radius:11px;background:linear-gradient(135deg,#14b9d7,#3ee1c1);color:#00121b;padding:12px;font-weight:950;font-size:.9rem;cursor:pointer}.continue:disabled,.refresh:disabled{opacity:.55;cursor:wait}.error{margin:12px 0 0;color:#ff9ca4;font-size:.78rem;line-height:1.45}@media(max-width:850px){.header{padding:24px}.clock{display:none}.content{grid-template-columns:1fr;grid-template-rows:auto auto;min-height:auto;padding:56px 24px 32px}.readiness-card{grid-column:1;grid-row:1}.decision{grid-column:1;grid-row:2;width:auto;justify-self:stretch}.mode-grid{grid-template-columns:1fr}}
</style>
<style scoped>
.startup-checks{display:grid;gap:8px;margin:10px 0 8px;padding:10px;border:1px solid #62ddec35;border-radius:13px;background:#04131fdd}.startup-check-head{display:flex;justify-content:space-between;align-items:center;gap:12px}.startup-check-head>b{display:block;margin-top:3px;color:#d8eff7;font-size:.77rem}.check-refresh{border:1px solid #63dff655;border-radius:8px;background:#0a2536;color:#bdeefa;padding:7px 9px;font-size:.66rem;font-weight:800;cursor:pointer}.check-refresh:disabled{opacity:.5;cursor:wait}.check-grid{display:grid;grid-template-columns:1fr 1fr;gap:7px}.startup-check{display:grid;grid-template-columns:9px 1fr;gap:8px;align-items:start;min-width:0;padding:9px;border:1px solid #ffffff16;border-radius:9px;background:#071b2acc}.startup-check.wide{grid-template-columns:9px 1fr auto;align-items:center}.startup-check b{display:block;color:#eaf8ff;font-size:.72rem}.startup-check small{display:block;margin-top:3px;color:#a9bfce;font-size:.64rem;line-height:1.35}.startup-check code{display:block;margin-top:4px;color:#ffc86e;font-size:.56rem}.check-dot{width:8px;height:8px;margin-top:3px;border-radius:50%;background:#778b9c}.startup-check.checking{border-color:#ebba5b66}.startup-check.checking .check-dot{background:#f4bd59;box-shadow:0 0 10px #f4bd59}.startup-check.ready{border-color:#42d69a66;background:#08261fbb}.startup-check.ready .check-dot{background:#4fe39d;box-shadow:0 0 10px #4fe39d}.startup-check.blocked{border-color:#ff727866;background:#2a1115bb}.startup-check.blocked .check-dot{background:#ff6f78;box-shadow:0 0 10px #ff6f78}.direction-actions{display:grid;grid-template-columns:repeat(4,minmax(64px,1fr));gap:5px;min-width:300px}.direction-actions button,.trigger-check-button{border:1px solid #62dff655;border-radius:7px;background:#0b2a3a;color:#dcf9ff;padding:7px 6px;font-size:.63rem;font-weight:850;cursor:pointer;transition:.15s}.direction-actions button:hover:not(:disabled),.trigger-check-button:hover:not(:disabled){transform:translateY(-1px);filter:brightness(1.16)}.direction-actions button:disabled,.trigger-check-button:disabled{opacity:.4;cursor:not-allowed}.direction-actions em{display:block;margin-top:2px;color:#79eab2;font-size:.5rem;font-style:normal}.trigger-check-button{border-color:#e6a45d88;background:#3e2113;color:#ffe4bd;white-space:nowrap}.wide-check-copy{min-width:0}.wide-check-copy small{max-width:420px}.hardware-result{grid-column:2/-1;display:grid;gap:2px;margin-top:3px;padding:6px 8px;border-radius:6px;background:#021018;color:#b9d1dc;font:500 .58rem ui-monospace,monospace}.hardware-result b{color:#7beab0;font-size:.61rem}.hardware-result code{color:#ffc86e}.continue:disabled{filter:grayscale(.3)}
@media(min-width:851px){.decision{width:min(960px,calc(100vw - 48px));padding:12px 16px}.startup-checks{margin-top:8px}.direction-actions{min-width:350px}}
@media(max-width:760px){.check-grid{grid-template-columns:1fr}.startup-check.wide{grid-template-columns:9px 1fr}.direction-actions{grid-column:2;grid-template-columns:repeat(2,1fr);min-width:0}.trigger-check-button{grid-column:2;justify-self:start;white-space:normal}.startup-check-head{align-items:flex-start;flex-direction:column}}
</style>
<style scoped>
.readiness-card{cursor:pointer;transition:border-color .18s,transform .18s,box-shadow .18s}.readiness-card:hover{border-color:#72e7fb80;transform:translateY(-2px);box-shadow:0 26px 72px #000a}.readiness-row{cursor:pointer}.card-hint{display:block;margin-top:5px;color:#79e7fb;font-size:.62rem;font-weight:800;letter-spacing:.04em}.decision h2{margin:5px 0 4px;font-size:1.45rem}.decision-copy{max-width:720px;margin:0;color:#abc0ce;font-size:.76rem;line-height:1.5}.decision-hint{margin:10px 0 0;color:#8fa9b8;font-size:.68rem;line-height:1.4}.decision-hint b{color:#79e7fb}
.preflight-backdrop{position:fixed;inset:0;z-index:50;display:grid;place-items:center;padding:24px;background:#000b;backdrop-filter:blur(10px)}.preflight-modal{width:min(820px,calc(100vw - 36px));max-height:min(760px,calc(100vh - 48px));overflow:auto;border:1px solid #6ce5f566;border-radius:20px;background:linear-gradient(160deg,#071b2af7,#031019f7);box-shadow:0 32px 110px #000e;color:#edfaff;padding:22px}.preflight-modal-head{display:flex;justify-content:space-between;gap:18px;align-items:flex-start}.preflight-modal-head h2{margin:6px 0 5px;font-size:1.55rem}.preflight-modal-head p:not(.eyebrow){max-width:620px;margin:0;color:#a9c1ce;font-size:.76rem;line-height:1.45}.modal-close{width:34px;height:34px;border:1px solid #ffffff24;border-radius:10px;background:#0b2635;color:#dffaff;font-size:1.45rem;line-height:1;cursor:pointer}.modal-close:hover{border-color:#79e8fb;background:#123c4f}.modal-profile-line{display:flex;align-items:center;gap:10px;margin:18px 0 11px;padding:10px 12px;border:1px solid #67dced35;border-radius:11px;background:#061725d9}.modal-profile-line span{color:#8fa8b7;font-size:.68rem}.modal-profile-line b{flex:1;font-size:.78rem}.preflight-modal .check-grid{margin-bottom:8px}.preflight-modal .startup-check.wide{padding:12px}.preflight-reasons{display:grid;gap:4px;margin-top:10px;padding:9px 11px;border:1px solid #ffc56855;border-radius:9px;background:#2d1a0bdd}.preflight-reasons b{font-size:.66rem;color:#ffd483}.preflight-reasons code{color:#ffe4a9;font-size:.59rem}.preflight-modal-foot{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-top:16px;padding-top:14px;border-top:1px solid #ffffff16}.preflight-modal-foot>span{color:#9eb5c4;font-size:.68rem;line-height:1.4}.preflight-modal-foot .continue{width:auto;min-width:190px;padding:11px 18px}.preflight-modal .warning,.preflight-modal .error{margin-bottom:0}
@media(max-width:760px){.preflight-backdrop{padding:12px}.preflight-modal{width:calc(100vw - 24px);max-height:calc(100vh - 24px);padding:16px}.preflight-modal-head h2{font-size:1.25rem}.modal-profile-line{align-items:flex-start;flex-wrap:wrap}.modal-profile-line b{flex-basis:calc(100% - 70px)}.modal-profile-line .check-refresh{margin-left:auto}.preflight-modal-foot{align-items:stretch;flex-direction:column}.preflight-modal-foot .continue{width:100%}}
</style>
<style scoped>
.profile-picker{display:grid;grid-template-columns:minmax(0,1fr) minmax(210px,280px);gap:14px;align-items:center;margin-top:10px;padding:12px 14px;border:1px solid #67dced33;border-radius:12px;background:#061725d9}.profile-picker>div{display:grid;gap:4px}.profile-picker b{font-size:.82rem}.profile-picker small{overflow:hidden;color:#a9bdcc;font-size:.69rem;text-overflow:ellipsis;white-space:nowrap}.profile-picker select{width:100%;border:1px solid #64dff54d;border-radius:9px;background:#020a13;color:#eaf9ff;padding:10px;cursor:pointer}.profile-picker select:disabled{opacity:.55;cursor:wait}@media(max-width:620px){.profile-picker{grid-template-columns:1fr}.profile-picker small{white-space:normal}}
.preflight-result{display:grid;gap:6px;max-height:190px;overflow:auto;margin-top:10px;padding:10px;border:1px solid #ff796855;border-radius:10px;background:#2a1114dd}.preflight-result.ready{border-color:#49dfa06b;background:#09261ddd}.preflight-result>b{font-size:.7rem;letter-spacing:.12em}.preflight-result>div{display:grid;grid-template-columns:12px max-content 1fr;gap:7px;align-items:start}.preflight-result code{color:#e9f7ff;font-size:.63rem}.preflight-result small{color:#aabfcd;font-size:.62rem;line-height:1.3}.preflight-result .ok{color:#4fe39d}.preflight-result .bad{color:#ff6f78}.warning{margin:9px 0 0;color:#ffd07e;font-size:.7rem;line-height:1.4}
</style>
<style scoped>
@media(min-width:851px){
  .landing{height:100vh;min-height:0}
  .header{box-sizing:border-box;height:112px;padding:22px clamp(24px,5vw,72px)}
  .header h1{font-size:clamp(1.8rem,2.55vw,2.85rem)}
  .setup-link{padding:9px 12px}
  .clock{min-width:165px;padding:10px 14px}
  .content{box-sizing:border-box;height:calc(100vh - 112px);min-height:0;grid-template-rows:minmax(0,1fr) auto;gap:12px;padding:0 clamp(24px,5vw,72px) 16px}
  .readiness-card{margin-top:0;padding:15px 18px}
  .card-heading{margin-bottom:7px}
  .card-heading h2{margin:3px 0;font-size:1.18rem}
  .refresh{padding:7px 9px}
  .readiness-row{gap:9px;padding:10px 3px}
  .readiness-row code{font-size:.58rem}
  .row-copy b{font-size:.83rem}
  .row-copy small{font-size:.68rem;line-height:1.25}
  .decision{width:min(960px,calc(100vw - 48px));padding:11px 16px}
  .profile-picker{gap:10px;margin-top:7px;padding:8px 11px}
  .profile-picker select{padding:8px 9px}
  .mode-grid{gap:9px;margin-top:8px}
  .mode-card{min-height:72px;padding:9px 11px}
  .mode-card small{margin-top:2px}
  .mode-card em{margin-top:3px}
  .selection-summary{margin:7px 0 5px}
  .continue{padding:10px}
}
@media(min-width:851px) and (max-height:760px){
  .header{height:92px;padding-top:14px;padding-bottom:14px}
  .header h1{font-size:2rem}
  .content{height:calc(100vh - 92px)}
  .readiness-row{padding:7px 3px}
  .decision{padding:8px 13px}
  .mode-card{min-height:64px}
}
</style>
