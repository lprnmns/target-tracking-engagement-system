import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { exportInterfaceInventory, fetchInterfaceInventory, fetchInterfaceKtrSection } from '../api/interfaces'
import type { InterfaceExportRecord, InterfaceInventoryResponse, InterfaceKtrSection, InterfaceRecord } from '../types/interfaces'

const defaultInventory: InterfaceInventoryResponse = {
  generated_at: 0,
  interfaces: [],
  categories: {},
  no_physical_command_generated: true,
}

export const useInterfacesStore = defineStore('interfaces', () => {
  const inventory = ref<InterfaceInventoryResponse>(defaultInventory)
  const ktr = ref<InterfaceKtrSection | null>(null)
  const selected = ref<InterfaceRecord | null>(null)
  const latestExport = ref<InterfaceExportRecord | null>(null)
  const error = ref<string | null>(null)
  const isExporting = ref(false)

  const safetyCriticalCount = computed(() => inventory.value.interfaces.filter((item) => item.safety_boundary.toLowerCase().includes('safety') || item.safety_boundary.toLowerCase().includes('physical')).length)

  async function refresh(): Promise<void> {
    error.value = null
    try {
      const [nextInventory, nextKtr] = await Promise.all([fetchInterfaceInventory(), fetchInterfaceKtrSection()])
      inventory.value = nextInventory
      ktr.value = nextKtr
      selected.value ??= nextInventory.interfaces[0] ?? null
    } catch (caught) {
      error.value = caught instanceof Error ? caught.message : 'Interface inventory refresh failed'
    }
  }

  async function exportInventory(): Promise<void> {
    error.value = null
    isExporting.value = true
    try {
      latestExport.value = await exportInterfaceInventory()
    } catch (caught) {
      error.value = caught instanceof Error ? caught.message : 'Interface inventory export failed'
    } finally {
      isExporting.value = false
    }
  }

  return {
    inventory,
    ktr,
    selected,
    latestExport,
    error,
    isExporting,
    safetyCriticalCount,
    refresh,
    exportInventory,
  }
})
