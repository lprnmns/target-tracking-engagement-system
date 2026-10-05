<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'

type AssetId = 'phase55-raw' | 'phase55-kinematic' | 'step-hifi'

const ASSETS: Record<AssetId, { label: string, path: string }> = {
  'phase55-raw': {
    label: 'RAW Phase55 GLB',
    path: '/assets/digital-twin/ktr1_kinematic_world_phase55.glb?v=operator-x90-1234-20260815',
  },
  'phase55-kinematic': {
    label: 'STEP optimized (inspection)',
    path: '/assets/digital-twin/ktr1_colored_step_hero.glb?v=calibration-panel-1',
  },
  'step-hifi': {
    label: 'STEP HiFi Phase54',
    path: '/assets/digital-twin/ktr1_step_hifi_phase54.glb',
  },
}

const STORAGE_KEY = 'istiklal.c2.modelCalibration.phase55'
const OPERATOR_STORAGE_KEY = 'istiklal.c2.modelCalibration.phase55.operator'
const CALIBRATION_SCHEMA = 'phase116_runtime_x90_1234_v1'

const canvasRoot = ref<HTMLDivElement | null>(null)
const selectedAsset = ref<AssetId>('phase55-raw')
// Edge extraction creates a second line geometry for every one of the 136
// meshes in the raw Phase55 asset. It is useful for CAD inspection, but it
// makes the first calibration frame unnecessarily expensive (especially on
// the Windows HIL laptop). Start with the readable solid model; Edges remains
// an explicit opt-in control in the toolbar.
const edgesEnabled = ref(false)
const gridEnabled = ref(true)
const groundEnabled = ref(true)
const modelLoaded = ref(false)
const loadError = ref<string | null>(null)
const copied = ref(false)
const statusMessage = ref('')
const boundingBoxLabel = ref('n/a')
const keyboardStep = ref(1)

const transform = reactive({
  positionX: 0.13,
  positionY: 1.65,
  positionZ: -4.65,
  rotationX: 90,
  rotationY: 0,
  rotationZ: 0,
  scale: 1,
  groundY: 0,
})

const presetForAsset: Record<AssetId, typeof transform> = {
  'phase55-raw': {
    positionX: 0.13,
    positionY: 1.65,
    positionZ: -4.65,
    rotationX: 90,
    rotationY: 0,
    rotationZ: 0,
    scale: 1,
    groundY: 0,
  },
  'phase55-kinematic': {
    positionX: 0,
    positionY: 0,
    positionZ: 0,
    rotationX: 0,
    rotationY: 0,
    rotationZ: 0,
    scale: 1,
    groundY: 0,
  },
  'step-hifi': {
    positionX: 0,
    positionY: 0,
    positionZ: 0,
    rotationX: 0,
    rotationY: 0,
    rotationZ: 0,
    scale: 1,
    groundY: 0,
  },
}

let THREE: any = null
let renderer: any = null
let scene: any = null
let camera: any = null
let controls: any = null
let modelRoot: any = null
let modelContent: any = null
let guideRoot: any = null
let grid: any = null
let ground: any = null
let animationId: number | null = null
let resizeObserver: ResizeObserver | null = null

const calibrationJson = computed(() => JSON.stringify({
  // Keep this in sync with DigitalTwinPanel's accepted operator schema.  It
  // invalidates pre-correction drafts without touching any physical command.
  assetVersion: CALIBRATION_SCHEMA,
  visualizationOnly: true,
  safety: {
    physical_command_enabled: false,
    serial_tx_enabled: false,
    no_physical_command_generated: true,
  },
  source: {
    asset: selectedAsset.value,
    glbPath: ASSETS[selectedAsset.value].path.split('?')[0],
    cadPath: 'work/ktr1.step',
  },
    runtimeTransform: {
    position: [round(transform.positionX), round(transform.positionY), round(transform.positionZ)],
    rotationEulerDeg: [round(transform.rotationX), round(transform.rotationY), round(transform.rotationZ)],
    scale: round(transform.scale),
    groundY: round(transform.groundY),
      note: 'Manual visual calibration from /cockpit/model-calibration. Read-only digital twin transform only.',
    },
    frame: {
      frontAxisRuntime: [0, 0, 1],
      upAxisRuntime: [0, 1, 0],
      cameraAxisRuntime: [0, 0, 1],
      launcherAxisRuntime: [0, 0, 1],
      yawSign: -1,
      pitchSign: 1,
      note: '12:34 runtime contract: raw CAD Z-up is corrected by root X=+90°; yaw uses source -Z and pitch uses source +X. Visualization only.',
    },
  }, null, 2))

function round(value: number): number {
  return Number(value.toFixed(4))
}

function applyTransform(): void {
  if (!THREE || !modelRoot) return
  modelRoot.position.set(transform.positionX, transform.positionY, transform.positionZ)
  modelRoot.rotation.set(
    THREE.MathUtils.degToRad(transform.rotationX),
    THREE.MathUtils.degToRad(transform.rotationY),
    THREE.MathUtils.degToRad(transform.rotationZ),
    'XYZ',
  )
  modelRoot.scale.setScalar(transform.scale)
  if (guideRoot) {
    guideRoot.position.copy(modelRoot.position)
    guideRoot.rotation.copy(modelRoot.rotation)
    guideRoot.scale.setScalar(Math.max(0.5, transform.scale))
  }
  if (ground) ground.position.y = transform.groundY
  if (grid) grid.position.y = transform.groundY + 0.004
}

async function initScene(): Promise<void> {
  if (!canvasRoot.value || renderer) return
  THREE = await import('three')
  scene = new THREE.Scene()
  scene.background = new THREE.Color(0xe8edf2)
  camera = new THREE.PerspectiveCamera(36, 1, 0.01, 300)
  camera.position.set(3.8, 2.6, 4.4)
  camera.lookAt(0, 0.6, 0)

  renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false, powerPreference: 'high-performance' })
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2))
  renderer.outputColorSpace = THREE.SRGBColorSpace
  renderer.toneMapping = THREE.ACESFilmicToneMapping
  renderer.toneMappingExposure = 1.2
  renderer.shadowMap.enabled = true
  canvasRoot.value.appendChild(renderer.domElement)

  const { OrbitControls } = await import('three/examples/jsm/controls/OrbitControls.js')
  controls = new OrbitControls(camera, renderer.domElement)
  controls.enableDamping = true
  controls.enableZoom = true
  controls.zoomSpeed = 0.9
  controls.enablePan = true
  controls.minDistance = 0.25
  controls.maxDistance = 40

  scene.add(new THREE.HemisphereLight(0xffffff, 0x9ca3af, 2.2))
  const key = new THREE.DirectionalLight(0xffffff, 2.2)
  key.position.set(3, 6, 4)
  key.castShadow = true
  scene.add(key)
  const fill = new THREE.DirectionalLight(0xdbeafe, 0.9)
  fill.position.set(-4, 3, -3)
  scene.add(fill)

  modelRoot = new THREE.Group()
  modelRoot.name = 'manual_calibration_root'
  scene.add(modelRoot)

  guideRoot = new THREE.Group()
  guideRoot.name = 'manual_calibration_frame_guides'
  const origin = new THREE.Vector3(0, 0, 0)
  guideRoot.add(new THREE.ArrowHelper(new THREE.Vector3(0, 0, 1), origin, 2.4, 0x22d3ee, 0.24, 0.12))
  guideRoot.add(new THREE.ArrowHelper(new THREE.Vector3(0, 1, 0), origin, 1.8, 0x4ade80, 0.2, 0.1))
  guideRoot.add(new THREE.ArrowHelper(new THREE.Vector3(1, 0, 0), origin, 1.4, 0xfacc15, 0.18, 0.09))
  const targetMarker = new THREE.Mesh(
    new THREE.SphereGeometry(0.12, 20, 12),
    new THREE.MeshBasicMaterial({ color: 0xfacc15, transparent: true, opacity: 0.9 }),
  )
  targetMarker.position.set(0, 0, 2.4)
  targetMarker.name = 'calibration_front_target_marker'
  guideRoot.add(targetMarker)
  scene.add(guideRoot)

  ground = new THREE.Mesh(
    new THREE.PlaneGeometry(16, 16),
    new THREE.MeshStandardMaterial({ color: 0xd8dee6, metalness: 0, roughness: 0.8 }),
  )
  ground.rotation.x = -Math.PI / 2
  ground.receiveShadow = true
  scene.add(ground)
  grid = new THREE.GridHelper(16, 32, 0x1f2937, 0x9ca3af)
  scene.add(grid)

  resizeObserver = new ResizeObserver(resizeScene)
  resizeObserver.observe(canvasRoot.value)
  resizeScene()
  await loadModel()
  renderLoop()
}

async function loadModel(): Promise<void> {
  if (!THREE || !modelRoot) return
  disposeModel()
  modelLoaded.value = false
  loadError.value = null
  try {
    const { GLTFLoader } = await import('three/examples/jsm/loaders/GLTFLoader.js')
    const loader = new GLTFLoader()
    const gltf = await loader.loadAsync(ASSETS[selectedAsset.value].path)
    modelContent = gltf.scene
    modelContent.name = 'calibrated_ktr_model'
    modelContent.traverse((child: any) => {
      if (!child.isMesh) return
      child.castShadow = true
      child.receiveShadow = true
      if (child.material) {
        const materials = Array.isArray(child.material) ? child.material : [child.material]
        materials.forEach((material: any) => {
          material.roughness = Math.min(Math.max(material.roughness ?? 0.58, 0.42), 0.82)
          material.metalness = Math.min(Math.max(material.metalness ?? 0.04, 0), 0.22)
          material.needsUpdate = true
        })
      }
    })
    modelRoot.add(modelContent)
    rebuildEdges()
    applyTransform()
    fitCamera()
    modelLoaded.value = true
  } catch (error) {
    loadError.value = error instanceof Error ? error.message : String(error)
  }
}

function disposeModel(): void {
  if (!modelRoot) return
  while (modelRoot.children.length) {
    const child = modelRoot.children.pop()
    child?.traverse?.(disposeObject)
  }
  modelContent = null
}

function rebuildEdges(): void {
  if (!THREE || !modelContent) return
  modelContent.traverse((child: any) => {
    const stale = child.children?.filter?.((node: any) => node.name === 'calibration_edges') ?? []
    stale.forEach((node: any) => child.remove(node))
    if (!child.isMesh || !child.geometry || !edgesEnabled.value) return
    const edges = new THREE.LineSegments(
      new THREE.EdgesGeometry(child.geometry, 10),
      new THREE.LineBasicMaterial({ color: 0x111827, transparent: true, opacity: 0.62 }),
    )
    edges.name = 'calibration_edges'
    child.add(edges)
  })
}

function fitCamera(): void {
  if (!THREE || !modelRoot || !camera || !controls) return
  const box = new THREE.Box3().setFromObject(modelRoot)
  if (!Number.isFinite(box.min.x)) return
  const size = new THREE.Vector3()
  const center = new THREE.Vector3()
  box.getSize(size)
  box.getCenter(center)
  boundingBoxLabel.value = `center ${center.x.toFixed(2)}, ${center.y.toFixed(2)}, ${center.z.toFixed(2)} · size ${size.x.toFixed(2)}, ${size.y.toFixed(2)}, ${size.z.toFixed(2)}`
  const radius = Math.max(size.length() * 0.5, 0.5)
  controls.target.copy(center)
  camera.position.copy(center.clone().add(new THREE.Vector3(radius * 1.2, radius * 0.85, radius * 1.35)))
  camera.near = 0.01
  camera.far = Math.max(200, radius * 50)
  camera.updateProjectionMatrix()
  controls.update()
}

function resizeScene(): void {
  if (!renderer || !camera || !canvasRoot.value) return
  const width = Math.max(320, canvasRoot.value.clientWidth)
  const height = Math.max(240, canvasRoot.value.clientHeight)
  renderer.setSize(width, height, false)
  camera.aspect = width / height
  camera.updateProjectionMatrix()
}

function renderLoop(): void {
  if (!renderer || !scene || !camera) return
  applyTransform()
  ground.visible = groundEnabled.value
  grid.visible = gridEnabled.value
  controls?.update?.()
  renderer.render(scene, camera)
  animationId = window.requestAnimationFrame(renderLoop)
}

function disposeObject(object: any): void {
  if (object.geometry) object.geometry.dispose?.()
  if (object.material) {
    const materials = Array.isArray(object.material) ? object.material : [object.material]
    materials.forEach((material: any) => material.dispose?.())
  }
}

function resetTransform(): void {
  Object.assign(transform, presetForAsset[selectedAsset.value])
  statusMessage.value = 'Seçili asset için güvenli başlangıç çerçevesi uygulandı.'
  void nextTick(fitCamera)
}

function saveLocal(): void {
  window.localStorage.setItem(STORAGE_KEY, calibrationJson.value)
  statusMessage.value = 'Taslak tarayıcıya kaydedildi.'
}

function loadLocal(): void {
  const raw = window.localStorage.getItem(STORAGE_KEY)
  if (!raw) return
  try {
    const parsed = JSON.parse(raw)
    if (parsed.assetVersion !== CALIBRATION_SCHEMA) {
      statusMessage.value = 'Eski kalibrasyon taslağı yok sayıldı; doğrulanmış başlangıç kullanılıyor.'
      return
    }
    const runtime = parsed.runtimeTransform ?? {}
    const position = runtime.position ?? []
    const rotation = runtime.rotationEulerDeg ?? []
    transform.positionX = Number(position[0] ?? transform.positionX)
    transform.positionY = Number(position[1] ?? transform.positionY)
    transform.positionZ = Number(position[2] ?? transform.positionZ)
    transform.rotationX = Number(rotation[0] ?? transform.rotationX)
    transform.rotationY = Number(rotation[1] ?? transform.rotationY)
    transform.rotationZ = Number(rotation[2] ?? transform.rotationZ)
    transform.scale = Number(runtime.scale ?? transform.scale)
    transform.groundY = Number(runtime.groundY ?? transform.groundY)
  } catch {
    // Local calibration is optional; ignore malformed drafts.
  }
}

async function copyJson(): Promise<void> {
  await navigator.clipboard.writeText(calibrationJson.value)
  copied.value = true
  window.setTimeout(() => { copied.value = false }, 1400)
}

function applyToOperator(): void {
  window.localStorage.setItem(OPERATOR_STORAGE_KEY, calibrationJson.value)
  window.localStorage.setItem(STORAGE_KEY, calibrationJson.value)
  window.dispatchEvent(new CustomEvent('istiklal:model-calibration-applied'))
  statusMessage.value = 'Doğrulanmış görsel transform kokpite uygulandı.'
}

function downloadJson(): void {
  const blob = new Blob([calibrationJson.value], { type: 'application/json' })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `istiklal-twin-calibration-${selectedAsset.value}.json`
  anchor.click()
  URL.revokeObjectURL(url)
  statusMessage.value = 'JSON dosyası indirildi.'
}

function nudgeTransform(axis: 'rotationX' | 'rotationY' | 'rotationZ', direction: number): void {
  const next = Number(transform[axis]) + direction * Math.max(0.1, Number(keyboardStep.value) || 1)
  transform[axis] = Math.max(-180, Math.min(180, Number(next.toFixed(2))))
  applyTransform()
}

function handleKeyDown(event: KeyboardEvent): void {
  if (event.target instanceof HTMLInputElement || event.target instanceof HTMLSelectElement || event.target instanceof HTMLTextAreaElement) return
  const step = event.shiftKey ? 5 : 1
  if (event.key === 'ArrowLeft') { event.preventDefault(); nudgeTransform('rotationY', -step) }
  else if (event.key === 'ArrowRight') { event.preventDefault(); nudgeTransform('rotationY', step) }
  else if (event.key === 'ArrowUp') { event.preventDefault(); nudgeTransform('rotationX', step) }
  else if (event.key === 'ArrowDown') { event.preventDefault(); nudgeTransform('rotationX', -step) }
  else if (event.key.toLowerCase() === 'q') { event.preventDefault(); nudgeTransform('rotationZ', -step) }
  else if (event.key.toLowerCase() === 'e') { event.preventDefault(); nudgeTransform('rotationZ', step) }
}

watch(selectedAsset, () => {
  Object.assign(transform, presetForAsset[selectedAsset.value])
  void loadModel()
})

watch(edgesEnabled, rebuildEdges)

onMounted(() => {
  loadLocal()
  window.addEventListener('keydown', handleKeyDown)
  void initScene()
})

onBeforeUnmount(() => {
  if (animationId !== null) window.cancelAnimationFrame(animationId)
  window.removeEventListener('keydown', handleKeyDown)
  resizeObserver?.disconnect()
  controls?.dispose?.()
  scene?.traverse(disposeObject)
  renderer?.dispose?.()
  if (renderer?.domElement?.parentElement) renderer.domElement.parentElement.removeChild(renderer.domElement)
})
</script>

<template>
  <main class="calibration-page">
    <section class="calibration-header">
      <div>
        <p class="eyebrow">ISTIKLAL · read-only calibration</p>
        <h1>3D Model Kalibrasyon Paneli</h1>
        <p>Modeli manuel konumlandır, doğru transform değerlerini çıkar. Bu panel fiziksel komut üretmez.</p>
      </div>
      <div class="safety-pill">physical_command_enabled=false · serial_tx_enabled=false · no_physical_command_generated=true</div>
    </section>

    <section class="calibration-layout">
      <div class="viewer-card">
        <div class="viewer-toolbar">
          <label>
            Asset
            <select v-model="selectedAsset">
              <option value="phase55-raw">RAW Phase55 GLB</option>
              <option value="phase55-kinematic">Kinematic STEP GLB</option>
              <option value="step-hifi">STEP HiFi Phase54</option>
            </select>
          </label>
          <button @click="fitCamera">Fit</button>
          <button @click="resetTransform">Reset Transform</button>
          <label class="check"><input v-model="edgesEnabled" type="checkbox"> Edges</label>
          <label class="check"><input v-model="gridEnabled" type="checkbox"> Grid</label>
          <label class="check"><input v-model="groundEnabled" type="checkbox"> Ground</label>
          <span class="keyboard-hint">Yön tuşları: X/Y açı · Q/E: Z · Shift: 5°</span>
        </div>
        <div ref="canvasRoot" class="calibration-canvas">
          <div v-if="loadError" class="error-box">{{ loadError }}</div>
          <div v-else-if="!modelLoaded" class="loading-box">Model yükleniyor...</div>
        </div>
      </div>

      <aside class="control-card">
        <div class="control-section">
          <h2>Transform</h2>
          <p class="muted">{{ ASSETS[selectedAsset].label }}</p>
          <p class="muted">{{ boundingBoxLabel }}</p>
          <p class="frame-note"><span class="axis-cyan">●</span> cyan: ön/kamera ekseni · <span class="axis-green">●</span> yeşil: yukarı · <span class="axis-yellow">●</span> sarı: sağ</p>
        </div>

        <div class="control-section">
          <h3>Position</h3>
          <label>X <input v-model.number="transform.positionX" type="range" min="-4" max="4" step="0.01"><span>{{ transform.positionX.toFixed(2) }}</span></label>
          <label>Y <input v-model.number="transform.positionY" type="range" min="-4" max="4" step="0.01"><span>{{ transform.positionY.toFixed(2) }}</span></label>
          <label>Z <input v-model.number="transform.positionZ" type="range" min="-4" max="4" step="0.01"><span>{{ transform.positionZ.toFixed(2) }}</span></label>
        </div>

        <div class="control-section">
          <h3>Rotation</h3>
          <label>X <input v-model.number="transform.rotationX" type="range" min="-180" max="180" step="1"><span>{{ transform.rotationX }}°</span></label>
          <label>Y <input v-model.number="transform.rotationY" type="range" min="-180" max="180" step="1"><span>{{ transform.rotationY }}°</span></label>
          <label>Z <input v-model.number="transform.rotationZ" type="range" min="-180" max="180" step="1"><span>{{ transform.rotationZ }}°</span></label>
        </div>

        <div class="control-section">
          <h3>Scale / Ground</h3>
          <label>Scale <input v-model.number="transform.scale" type="range" min="0.05" max="3" step="0.01"><span>{{ transform.scale.toFixed(2) }}</span></label>
          <label>Ground Y <input v-model.number="transform.groundY" type="range" min="-4" max="4" step="0.01"><span>{{ transform.groundY.toFixed(2) }}</span></label>
          <label>Key step <input v-model.number="keyboardStep" type="range" min="0.1" max="10" step="0.1"><span>{{ keyboardStep.toFixed(1) }}°</span></label>
        </div>

        <div class="actions">
          <button @click="resetTransform">Başlangıç</button>
          <button @click="saveLocal">Taslağı Kaydet</button>
          <button class="primary-action" @click="applyToOperator">Kokpite Uygula</button>
          <button @click="loadLocal">Taslağı Yükle</button>
          <button @click="copyJson">{{ copied ? 'Kopyalandı' : 'JSON Kopyala' }}</button>
          <button @click="downloadJson">JSON İndir</button>
        </div>

        <p v-if="statusMessage" class="status-message" role="status">{{ statusMessage }}</p>

        <pre>{{ calibrationJson }}</pre>
      </aside>
    </section>
  </main>
</template>

<style scoped>
.calibration-page {
  min-height: 100vh;
  padding: 20px;
  background: #07101d;
  color: #e5edf7;
}

.calibration-header {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 18px;
  margin-bottom: 16px;
  padding: 18px;
  border: 1px solid rgba(34, 211, 238, 0.2);
  border-radius: 8px;
  background: rgba(3, 7, 18, 0.72);
}

.eyebrow {
  margin: 0 0 8px;
  color: #67e8f9;
  font-size: 11px;
  font-weight: 800;
  letter-spacing: 0.22em;
  text-transform: uppercase;
}

h1, h2, h3, p {
  margin: 0;
}

h1 {
  font-size: 28px;
}

.calibration-header p:last-child {
  margin-top: 6px;
  color: #94a3b8;
}

.safety-pill {
  padding: 12px 14px;
  border: 1px solid rgba(16, 185, 129, 0.42);
  border-radius: 6px;
  background: rgba(6, 78, 59, 0.35);
  color: #a7f3d0;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 12px;
  white-space: nowrap;
}

.calibration-layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 420px;
  gap: 16px;
}

.viewer-card,
.control-card {
  border: 1px solid rgba(148, 163, 184, 0.2);
  border-radius: 8px;
  background: rgba(15, 23, 42, 0.72);
  overflow: hidden;
}

.viewer-toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  min-height: 56px;
  padding: 10px 12px;
  border-bottom: 1px solid rgba(148, 163, 184, 0.18);
}

.viewer-toolbar label {
  display: flex;
  align-items: center;
  gap: 8px;
  color: #94a3b8;
  font-size: 12px;
  font-weight: 800;
  text-transform: uppercase;
  letter-spacing: 0.08em;
}

select,
button {
  min-height: 34px;
  border: 1px solid rgba(34, 211, 238, 0.32);
  border-radius: 6px;
  background: #08111f;
  color: #e0f2fe;
  font-weight: 800;
}

button {
  padding: 0 12px;
  cursor: pointer;
}

.check {
  padding: 7px 10px;
  border-radius: 6px;
  background: rgba(15, 23, 42, 0.9);
}

.keyboard-hint {
  margin-left: auto;
  color: #67e8f9;
  font-size: 11px;
  font-weight: 800;
  letter-spacing: 0.04em;
}

.calibration-canvas {
  position: relative;
  height: calc(100vh - 190px);
  min-height: 720px;
}

.calibration-canvas :deep(canvas) {
  display: block;
  width: 100%;
  height: 100%;
}

.error-box,
.loading-box {
  position: absolute;
  left: 16px;
  top: 16px;
  z-index: 2;
  padding: 10px 12px;
  border-radius: 6px;
  background: rgba(15, 23, 42, 0.88);
  color: #fecaca;
}

.loading-box {
  color: #bfdbfe;
}

.control-card {
  padding: 14px;
}

.control-section {
  padding-bottom: 16px;
  margin-bottom: 16px;
  border-bottom: 1px solid rgba(148, 163, 184, 0.16);
}

.muted {
  margin-top: 6px;
  color: #94a3b8;
  font-size: 12px;
}

.frame-note {
  margin-top: 10px;
  color: #a8bacb;
  font-size: 11px;
  line-height: 1.5;
}

.axis-cyan { color: #22d3ee; }
.axis-green { color: #4ade80; }
.axis-yellow { color: #facc15; }

.control-section label {
  display: grid;
  grid-template-columns: 54px 1fr 58px;
  align-items: center;
  gap: 8px;
  margin-top: 10px;
  color: #cbd5e1;
  font-size: 13px;
}

input[type="range"] {
  width: 100%;
  accent-color: #22d3ee;
}

.actions {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 8px;
  margin-bottom: 12px;
}

.primary-action {
  border-color: rgba(52, 211, 153, 0.6);
  background: rgba(6, 95, 70, 0.72);
  color: #d1fae5;
}

.status-message {
  margin: 8px 0 12px;
  padding: 9px 10px;
  border: 1px solid rgba(34, 211, 238, 0.28);
  border-radius: 6px;
  background: rgba(8, 47, 73, 0.42);
  color: #bae6fd;
  font-size: 12px;
}

pre {
  max-height: 340px;
  overflow: auto;
  padding: 12px;
  border: 1px solid rgba(148, 163, 184, 0.18);
  border-radius: 6px;
  background: #020617;
  color: #bfdbfe;
  font-size: 11px;
}

@media (max-width: 1180px) {
  .calibration-layout {
    grid-template-columns: 1fr;
  }

  .safety-pill {
    white-space: normal;
  }

  .keyboard-hint {
    width: 100%;
    margin-left: 0;
  }
}
</style>
