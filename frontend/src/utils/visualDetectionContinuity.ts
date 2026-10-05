import type { BalloonDetection, BodyDetection, VisionEvent } from '../types/vision'

export interface VisualContinuityMeta {
  visual_track_key: string
  visual_live: boolean
  visual_age_ms: number
  visual_logical_name?: string | null
}

export type VisualBodyDetection = BodyDetection & VisualContinuityMeta
export type VisualBalloonDetection = BalloonDetection & VisualContinuityMeta

export interface VisualContinuitySnapshot {
  bodies: VisualBodyDetection[]
  balloons: VisualBalloonDetection[]
  heldCount: number
}

type VisualTrack<T> = {
  key: string
  value: T
  lastSeenAt: number
  live: boolean
  backendTrackId: number | null
  classHistory: string[]
  teamHistory: string[]
}

const DEFAULT_HOLD_MS = 900
const CLASS_HISTORY_SIZE = 12

function center(value: { bbox: { x: number, y: number, w: number, h: number } }): [number, number] {
  return [value.bbox.x + value.bbox.w / 2, value.bbox.y + value.bbox.h / 2]
}

function area(value: { bbox: { w: number, h: number } }): number {
  return Math.max(1, value.bbox.w * value.bbox.h)
}

function intersectionOverUnion(
  first: { bbox: { x: number, y: number, w: number, h: number } },
  second: { bbox: { x: number, y: number, w: number, h: number } },
): number {
  const left = Math.max(first.bbox.x, second.bbox.x)
  const top = Math.max(first.bbox.y, second.bbox.y)
  const right = Math.min(first.bbox.x + first.bbox.w, second.bbox.x + second.bbox.w)
  const bottom = Math.min(first.bbox.y + first.bbox.h, second.bbox.y + second.bbox.h)
  const overlap = Math.max(0, right - left) * Math.max(0, bottom - top)
  return overlap / Math.max(1, area(first) + area(second) - overlap)
}

function majority(values: string[], fallback: string): string {
  const counts = new Map<string, number>()
  values.forEach((value) => counts.set(value, (counts.get(value) ?? 0) + 1))
  let selected = fallback
  let selectedCount = -1
  // Traverse from newest to oldest so an equal vote resolves to the newest
  // detector evidence instead of keeping an arbitrary map insertion order.
  for (const value of [...values].reverse()) {
    const count = counts.get(value) ?? 0
    if (count > selectedCount) {
      selected = value
      selectedCount = count
    }
  }
  return selected
}

function pushBounded(values: string[], value: string): string[] {
  const next = [...values, value]
  return next.slice(Math.max(0, next.length - CLASS_HISTORY_SIZE))
}

/**
 * UI-only continuity for camera overlays and the digital twin.
 *
 * It never feeds a held bbox back to operation selection, motion, tracking or
 * FIRE. A missing detection remains visible as a dim dashed "last seen" item
 * for less than one second, but is explicitly non-selectable.
 */
export class VisualDetectionContinuity {
  private bodyTracks = new Map<string, VisualTrack<VisualBodyDetection>>()
  private balloonTracks = new Map<string, VisualTrack<VisualBalloonDetection>>()
  private nextBodyId = 1
  private nextBalloonId = 1
  private lastFrameId: number | null = null
  private sourceIdentity = ''
  private readonly holdMs: number

  constructor(holdMs = DEFAULT_HOLD_MS) {
    this.holdMs = holdMs
  }

  reset(): void {
    this.bodyTracks.clear()
    this.balloonTracks.clear()
    this.nextBodyId = 1
    this.nextBalloonId = 1
    this.lastFrameId = null
    this.sourceIdentity = ''
  }

  update(event: VisionEvent | null, names: Record<string, string> = {}, now = Date.now()): VisualContinuitySnapshot {
    if (!event || event.frame_id <= 0) return this.snapshot(now)
    const identity = [
      event.source,
      event.camera_device_path ?? '',
      event.frame_width ?? 0,
      event.frame_height ?? 0,
    ].join('|')
    if (this.sourceIdentity && identity !== this.sourceIdentity) this.reset()
    if (this.lastFrameId !== null && event.frame_id < this.lastFrameId) this.reset()
    this.sourceIdentity = identity
    if (event.frame_id !== this.lastFrameId) {
      this.processBodies(event.body_detections, names, event, now)
      this.processBalloons(event.balloon_detections, names, event, now)
      this.lastFrameId = event.frame_id
    }
    return this.snapshot(now)
  }

  private processBodies(
    detections: BodyDetection[],
    names: Record<string, string>,
    event: VisionEvent,
    now: number,
  ): void {
    this.bodyTracks.forEach((track) => { track.live = false })
    const diagonal = Math.hypot(event.frame_width ?? 1280, event.frame_height ?? 720)
    const maxDistance = Math.max(80, diagonal * 0.11)
    const tracks = [...this.bodyTracks.values()]
    const candidates: Array<{ cost: number, track: VisualTrack<VisualBodyDetection>, index: number }> = []
    tracks.forEach((track) => {
      const [tx, ty] = center(track.value)
      detections.forEach((detection, index) => {
        const [dx, dy] = center(detection)
        const distance = Math.hypot(dx - tx, dy - ty)
        const sameBackendTrack = detection.track_id !== null && detection.track_id !== undefined
          && track.backendTrackId === detection.track_id
        const sameClass = detection.class_name === track.value.class_name
        const overlap = intersectionOverUnion(track.value, detection)
        const closeClassJitter = overlap >= 0.18 || distance <= diagonal * 0.035
        if (!sameBackendTrack && (distance > maxDistance || (!sameClass && !closeClassJitter))) return
        const classPenalty = sameClass ? 0 : maxDistance * 0.35
        candidates.push({ cost: sameBackendTrack ? -1 : distance + classPenalty - overlap * 20, track, index })
      })
    })
    const usedTracks = new Set<string>()
    const usedDetections = new Set<number>()
    for (const candidate of candidates.sort((a, b) => a.cost - b.cost)) {
      if (usedTracks.has(candidate.track.key) || usedDetections.has(candidate.index)) continue
      this.refreshBodyTrack(candidate.track, detections[candidate.index], names, now)
      usedTracks.add(candidate.track.key)
      usedDetections.add(candidate.index)
    }
    detections.forEach((detection, index) => {
      if (usedDetections.has(index)) return
      const key = `visual-body-${this.nextBodyId++}`
      const value: VisualBodyDetection = {
        ...detection,
        visual_track_key: key,
        visual_live: true,
        visual_age_ms: 0,
        visual_logical_name: names[`body:${detection.id}`] ?? null,
      }
      this.bodyTracks.set(key, {
        key,
        value,
        lastSeenAt: now,
        live: true,
        backendTrackId: detection.track_id ?? null,
        classHistory: [detection.class_name],
        teamHistory: detection.target_team ? [detection.target_team] : [],
      })
    })
  }

  private refreshBodyTrack(
    track: VisualTrack<VisualBodyDetection>,
    detection: BodyDetection,
    names: Record<string, string>,
    now: number,
  ): void {
    track.classHistory = pushBounded(track.classHistory, detection.class_name)
    if (detection.target_team) track.teamHistory = pushBounded(track.teamHistory, detection.target_team)
    const stableClass = majority(track.classHistory, detection.class_name)
    const stableTeam = majority(track.teamHistory, detection.target_team ?? 'unknown')
    track.value = {
      ...detection,
      class_name: stableClass,
      target_team: stableTeam,
      visual_track_key: track.key,
      visual_live: true,
      visual_age_ms: 0,
      visual_logical_name: names[`body:${detection.id}`] ?? track.value.visual_logical_name ?? null,
    }
    track.backendTrackId = detection.track_id ?? track.backendTrackId
    track.lastSeenAt = now
    track.live = true
  }

  private processBalloons(
    detections: BalloonDetection[],
    names: Record<string, string>,
    event: VisionEvent,
    now: number,
  ): void {
    this.balloonTracks.forEach((track) => { track.live = false })
    const diagonal = Math.hypot(event.frame_width ?? 1280, event.frame_height ?? 720)
    const maxDistance = Math.max(70, diagonal * 0.09)
    const tracks = [...this.balloonTracks.values()]
    const candidates: Array<{ cost: number, track: VisualTrack<VisualBalloonDetection>, index: number }> = []
    tracks.forEach((track) => {
      const [tx, ty] = center(track.value)
      detections.forEach((detection, index) => {
        const [dx, dy] = center(detection)
        const distance = Math.hypot(dx - tx, dy - ty)
        const sizeRatio = area(detection) / area(track.value)
        if (distance > maxDistance || sizeRatio < 0.18 || sizeRatio > 5.5) return
        candidates.push({ cost: distance - intersectionOverUnion(track.value, detection) * 24, track, index })
      })
    })
    const usedTracks = new Set<string>()
    const usedDetections = new Set<number>()
    for (const candidate of candidates.sort((a, b) => a.cost - b.cost)) {
      if (usedTracks.has(candidate.track.key) || usedDetections.has(candidate.index)) continue
      const detection = detections[candidate.index]
      candidate.track.value = {
        ...detection,
        visual_track_key: candidate.track.key,
        visual_live: true,
        visual_age_ms: 0,
        visual_logical_name: names[`balloon:${detection.id}`] ?? candidate.track.value.visual_logical_name ?? null,
      }
      candidate.track.lastSeenAt = now
      candidate.track.live = true
      usedTracks.add(candidate.track.key)
      usedDetections.add(candidate.index)
    }
    detections.forEach((detection, index) => {
      if (usedDetections.has(index)) return
      const key = `visual-balloon-${this.nextBalloonId++}`
      const value: VisualBalloonDetection = {
        ...detection,
        visual_track_key: key,
        visual_live: true,
        visual_age_ms: 0,
        visual_logical_name: names[`balloon:${detection.id}`] ?? null,
      }
      this.balloonTracks.set(key, {
        key,
        value,
        lastSeenAt: now,
        live: true,
        backendTrackId: null,
        classHistory: [],
        teamHistory: [],
      })
    })
  }

  private snapshot(now: number): VisualContinuitySnapshot {
    const bodies = this.collect(this.bodyTracks, now)
    const balloons = this.collect(this.balloonTracks, now)
    return {
      bodies,
      balloons,
      heldCount: [...bodies, ...balloons].filter((item) => !item.visual_live).length,
    }
  }

  private collect<T extends BodyDetection | BalloonDetection>(
    tracks: Map<string, VisualTrack<T & VisualContinuityMeta>>,
    now: number,
  ): Array<T & VisualContinuityMeta> {
    const result: Array<T & VisualContinuityMeta> = []
    for (const [key, track] of tracks) {
      const age = Math.max(0, now - track.lastSeenAt)
      if (age > this.holdMs) {
        tracks.delete(key)
        continue
      }
      result.push({
        ...track.value,
        visual_live: track.live,
        visual_age_ms: Math.round(age),
      })
    }
    return result
  }
}
