<script setup lang="ts">
import { computed, onMounted } from 'vue'
import DashboardCard from '../components/dashboard/DashboardCard.vue'
import MetricRow from '../components/dashboard/MetricRow.vue'
import StatusBadge from '../components/shared/StatusBadge.vue'
import { useInterfacesStore } from '../stores/interfacesStore'

const interfaces = useInterfacesStore()

const categoryRows = computed(() => Object.entries(interfaces.inventory.categories).sort(([a], [b]) => a.localeCompare(b)))

function categoryLabel(value: string): string {
  const labels: Record<string, string> = {
    user_interface: 'Kullanıcı Arayüzü',
    rest_api: 'REST API Arayüzü',
    websocket: 'WebSocket Olay Arayüzü',
    mjpeg_stream: 'MJPEG Görüntü Akışı',
    camera_interface: 'Cihaz Keşif ve Kamera Arayüzü',
    vision_model_interface: 'Görüntü İşleme Model Arayüzü',
    pico_serial_telemetry: 'Pico Seri Telemetri Arayüzü',
    serial_protocol: 'Seri Protokol Arayüzü',
    safety_interface: 'Güvenlik Arayüzü',
    config_interface: 'Konfigürasyon Arayüzü',
    logging_interface: 'Loglama Arayüzü',
    dataset_replay_interface: 'Veri Seti ve Replay Arayüzü',
    report_export_interface: 'Rapor Dışa Aktarım Arayüzü',
    deployment_interface: 'Dağıtım/Çalıştırma Arayüzü',
    electronic_power_signal_interface_placeholder: 'Elektronik Güç/Sinyal Arayüz Tanımı',
  }
  return labels[value] ?? value.replaceAll('_', ' ').replace(/\b\w/g, (letter) => letter.toUpperCase())
}

async function copyKtr(): Promise<void> {
  if (!interfaces.ktr?.markdown) return
  await navigator.clipboard?.writeText(interfaces.ktr.markdown)
}

onMounted(() => {
  void interfaces.refresh()
})
</script>

<template>
  <div class="grid gap-4">
    <div class="grid gap-4 xl:grid-cols-3">
      <DashboardCard title="Interface Inventory" subtitle="KTR 4.3 source of truth">
        <MetricRow label="Interfaces" :value="interfaces.inventory.interfaces.length" />
        <MetricRow label="Categories" :value="categoryRows.length" />
        <MetricRow label="Safety boundaries" :value="interfaces.safetyCriticalCount" />
        <div class="mt-3 flex flex-wrap gap-2">
          <StatusBadge label="KTR READY TEXT" tone="good" />
          <StatusBadge label="NO PHYSICAL COMMAND" tone="bad" />
        </div>
      </DashboardCard>

      <DashboardCard title="Categories" subtitle="Inventory coverage">
        <div class="grid gap-2">
          <div v-for="[category, count] in categoryRows" :key="category" class="flex items-center justify-between border-t border-white/8 py-2 text-sm first:border-t-0">
            <span class="text-slate-300">{{ categoryLabel(category) }}</span>
            <StatusBadge :label="String(count)" tone="neutral" />
          </div>
        </div>
      </DashboardCard>

      <DashboardCard title="Export" subtitle="Markdown and JSON evidence">
        <button class="focus-ring rounded-md border border-cyan-400/40 bg-cyan-400/12 px-3 py-2 text-sm font-semibold text-cyan-100 disabled:opacity-50" :disabled="interfaces.isExporting" @click="interfaces.exportInventory()">
          Export interface inventory
        </button>
        <button class="focus-ring ml-2 rounded-md border border-white/15 px-3 py-2 text-sm text-slate-200" @click="copyKtr()">
          Copy KTR section
        </button>
        <MetricRow label="Latest export" :value="interfaces.latestExport?.export_id ?? 'none'" />
        <MetricRow label="Output" :value="interfaces.latestExport?.output_dir ?? 'not exported'" />
        <p v-if="interfaces.error" class="mt-2 rounded-md border border-red-400/30 bg-red-400/10 px-3 py-2 text-sm text-red-100">{{ interfaces.error }}</p>
      </DashboardCard>
    </div>

    <div class="grid gap-4 xl:grid-cols-[1.25fr_0.75fr]">
      <DashboardCard title="Interface Matrix" subtitle="Producer, consumer, protocol and safety boundary">
        <div class="overflow-auto">
          <table class="min-w-full text-left text-sm">
            <thead class="text-xs uppercase tracking-[0.16em] text-slate-500">
              <tr>
                <th class="px-3 py-2">Arayüz</th>
                <th class="px-3 py-2">Kategori</th>
                <th class="px-3 py-2">Üreten</th>
                <th class="px-3 py-2">Tüketen</th>
                <th class="px-3 py-2">Protokol</th>
                <th class="px-3 py-2">Güvenlik sınırı</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="item in interfaces.inventory.interfaces"
                :key="item.interface_id"
                class="cursor-pointer border-t border-white/8 hover:bg-white/5"
                :class="{ 'bg-cyan-400/8': interfaces.selected?.interface_id === item.interface_id }"
                @click="interfaces.selected = item"
              >
                <td class="px-3 py-2">
                  <p class="font-semibold text-white">{{ item.display_name ?? item.name }}</p>
                  <p class="font-mono text-xs text-slate-500">{{ item.name }}</p>
                </td>
                <td class="px-3 py-2 text-slate-300">{{ item.category_label ?? categoryLabel(item.category) }}</td>
                <td class="px-3 py-2 text-slate-300">{{ item.producer }}</td>
                <td class="px-3 py-2 text-slate-300">{{ item.consumer }}</td>
                <td class="px-3 py-2 text-cyan-100">{{ item.protocol }}</td>
                <td class="px-3 py-2 text-amber-100">{{ item.safety_boundary }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </DashboardCard>

      <DashboardCard title="Message Detail" subtitle="Protocol and failure behavior">
        <template v-if="interfaces.selected">
          <MetricRow label="Endpoint/port" :value="interfaces.selected.endpoint_or_port" />
          <MetricRow label="Direction" :value="interfaces.selected.direction" />
          <MetricRow label="Transport" :value="interfaces.selected.transport" />
          <MetricRow label="Format" :value="interfaces.selected.message_format" />
          <MetricRow label="Update rate" :value="interfaces.selected.update_rate" />
          <MetricRow label="Failure behavior" :value="interfaces.selected.failure_behavior" />
          <MetricRow label="Verification" :value="interfaces.selected.verification_method" />
          <div class="mt-3 flex flex-wrap gap-1.5">
            <StatusBadge v-for="field in interfaces.selected.data_fields" :key="field" :label="field" tone="neutral" />
          </div>
        </template>
        <p v-else class="text-sm text-slate-400">Select an interface to inspect details.</p>
      </DashboardCard>
    </div>

    <DashboardCard title="KTR-ready 4.3 Preview" subtitle="Generated text for interface section">
      <pre class="max-h-[520px] overflow-auto whitespace-pre-wrap rounded-md border border-white/8 bg-black/24 p-4 text-xs leading-6 text-slate-300">{{ interfaces.ktr?.markdown ?? 'KTR section not loaded.' }}</pre>
    </DashboardCard>
  </div>
</template>
