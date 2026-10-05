import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import {
  activateModelPackage,
  applyRecommendedModelSettings,
  benchmarkModelPackage,
  deactivateModelPackage,
  fetchModelPackages,
  importModelPackage,
  testModelPackage,
  validateModelPackage,
} from '../api/modelPackages'
import type { ModelPackageRecord } from '../types/modelPackage'

export const useModelPackageStore = defineStore('modelPackages', () => {
  const packages = ref<ModelPackageRecord[]>([])
  const selectedModelId = ref<string | null>(null)
  const importPath = ref('backend/tests/fixtures/model_packages/opencv_test_adapter_package')
  const lastResult = ref<unknown | null>(null)
  const error = ref<string | null>(null)
  const isBusy = ref(false)

  const activePackage = computed(() => packages.value.find((item) => item.active) ?? null)
  const selectedPackage = computed(() => packages.value.find((item) => item.model_id === selectedModelId.value) ?? activePackage.value ?? packages.value[0] ?? null)

  async function refresh(): Promise<void> {
    error.value = null
    try {
      packages.value = await fetchModelPackages()
      if (!selectedModelId.value && packages.value.length > 0) selectedModelId.value = packages.value[0].model_id
    } catch (caught) {
      error.value = caught instanceof Error ? caught.message : 'Model package refresh failed'
    }
  }

  async function run(action: () => Promise<unknown>): Promise<void> {
    isBusy.value = true
    error.value = null
    try {
      lastResult.value = await action()
      await refresh()
    } catch (caught) {
      error.value = caught instanceof Error ? caught.message : 'Model package action failed'
    } finally {
      isBusy.value = false
    }
  }

  const importSelected = () => run(() => importModelPackage(importPath.value))
  const validateSelected = () => selectedPackage.value ? run(() => validateModelPackage(selectedPackage.value!.model_id)) : Promise.resolve()
  const activateSelected = () => selectedPackage.value ? run(() => activateModelPackage(selectedPackage.value!.model_id)) : Promise.resolve()
  const deactivateSelected = () => selectedPackage.value ? run(() => deactivateModelPackage(selectedPackage.value!.model_id)) : Promise.resolve()
  const testSelected = () => selectedPackage.value ? run(() => testModelPackage(selectedPackage.value!.model_id)) : Promise.resolve()
  const benchmarkSelected = () => selectedPackage.value ? run(() => benchmarkModelPackage(selectedPackage.value!.model_id)) : Promise.resolve()
  const applyRecommended = () => selectedPackage.value ? run(() => applyRecommendedModelSettings(selectedPackage.value!.model_id)) : Promise.resolve()

  return {
    packages,
    selectedModelId,
    importPath,
    lastResult,
    error,
    isBusy,
    activePackage,
    selectedPackage,
    refresh,
    importSelected,
    validateSelected,
    activateSelected,
    deactivateSelected,
    testSelected,
    benchmarkSelected,
    applyRecommended,
  }
})
