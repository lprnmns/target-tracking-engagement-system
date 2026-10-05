<script setup lang="ts">
import type { ManagedDevice } from '../../types/deviceRuntime'

const props = defineProps<{
  cameras: ManagedDevice[]
  selectedDevice: string
  imageSettings: {
    brightness: number
    contrast: number
    saturation: number
    exposure: number
    exposureAuto: boolean
  }
  cameraStatus: string
}>()

const emit = defineEmits<{
  refresh: []
  connect: [deviceId: string]
  stop: []
  fullscreen: []
  updateImageSettings: [settings: { brightness: number, contrast: number, saturation: number, exposure: number, exposureAuto: boolean }]
  resetImageSettings: []
}>()

function update<K extends keyof typeof props.imageSettings>(key: K, value: number | boolean): void {
  emit('updateImageSettings', { ...props.imageSettings, [key]: value })
}
</script>

<template>
  <section class="operator-card">
    <div class="operator-card-header">
      <div>
        <h3>Kamera Kontrol</h3>
        <p>Kamera seçimi ve görüntü önizleme filtresi</p>
      </div>
      <span class="status">{{ props.cameraStatus }}</span>
    </div>

    <div class="camera-row">
      <select :value="props.selectedDevice" @change="emit('connect', ($event.target as HTMLSelectElement).value)">
        <option value="">Kamera seç</option>
        <option v-for="camera in props.cameras" :key="camera.device_id" :value="camera.device_id">
          {{ camera.name || camera.device_path }}
        </option>
      </select>
      <button @click="emit('refresh')">Yenile</button>
      <button @click="emit('connect', props.selectedDevice)">Bağlan</button>
      <button @click="emit('stop')">Durdur</button>
      <button @click="emit('fullscreen')">Tam ekran</button>
    </div>

    <div class="image-grid">
      <label>Parlaklık <input :value="props.imageSettings.brightness" type="range" min="-100" max="100" step="1" @input="update('brightness', Number(($event.target as HTMLInputElement).value))"></label>
      <label>Kontrast <input :value="props.imageSettings.contrast" type="range" min="-100" max="100" step="1" @input="update('contrast', Number(($event.target as HTMLInputElement).value))"></label>
      <label>Doygunluk <input :value="props.imageSettings.saturation" type="range" min="-100" max="100" step="1" @input="update('saturation', Number(($event.target as HTMLInputElement).value))"></label>
      <label>Pozlama <input :value="props.imageSettings.exposure" type="range" min="-100" max="100" step="1" :disabled="props.imageSettings.exposureAuto" @input="update('exposure', Number(($event.target as HTMLInputElement).value))"></label>
    </div>
    <div class="footer-row">
      <label class="check"><input :checked="props.imageSettings.exposureAuto" type="checkbox" @change="update('exposureAuto', ($event.target as HTMLInputElement).checked)"> Otomatik pozlama</label>
      <button @click="emit('resetImageSettings')">Görüntü ayarlarını sıfırla</button>
      <span>Önizleme filtresi</span>
    </div>
  </section>
</template>

<style scoped>
.operator-card { border: 1px solid rgba(34, 211, 238, 0.16); border-radius: 10px; background: rgba(3, 7, 18, 0.72); padding: 12px; color: #e2e8f0; }
.operator-card-header, .camera-row, .footer-row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.operator-card-header { justify-content: space-between; margin-bottom: 12px; }
h3 { margin: 0; font-size: 0.95rem; font-weight: 800; color: #f8fafc; }
p { margin: 3px 0 0; font-size: 0.72rem; color: #94a3b8; }
.status { border: 1px solid rgba(16, 185, 129, 0.24); border-radius: 999px; padding: 5px 8px; color: #a7f3d0; font-size: 0.72rem; font-weight: 800; }
select, button { border: 1px solid rgba(103, 232, 249, 0.24); border-radius: 7px; background: rgba(15, 23, 42, 0.88); color: #e0f2fe; padding: 7px 9px; font-size: 0.75rem; font-weight: 800; }
select { min-width: 220px; flex: 1; }
.image-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px 14px; margin-top: 12px; }
label { display: grid; gap: 5px; font-size: 0.72rem; font-weight: 800; color: #94a3b8; }
input[type="range"] { accent-color: #22d3ee; }
.footer-row { margin-top: 12px; font-size: 0.72rem; color: #94a3b8; }
.check { display: inline-flex; align-items: center; gap: 6px; }
.footer-row span { margin-left: auto; color: #fde68a; }
@media (max-width: 900px) { .image-grid { grid-template-columns: 1fr; } }
</style>
