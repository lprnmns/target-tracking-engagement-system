/**
 * Low-priority browser warm-up for the operator digital twin.
 *
 * This is deliberately fire-and-forget: it never owns a Three.js scene and
 * never touches camera/Pico state.  The browser HTTP cache is shared with
 * GLTFLoader when the cockpit mounts, so an idle visit to the landing page can
 * remove the large first-view download without delaying the live preflight.
 */

const OPERATOR_ASSET = '/assets/digital-twin/ktr1_kinematic_world_phase55_draco.glb?v=operator-draco-node-safe-20260818'
const OPERATOR_MANIFEST = '/assets/digital-twin/ktr1_kinematic_world_phase55_manifest.json'
const KINEMATICS = '/assets/digital-twin/ktr1_kinematics.json'
const PHASE56_ASSETS = [
  '/assets/digital-twin/ktr1_device_frame.json',
  '/assets/digital-twin/ktr1_mechanical_groups.json',
  '/assets/digital-twin/ktr1_joint_calibration.json',
]

let scheduled = false
let metadataStarted = false
let modelWarmupStarted = false
const MODEL_WARMUP_DELAY_MS = 1200

type IdleDeadlineLike = { timeRemaining: () => number }
type IdleCallback = (callback: (deadline: IdleDeadlineLike) => void, options?: { timeout: number }) => number

function scheduleIdle(callback: () => void): void {
  const idle = (window as Window & { requestIdleCallback?: IdleCallback }).requestIdleCallback
  if (idle) {
    idle(() => callback(), { timeout: 5000 })
    return
  }
  window.setTimeout(callback, 2500)
}

function shouldPreload(): boolean {
  if (typeof window === 'undefined' || typeof fetch === 'undefined') return false
  if (document.hidden) return false
  const connection = (navigator as Navigator & { connection?: { saveData?: boolean, effectiveType?: string } }).connection
  if (connection?.saveData) return false
  // Avoid competing with the user's live startup on a constrained mobile or
  // 2G/3G link.  A normal LAN/desktop connection is the intended target.
  if (connection?.effectiveType === 'slow-2g' || connection?.effectiveType === '2g') return false
  return true
}

async function warmAsset(url: string): Promise<void> {
  try {
    const response = await fetch(url, { cache: 'force-cache', credentials: 'same-origin' })
    if (!response.ok) return
    // Complete the response so the browser can commit it to its HTTP cache.
    // The temporary ArrayBuffer is released after this task; no scene or
    // decoded geometry is retained here.
    await response.arrayBuffer()
  } catch {
    // Preload is an optimization only. Cockpit loading remains authoritative.
  }
}

export function preloadDigitalTwinAssets(): void {
  if (scheduled || !shouldPreload()) return
  scheduled = true
  scheduleIdle(() => {
    if (metadataStarted || !shouldPreload()) return
    metadataStarted = true
    // Metadata is tiny and safe to warm immediately. Do not import Three.js
    // or read the 29 MB GLB in the landing-page idle callback: on a remote or
    // CPU-bound Windows machine that work can monopolize the main thread and
    // leave the entire landing screen black until the request finishes.
    void Promise.allSettled([
      warmAsset(OPERATOR_MANIFEST),
      warmAsset(KINEMATICS),
      ...PHASE56_ASSETS.map(warmAsset),
    ])
    // Keep the cache warm-up optimization, but only after the operator has
    // had a visible landing page for a while. If the user enters the cockpit
    // earlier, GLTFLoader owns the authoritative load and shows its progress
    // overlay; there is no correctness dependency on this timer.
    window.setTimeout(() => {
      if (modelWarmupStarted || !shouldPreload()) return
      modelWarmupStarted = true
      void Promise.allSettled([
        import('three'),
        import('three/examples/jsm/controls/OrbitControls.js'),
        warmAsset(OPERATOR_ASSET),
      ])
    }, MODEL_WARMUP_DELAY_MS)
  })
}

export const DIGITAL_TWIN_OPERATOR_ASSET_URL = OPERATOR_ASSET
