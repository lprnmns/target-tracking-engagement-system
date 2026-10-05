<script setup lang="ts">
import { computed, ref, onMounted } from 'vue'
import { fetchHardwareStatus, testHardwareJog, testHardwareTrigger } from '../api/hardware'
import { fetchTrackingStatus, updateTrackingConfig as applyTrackingConfig } from '../api/tracking'
import DashboardCard from '../components/dashboard/DashboardCard.vue'
import MetricRow from '../components/dashboard/MetricRow.vue'
import StatusBadge from '../components/shared/StatusBadge.vue'

// State
const currentStep = ref(0)
const steps = [
  { id: 'welcome', title: 'Bağlantı Kontrolü' },
  { id: 'trigger', title: 'Tetik Testi' },
  { id: 'pan', title: 'X Ekseni (Sağ/Sol)' },
  { id: 'tilt', title: 'Y Ekseni (Aşağı/Yukarı)' },
  { id: 'finish', title: 'Kurulum Tamamlandı' }
]

const isBusy = ref(false)
const errorMsg = ref<string | null>(null)
const hardwareStatus = ref<any>(null)
const trackingStatus = ref<any>(null)

async function refreshStatus() {
  try {
    hardwareStatus.value = await fetchHardwareStatus()
    trackingStatus.value = await fetchTrackingStatus()
  } catch (err) {
    console.error(err)
  }
}

onMounted(() => {
  refreshStatus()
})

const isHardwareReady = computed(() => {
  return hardwareStatus.value?.port_open === true && hardwareStatus.value?.physical_command_enabled === true
})

const preflightItems = computed(() => [
  { label: 'Pico port açık', ok: hardwareStatus.value?.port_open === true, detail: hardwareStatus.value?.telemetry?.port ?? 'port yok' },
  { label: 'Pico telemetri', ok: hardwareStatus.value?.telemetry_received === true || hardwareStatus.value?.pico_verified === true, detail: hardwareStatus.value?.connection_state ?? 'bilinmiyor' },
  { label: 'E-stop okunuyor', ok: hardwareStatus.value?.telemetry?.estop_state !== null && hardwareStatus.value?.telemetry?.estop_state !== undefined, detail: String(hardwareStatus.value?.telemetry?.estop_state ?? 'unknown') },
  { label: 'X yön kalibrasyonu', ok: trackingStatus.value?.invert_x !== undefined, detail: trackingStatus.value?.invert_x ? 'ters çevrilmiş' : 'normal' },
  { label: 'Y yön kalibrasyonu', ok: trackingStatus.value?.invert_y !== undefined, detail: trackingStatus.value?.invert_y ? 'ters çevrilmiş' : 'normal' },
  { label: 'Komut yolu', ok: hardwareStatus.value?.physical_command_enabled === true, detail: hardwareStatus.value?.physical_command_enabled ? 'aktif' : 'kapalı' },
])

// --- Test Fonksiyonları ---

async function testTrigger() {
  errorMsg.value = null
  isBusy.value = true
  try {
    await testHardwareTrigger()
  } catch (err: any) {
    errorMsg.value = `Tetik testi başarısız: ${err.message}`
  } finally {
    isBusy.value = false
  }
}

async function testJog(speedX: number, speedY: number) {
  errorMsg.value = null
  isBusy.value = true
  try {
    await testHardwareJog({ speed_x: speedX, speed_y: speedY, duration_ms: 500 })
  } catch (err: any) {
    errorMsg.value = `Jog testi başarısız: ${err.message}`
  } finally {
    isBusy.value = false
  }
}

async function updateTrackingConfig(updates: any) {
  errorMsg.value = null
  isBusy.value = true
  try {
    const newStatus = await applyTrackingConfig(updates)
    trackingStatus.value = newStatus
  } catch (err: any) {
    errorMsg.value = `Ayar kaydedilemedi: ${err.message}`
  } finally {
    isBusy.value = false
  }
}

// İleri / Geri Navigasyon
function nextStep() {
  if (currentStep.value < steps.length - 1) currentStep.value++
}
function prevStep() {
  if (currentStep.value > 0) currentStep.value--
}
</script>

<template>
  <div class="grid gap-4 max-w-4xl mx-auto">
    <DashboardCard title="Yarışma Öncesi Preflight" subtitle="30 dakikalık hazırlıkta hızlı sağlık ve port kontrolü">
      <div class="grid gap-2 md:grid-cols-2">
        <div v-for="item in preflightItems" :key="item.label" class="rounded-md border border-white/8 bg-black/18 px-3 py-2">
          <div class="flex items-center justify-between gap-3">
            <span class="text-sm text-slate-200">{{ item.label }}</span>
            <StatusBadge :label="item.ok ? 'OK' : 'KONTROL'" :tone="item.ok ? 'good' : 'warn'" />
          </div>
          <p class="mt-1 break-words font-mono text-xs text-slate-500">{{ item.detail }}</p>
        </div>
      </div>
      <div class="mt-4 grid gap-2 md:grid-cols-3">
        <MetricRow label="Bağlantı" :value="hardwareStatus?.connection_state ?? 'unknown'" />
        <MetricRow label="Firmware" :value="hardwareStatus?.telemetry?.firmware_version ?? 'unknown'" />
        <MetricRow label="Heartbeat" :value="hardwareStatus?.telemetry?.heartbeat_age_ms === null || hardwareStatus?.telemetry?.heartbeat_age_ms === undefined ? 'n/a' : `${hardwareStatus.telemetry.heartbeat_age_ms} ms`" />
      </div>
      <button class="focus-ring mt-4 rounded-md bg-slate-700 px-3 py-2 text-sm font-semibold text-white" @click="refreshStatus">
        Durumu Yenile
      </button>
    </DashboardCard>

    <DashboardCard title="Donanım Kurulum ve Test Sihirbazı" subtitle="Fiziksel donanım bağlantılarını interaktif olarak doğrulayın.">

      <!-- Stepper Header -->
      <div class="flex items-center justify-between mb-8 overflow-x-auto pb-2">
        <div
          v-for="(step, idx) in steps"
          :key="step.id"
          class="flex items-center gap-2"
        >
          <div
            class="flex items-center justify-center w-8 h-8 rounded-full border-2 text-sm font-bold transition-colors"
            :class="idx === currentStep ? 'border-cyan-400 text-cyan-400 bg-cyan-400/10' : idx < currentStep ? 'border-emerald-500 text-emerald-500 bg-emerald-500/10' : 'border-slate-600 text-slate-500'"
          >
            {{ idx < currentStep ? '✓' : idx + 1 }}
          </div>
          <span class="text-sm font-medium hidden md:block" :class="idx <= currentStep ? 'text-slate-200' : 'text-slate-600'">
            {{ step.title }}
          </span>
          <div v-if="idx < steps.length - 1" class="w-8 md:w-12 h-px bg-slate-700 mx-2"></div>
        </div>
      </div>

      <!-- Hata Gösterimi -->
      <div v-if="errorMsg" class="mb-6 p-4 rounded-md bg-red-500/10 border border-red-500/30 text-red-200 text-sm">
        <p class="font-bold">Hata Oluştu</p>
        <p>{{ errorMsg }}</p>
      </div>

      <!-- ADIM 1: Hoşgeldiniz -->
      <div v-if="currentStep === 0" class="min-h-[200px] flex flex-col justify-center items-center text-center">
        <h2 class="text-2xl font-bold text-white mb-4">Sisteme Hoşgeldiniz</h2>
        <p class="text-slate-400 max-w-lg mb-6">
          Bu sihirbaz, Pico ve kamera bağlantılarınızı test etmenizi ve ters bağlanan eksenleri yazılımsal olarak tek tuşla düzeltmenizi sağlar. Lütfen silah mekanizmasının güvenli bir yöne baktığından emin olun.
        </p>
        <div class="flex gap-4">
          <StatusBadge :label="isHardwareReady ? 'Pico Bağlantısı: AÇIK' : 'Pico Bağlantısı: KAPALI / YETKİSİZ'" :tone="isHardwareReady ? 'good' : 'bad'" class="text-sm" />
          <StatusBadge :label="hardwareStatus?.telemetry_received ? 'Telemetri: AKTİF' : 'Telemetri: YOK'" :tone="hardwareStatus?.telemetry_received ? 'good' : 'warn'" class="text-sm" />
        </div>
      </div>

      <!-- ADIM 2: Tetik Testi -->
      <div v-else-if="currentStep === 1" class="min-h-[200px] flex flex-col justify-center items-center text-center">
        <h2 class="text-xl font-bold text-white mb-2">Adım 1: Tetik Mekanizması</h2>
        <p class="text-slate-400 max-w-lg mb-8">
          Aşağıdaki butona bastığınızda servo motor tetiği (155 derece) çekecek ve 1 saniye sonra bırakacaktır.
        </p>

        <button
          @click="testTrigger"
          :disabled="isBusy"
          class="px-8 py-4 bg-red-600 hover:bg-red-500 active:bg-red-700 text-white font-bold rounded-lg shadow-[0_0_20px_rgba(220,38,38,0.4)] transition-all mb-8 disabled:opacity-50"
        >
          {{ isBusy ? 'Test Ediliyor...' : 'TETİĞİ TEST ET (LZR,1)' }}
        </button>

        <div class="flex flex-col gap-3 w-full max-w-md bg-black/20 p-4 rounded-xl border border-white/10">
          <p class="font-semibold text-slate-300">Servo (Tetik) sorunsuz çalıştı mı?</p>
          <div class="grid grid-cols-2 gap-3">
            <button @click="nextStep" class="px-4 py-2 bg-emerald-500/20 hover:bg-emerald-500/30 text-emerald-400 border border-emerald-500/30 rounded-md font-semibold transition-colors">Evet, Çalıştı</button>
            <button @click="errorMsg = 'Lütfen GP15 pininin bağlı olduğundan, bataryanın açık olduğundan ve Pico\'nun kilitli olmadığından emin olun.'" class="px-4 py-2 bg-slate-800 hover:bg-slate-700 text-slate-300 border border-slate-600 rounded-md font-semibold transition-colors">Hayır, Çalışmadı</button>
          </div>
        </div>
      </div>

      <!-- ADIM 3: Pan (X) Testi -->
      <div v-else-if="currentStep === 2" class="min-h-[200px] flex flex-col justify-center items-center text-center">
        <h2 class="text-xl font-bold text-white mb-2">Adım 2: Pan (X Ekseni) Testi</h2>
        <p class="text-slate-400 max-w-lg mb-4">
          Motorların sağa/sola hareketini test edeceğiz. Aşağıdaki butona bastığınızda sistem motoru **sola** döndürmeye çalışacaktır.
        </p>
        <p class="text-xs text-amber-200 bg-amber-400/10 border border-amber-400/20 px-3 py-2 rounded mb-8">
          Mevcut Invert Durumu: {{ trackingStatus?.invert_x ? 'Ters Çevrilmiş (True)' : 'Normal (False)' }}
        </p>

        <button
          @click="testJog(50, 0)"
          :disabled="isBusy"
          class="px-8 py-4 bg-cyan-600 hover:bg-cyan-500 active:bg-cyan-700 text-white font-bold rounded-lg shadow-[0_0_20px_rgba(8,145,178,0.4)] transition-all mb-8 disabled:opacity-50 flex items-center gap-2"
        >
          <span class="text-2xl">⬅</span>
          {{ isBusy ? 'Test Ediliyor...' : 'SOLA DÖNDÜR (SPD, 50)' }}
        </button>

        <div class="flex flex-col gap-3 w-full max-w-md bg-black/20 p-4 rounded-xl border border-white/10">
          <p class="font-semibold text-slate-300">Kamera ne yöne döndü?</p>
          <div class="grid grid-cols-2 gap-3">
            <button @click="nextStep" class="px-4 py-2 bg-emerald-500/20 hover:bg-emerald-500/30 text-emerald-400 border border-emerald-500/30 rounded-md font-semibold transition-colors flex flex-col items-center">
              <span>Sola (Doğru)</span>
              <span class="text-[10px] opacity-70">Devam Et</span>
            </button>
            <button @click="updateTrackingConfig({ invert_x: !trackingStatus?.invert_x })" class="px-4 py-2 bg-amber-500/20 hover:bg-amber-500/30 text-amber-400 border border-amber-500/30 rounded-md font-semibold transition-colors flex flex-col items-center">
              <span>Sağa (Ters)</span>
              <span class="text-[10px] opacity-70">X Yönünü Tersine Çevir</span>
            </button>
          </div>
        </div>
      </div>

      <!-- ADIM 4: Tilt (Y) Testi -->
      <div v-else-if="currentStep === 3" class="min-h-[200px] flex flex-col justify-center items-center text-center">
        <h2 class="text-xl font-bold text-white mb-2">Adım 3: Tilt (Y Ekseni) Testi</h2>
        <p class="text-slate-400 max-w-lg mb-4">
          Motorların aşağı/yukarı hareketini test edeceğiz. Aşağıdaki butona bastığınızda sistem motoru **yukarı (negatif yöne)** döndürmeye çalışacaktır.
        </p>
        <p class="text-xs text-amber-200 bg-amber-400/10 border border-amber-400/20 px-3 py-2 rounded mb-8">
          Mevcut Invert Durumu: {{ trackingStatus?.invert_y ? 'Ters Çevrilmiş (True)' : 'Normal (False)' }}
        </p>

        <button
          @click="testJog(0, -30)"
          :disabled="isBusy"
          class="px-8 py-4 bg-purple-600 hover:bg-purple-500 active:bg-purple-700 text-white font-bold rounded-lg shadow-[0_0_20px_rgba(147,51,234,0.4)] transition-all mb-8 disabled:opacity-50 flex items-center gap-2"
        >
          <span class="text-2xl">⬆</span>
          {{ isBusy ? 'Test Ediliyor...' : 'YUKARI DÖNDÜR (SPD, -30)' }}
        </button>

        <div class="flex flex-col gap-3 w-full max-w-md bg-black/20 p-4 rounded-xl border border-white/10">
          <p class="font-semibold text-slate-300">Kamera ne yöne döndü?</p>
          <div class="grid grid-cols-2 gap-3">
            <button @click="nextStep" class="px-4 py-2 bg-emerald-500/20 hover:bg-emerald-500/30 text-emerald-400 border border-emerald-500/30 rounded-md font-semibold transition-colors flex flex-col items-center">
              <span>Yukarı (Doğru)</span>
              <span class="text-[10px] opacity-70">Devam Et</span>
            </button>
            <button @click="updateTrackingConfig({ invert_y: !trackingStatus?.invert_y })" class="px-4 py-2 bg-amber-500/20 hover:bg-amber-500/30 text-amber-400 border border-amber-500/30 rounded-md font-semibold transition-colors flex flex-col items-center">
              <span>Aşağı (Ters)</span>
              <span class="text-[10px] opacity-70">Y Yönünü Tersine Çevir</span>
            </button>
          </div>
        </div>
      </div>

      <!-- ADIM 5: Bitiş -->
      <div v-else-if="currentStep === 4" class="min-h-[200px] flex flex-col justify-center items-center text-center">
        <div class="w-20 h-20 bg-emerald-500/20 border-2 border-emerald-500 rounded-full flex items-center justify-center text-4xl text-emerald-400 shadow-[0_0_30px_rgba(16,185,129,0.3)] mb-6">
          ✓
        </div>
        <h2 class="text-2xl font-bold text-white mb-2">Tebrikler!</h2>
        <p class="text-slate-400 max-w-lg mb-8">
          Tüm fiziksel donanımlar doğrulandı ve motor yönleri kalibre edildi. Artık güvenle otonom takibi başlatabilirsiniz.
        </p>
        <router-link
          to="/motion"
          class="px-6 py-3 bg-cyan-600 hover:bg-cyan-500 text-white font-bold rounded-lg shadow-[0_0_15px_rgba(8,145,178,0.4)] transition-all"
        >
          Takip Ekranına Git
        </router-link>
      </div>

      <!-- Stepper Controls (Bottom) -->
      <div class="mt-8 pt-4 border-t border-white/10 flex justify-between">
        <button
          v-if="currentStep > 0 && currentStep < steps.length - 1"
          @click="prevStep"
          class="px-4 py-2 bg-slate-800 hover:bg-slate-700 text-white rounded-md text-sm font-semibold"
        >
          Geri
        </button>
        <div v-else></div> <!-- Spacer -->

        <button
          v-if="currentStep === 0"
          @click="nextStep"
          class="px-6 py-2 bg-cyan-600 hover:bg-cyan-500 text-white rounded-md text-sm font-bold shadow-lg"
        >
          Teste Başla
        </button>
      </div>

    </DashboardCard>
  </div>
</template>
