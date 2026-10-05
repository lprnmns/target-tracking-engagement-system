import { defineStore } from 'pinia'
import { ref } from 'vue'
import {
  classifyColorSample,
  fetchColorConfig,
  fetchLatestColorDecision,
  previewColorMask,
  resetColor,
  updateColorConfig,
} from '../api/color'
import type {
  ColorClassifierConfig,
  ColorClassifySampleRequest,
  ColorDecisionResult,
  MaskPreviewResult,
} from '../types/color'

const defaultConfig: ColorClassifierConfig = {
  color_space: 'HSV',
  enemy_hsv_ranges: [
    { h_min: 0, h_max: 12, s_min: 70, v_min: 50 },
    { h_min: 170, h_max: 180, s_min: 70, v_min: 50 },
  ],
  friend_hsv_ranges: [{ h_min: 95, h_max: 130, s_min: 60, v_min: 40 }],
  saturation_min: 70,
  value_min: 50,
  lab_enabled: false,
  min_body_pixels: 200,
  decision_threshold: 0.55,
  temporal_window: 5,
  required_consistent_frames: 3,
  balloon_mask_enabled: true,
  balloon_hsv_ranges: [
    { h_min: 0, h_max: 12, s_min: 70, v_min: 50 },
    { h_min: 170, h_max: 180, s_min: 70, v_min: 50 },
  ],
  morphology_kernel: 3,
  updated_at: 0,
}

export const useColorStore = defineStore('color', () => {
  const config = ref<ColorClassifierConfig>({ ...defaultConfig })
  const latest = ref<ColorDecisionResult | null>(null)
  const maskPreview = ref<MaskPreviewResult | null>(null)
  const error = ref<string | null>(null)

  async function refresh(): Promise<void> {
    try {
      const [nextConfig, nextLatest] = await Promise.all([fetchColorConfig(), fetchLatestColorDecision()])
      config.value = nextConfig
      latest.value = nextLatest
      error.value = null
    } catch (caught) {
      error.value = caught instanceof Error ? caught.message : 'Color refresh failed'
    }
  }

  function applyConfig(nextConfig: ColorClassifierConfig): void {
    config.value = nextConfig
  }

  function applyDecision(result: ColorDecisionResult): void {
    latest.value = result
  }

  function applyMaskPreview(result: MaskPreviewResult): void {
    maskPreview.value = result
  }

  async function saveConfig(nextConfig: ColorClassifierConfig): Promise<void> {
    config.value = await updateColorConfig(nextConfig)
  }

  async function classify(request: ColorClassifySampleRequest): Promise<void> {
    latest.value = await classifyColorSample(request)
    maskPreview.value = {
      frame_id: latest.value.frame_id,
      detection_id: latest.value.detection_id,
      balloon_mask_enabled: config.value.balloon_mask_enabled,
      balloon_mask_applied: latest.value.balloon_mask_applied,
      debug_masks_available: latest.value.debug_masks_available,
      warnings: latest.value.blocking_warnings,
      updated_at: latest.value.updated_at,
    }
  }

  async function preview(request: ColorClassifySampleRequest): Promise<void> {
    maskPreview.value = await previewColorMask(request)
  }

  async function reset(): Promise<void> {
    await resetColor()
    latest.value = null
    maskPreview.value = null
  }

  return { config, latest, maskPreview, error, refresh, applyConfig, applyDecision, applyMaskPreview, saveConfig, classify, preview, reset }
})
