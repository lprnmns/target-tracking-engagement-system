import { ref } from 'vue'
import { defineStore } from 'pinia'
import { fetchActiveDeviceProfile, saveActiveDeviceProfile, verifyActiveDeviceProfile } from '../api/deviceProfiles'
import type { DeviceProfile, DeviceProfileResult } from '../types/deviceProfile'

export const useDeviceProfileStore = defineStore('deviceProfile', () => {
  const active = ref<DeviceProfile | null>(null)
  const lastResult = ref<DeviceProfileResult | null>(null)
  const error = ref<string | null>(null)

  async function refresh(): Promise<void> {
    try {
      active.value = await fetchActiveDeviceProfile()
    } catch (caught) {
      error.value = caught instanceof Error ? caught.message : 'Device profile refresh failed'
    }
  }

  async function save(): Promise<void> {
    lastResult.value = await saveActiveDeviceProfile()
    active.value = lastResult.value.profile
  }

  async function verify(): Promise<void> {
    lastResult.value = await verifyActiveDeviceProfile()
    active.value = lastResult.value.profile
  }

  return { active, lastResult, error, refresh, save, verify }
})
