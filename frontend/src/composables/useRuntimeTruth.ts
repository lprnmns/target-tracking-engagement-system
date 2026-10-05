import { computed } from 'vue'
import type { ComputedRef } from 'vue'
import { useDeviceRuntimeStore } from '../stores/deviceRuntimeStore'
import { useHardwareStore } from '../stores/hardwareStore'
import { useMotionStore } from '../stores/motionStore'
import { useSerialStore } from '../stores/serialStore'
import { useSystemStore } from '../stores/systemStore'
import { useVisionStore } from '../stores/visionStore'
import type { ManagedDevice } from '../types/deviceRuntime'

export type TruthTone = 'good' | 'warn' | 'bad' | 'neutral'

export interface HealthIssue {
  id: string
  area: string
  label: string
  detail: string
  tone: TruthTone
}

export interface RuntimeTruth {
  selectedPhysicalCamera: ComputedRef<ManagedDevice | null>
  physicalCameraConnected: ComputedRef<boolean>
  cameraProfileIsMock: ComputedRef<boolean>
  testVisionActive: ComputedRef<boolean>
  realCameraStreamHealthy: ComputedRef<boolean>
  cameraLabel: ComputedRef<string>
  cameraTone: ComputedRef<TruthTone>
  cameraMessage: ComputedRef<string>
  picoSimulated: ComputedRef<boolean>
  picoHealthy: ComputedRef<boolean>
  picoLabel: ComputedRef<string>
  picoTone: ComputedRef<TruthTone>
  trackingLabel: ComputedRef<string>
  trackingTone: ComputedRef<TruthTone>
  fireBlocked: ComputedRef<boolean>
  fireLabel: ComputedRef<string>
  fireTone: ComputedRef<TruthTone>
  magazineLabel: ComputedRef<string>
  magazineTone: ComputedRef<TruthTone>
  commandLineHealthy: ComputedRef<boolean>
  commandLineTone: ComputedRef<TruthTone>
  healthIssues: ComputedRef<HealthIssue[]>
  criticalIssues: ComputedRef<HealthIssue[]>
  overallTone: ComputedRef<TruthTone>
  overallLabel: ComputedRef<string>
}

export function useRuntimeTruth(): RuntimeTruth {
  const system = useSystemStore()
  const vision = useVisionStore()
  const runtime = useDeviceRuntimeStore()
  const hardware = useHardwareStore()
  const serial = useSerialStore()
  const motion = useMotionStore()

  const selectedPhysicalCamera = computed(() => {
    const profile = runtime.cameraStatus.profile
    return runtime.inventory.cameras.find((camera) => (
      (profile.device_path && camera.device_path === profile.device_path)
      || (profile.stable_path && camera.stable_path === profile.stable_path)
      || (profile.device_id && camera.device_id === profile.device_id)
    )) ?? null
  })

  const cameraProfileIsMock = computed(() => runtime.cameraStatus.profile.source_type === 'mock')
  const physicalCameraConnected = computed(() => (
    !cameraProfileIsMock.value
    && !!selectedPhysicalCamera.value
    && selectedPhysicalCamera.value.connected
  ))
  const testVisionActive = computed(() => (
    cameraProfileIsMock.value
    || runtime.visionStatus.test_adapter_active
    || !!runtime.visionStatus.surrogate_source_kind
  ))
  const realCameraStreamHealthy = computed(() => (
    (physicalCameraConnected.value || runtime.cameraStatus.is_real_camera_evidence)
    && runtime.cameraStatus.running
    && vision.cameraStatus.running
    && (vision.cameraStatus.connected || vision.cameraStatus.is_real_camera_evidence)
  ))
  const cameraTone = computed<TruthTone>(() => {
    if (realCameraStreamHealthy.value) return 'good'
    if (runtime.cameraStatus.source_mode?.includes('LAPTOP') || testVisionActive.value || physicalCameraConnected.value) return 'warn'
    return 'bad'
  })
  const cameraLabel = computed(() => {
    if (realCameraStreamHealthy.value && runtime.cameraStatus.is_laptop_camera) return 'Laptop kamera hazır'
    if (realCameraStreamHealthy.value && runtime.cameraStatus.is_external_usb_camera) return 'USB kamera hazır'
    if (runtime.cameraStatus.is_laptop_camera) return 'Laptop kamera dev'
    if (cameraProfileIsMock.value) return 'Test kamera'
    if (testVisionActive.value) return 'Test görüntü'
    if (physicalCameraConnected.value) return 'Akış yok'
    return 'Kamera yok'
  })
  const cameraMessage = computed(() => {
    if (realCameraStreamHealthy.value) return `${runtime.cameraStatus.selected_camera} canlı`
    if (runtime.cameraStatus.hardware_presence_note) return runtime.cameraStatus.hardware_presence_note
    if (runtime.cameraStatus.last_capture_error) return runtime.cameraStatus.last_capture_error
    if (testVisionActive.value) return 'Mock/surrogate veri gerçek kamera kanıtı değildir.'
    if (physicalCameraConnected.value) return 'Cihaz görünüyor fakat frame akışı doğrulanmadı.'
    if (runtime.cameraStatus.selected_camera && runtime.cameraStatus.selected_camera !== 'mock') {
      return `${runtime.cameraStatus.selected_camera} envanterde doğrulanmadı.`
    }
    return 'USB kamera seçilmedi veya bağlı değil.'
  })

  const hardwarePicoHealthy = computed(() => (
    hardware.status.transport_mode !== 'mock'
    && (hardware.status.pico_verified || hardware.status.telemetry_received)
  ))
  const serialPicoHealthy = computed(() => (
    serial.status.transport_mode !== 'mock'
    && serial.status.transport_source !== 'mock'
    && (serial.status.pico_verified || serial.status.telemetry_received)
  ))
  const serialLiveSelected = computed(() => (
    serial.status.transport_source === 'real_serial'
    && serial.status.real_serial_enabled
  ))
  const picoHealthy = computed(() => serialLiveSelected.value ? serialPicoHealthy.value : hardwarePicoHealthy.value)
  const picoSimulated = computed(() => (
    !picoHealthy.value
    && !serialLiveSelected.value
    && (hardware.status.mock_pico_active || hardware.status.transport_mode === 'mock' || serial.status.transport_mode === 'mock' || serial.status.transport_source === 'mock')
  ))
  const picoLabel = computed(() => {
    if (picoHealthy.value) return 'Pico sağlıklı'
    if (picoSimulated.value) return 'Pico simülasyon'
    if (serialLiveSelected.value && serial.status.transport_healthy) return 'Pico port açık · doğrulama bekleniyor'
    if (hardware.status.port_open) return 'Pico port açık'
    return 'Pico yok'
  })
  const picoTone = computed<TruthTone>(() => {
    if (picoHealthy.value) return 'good'
    if (picoSimulated.value) return 'warn'
    if (hardware.status.port_open) return 'warn'
    return 'bad'
  })
  const trackingLabel = computed(() => motion.trackingStatus.active ? motion.trackingStatus.state : 'Tracking kapalı')
  const trackingTone = computed<TruthTone>(() => {
    if (motion.trackingStatus.active && motion.trackingStatus.state === 'LOCKED') return 'good'
    if (motion.trackingStatus.active) return 'warn'
    return 'neutral'
  })
  const commandLineHealthy = computed(() => serial.status.command_queue_depth <= 1 && serial.status.last_command_ack_state !== 'timeout')
  const commandLineTone = computed<TruthTone>(() => commandLineHealthy.value ? 'good' : 'bad')
  const fireBlocked = computed(() => (
    serial.status.magazine_empty
    || !hardware.status.physical_command_enabled
    || !hardware.capabilities.allow_physical_fire
    || system.systemState.fire_policy === 'NO_FIRE'
  ))
  const fireLabel = computed(() => fireBlocked.value ? 'Fire blocked' : 'Fire ready')
  const fireTone = computed<TruthTone>(() => fireBlocked.value ? 'bad' : 'good')
  const magazineLabel = computed(() => `${serial.status.magazine_remaining}/${serial.status.magazine_capacity}`)
  const magazineTone = computed<TruthTone>(() => {
    if (serial.status.magazine_empty) return 'bad'
    if (serial.status.magazine_remaining <= 2) return 'warn'
    return 'good'
  })
  const performanceStatus = computed(() => system.latestEvents.find((event) => event.type === 'performance.status')?.payload as {
    camera_frame_age_ms?: number | null
    total_pipeline_ms?: number | null
    primary_bottleneck?: string
    serial_queue_depth?: number
    metrics?: Record<string, { tone?: string, value?: number | null, unit?: string }>
  } | undefined)
  const healthIssues = computed<HealthIssue[]>(() => {
    const issues: HealthIssue[] = []
    if (system.connectionStatus !== 'connected') {
      issues.push({ id: 'backend_offline', area: 'Backend', label: 'Backend bağlantısı yok', detail: 'WebSocket/REST bağlantısı gelmiyor.', tone: 'bad' })
    }
    if (!realCameraStreamHealthy.value) {
      issues.push({ id: 'camera_stream', area: 'Kamera', label: cameraLabel.value, detail: cameraMessage.value, tone: cameraTone.value })
    }
    if (runtime.cameraStatus.selected_camera !== 'mock' && !selectedPhysicalCamera.value) {
      issues.push({ id: 'camera_inventory_mismatch', area: 'Kamera', label: 'Seçili kamera envanterde yok', detail: `${runtime.cameraStatus.selected_camera} fiziksel cihaz listesinde doğrulanmadı.`, tone: 'bad' })
    }
    if (runtime.visionStatus.test_adapter_active || runtime.visionStatus.surrogate_source_kind) {
      issues.push({ id: 'vision_test_adapter', area: 'Vision', label: 'Test/surrogate vision', detail: 'Bu mod gerçek yarışma YOLO kanıtı değildir.', tone: 'warn' })
    } else if (!runtime.visionStatus.production_yolo_loaded) {
      issues.push({ id: 'yolo_not_loaded', area: 'Vision', label: 'Production YOLO hazır değil', detail: runtime.visionStatus.errors[0] ?? 'Aktif model yüklenmedi veya doğrulanmadı.', tone: 'bad' })
    }
    if (!picoHealthy.value) {
      issues.push({ id: 'pico_disconnected', area: 'Pico', label: picoLabel.value, detail: hardware.status.telemetry.last_error ?? hardware.status.connection_state, tone: picoTone.value })
    }
    if (serial.status.command_queue_depth > 1) {
      issues.push({ id: 'serial_queue', area: 'Serial', label: 'Komut kuyruğu birikiyor', detail: `${serial.status.command_queue_depth} komut sırada.`, tone: serial.status.command_queue_depth > 4 ? 'bad' : 'warn' })
    }
    if (serial.status.last_command_ack_state === 'timeout') {
      issues.push({ id: 'serial_timeout', area: 'Serial', label: 'ACK timeout', detail: serial.status.last_command_error ?? 'Son komut cevap vermedi.', tone: 'bad' })
    }
    if (serial.status.magazine_empty) {
      issues.push({ id: 'magazine_empty', area: 'Fire', label: 'Şarjör boş', detail: 'Fire komutu bloklanır.', tone: 'bad' })
    }
    if (fireBlocked.value) {
      issues.push({ id: 'fire_blocked', area: 'Fire', label: 'Fire gate kapalı', detail: `${system.systemState.fire_policy}; physical_fire=${hardware.capabilities.allow_physical_fire}`, tone: 'warn' })
    }
    if ((performanceStatus.value?.metrics?.total_pipeline?.tone ?? 'neutral') === 'bad') {
      issues.push({ id: 'pipeline_latency', area: 'Latency', label: 'Toplam gecikme yüksek', detail: `${performanceStatus.value?.total_pipeline_ms ?? 'n/a'} ms`, tone: 'bad' })
    }
    return issues
  })
  const criticalIssues = computed(() => healthIssues.value.filter((issue) => issue.tone === 'bad'))
  const overallTone = computed<TruthTone>(() => {
    if (criticalIssues.value.length > 0) return 'bad'
    if (healthIssues.value.some((issue) => issue.tone === 'warn')) return 'warn'
    return 'good'
  })
  const overallLabel = computed(() => {
    if (overallTone.value === 'good') return 'Sistem hazır'
    if (overallTone.value === 'warn') return `${healthIssues.value.length} uyarı`
    return `${criticalIssues.value.length} kritik sorun`
  })

  return {
    selectedPhysicalCamera,
    physicalCameraConnected,
    cameraProfileIsMock,
    testVisionActive,
    realCameraStreamHealthy,
    cameraLabel,
    cameraTone,
    cameraMessage,
    picoSimulated,
    picoHealthy,
    picoLabel,
    picoTone,
    trackingLabel,
    trackingTone,
    fireBlocked,
    fireLabel,
    fireTone,
    magazineLabel,
    magazineTone,
    commandLineHealthy,
    commandLineTone,
    healthIssues,
    criticalIssues,
    overallTone,
    overallLabel,
  }
}
