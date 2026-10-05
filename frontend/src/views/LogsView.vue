<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import DashboardCard from '../components/dashboard/DashboardCard.vue'
import { useSystemStore } from '../stores/systemStore'
import { eventSeverity } from '../utils/safetyLabels'
import { exportClientEvents } from '../api/logs'

const store = useSystemStore()
const route = useRoute()
const typeFilter = ref('all')
const severityFilter = ref('all')
const search = ref(typeof route.query.search === 'string' ? route.query.search : '')
const paused = ref(false)
const selectedEvent = ref<(typeof store.latestEvents)[number] | null>(null)
const visibleEvents = ref<typeof store.latestEvents>([])
const exportResult = ref<string | null>(null)
const exportError = ref<string | null>(null)

const eventTypes = computed(() => ['all', ...Array.from(new Set(store.latestEvents.map((event) => event.type))).sort()])
const sourceEvents = computed(() => paused.value ? visibleEvents.value : store.latestEvents)
const filteredEvents = computed(() => {
  return sourceEvents.value.filter((event) => {
    const severity = eventSeverity(event.type)
    const matchesType = typeFilter.value === 'all' || event.type === typeFilter.value
    const matchesSeverity = severityFilter.value === 'all' || severityFilter.value === severity
    const query = search.value.trim().toLowerCase()
    const matchesSearch = query.length === 0 || `${event.type} ${event.summary}`.toLowerCase().includes(query)
    return matchesType && matchesSeverity && matchesSearch
  })
})
const displayedEvents = computed(() => filteredEvents.value)

function pauseLive(): void {
  paused.value = !paused.value
  if (paused.value) visibleEvents.value = filteredEvents.value
}

function clearView(): void {
  visibleEvents.value = []
  paused.value = true
  selectedEvent.value = null
}

function clearFilters(): void {
  typeFilter.value = 'all'
  severityFilter.value = 'all'
  search.value = ''
}

const eventSummary = computed(() => {
  if (!selectedEvent.value) return 'Select an event to inspect details'
  return `${selectedEvent.value.type} #${selectedEvent.value.seq}: ${selectedEvent.value.summary}`
})
const selectedSeverity = computed(() => selectedEvent.value ? eventSeverity(selectedEvent.value.type) : 'info')
const suggestedAction = computed(() => {
  if (!selectedEvent.value) return 'Select an event.'
  if (selectedEvent.value.legacy_format) return 'Legacy readiness log: compare with newer split release/competition/dataset readiness events.'
  if (selectedSeverity.value === 'critical') return 'Inspect backend logs and keep system disarmed.'
  if (selectedSeverity.value === 'warning') return 'Review related subsystem status before demo.'
  return 'No operator action required.'
})

async function exportJson(): Promise<void> {
  exportError.value = null
  try {
    const result = await exportClientEvents(displayedEvents.value)
    exportResult.value = `${result.count} events exported to ${result.path}`
  } catch (caught) {
    exportError.value = caught instanceof Error ? caught.message : 'Export JSONL failed'
  }
}

onMounted(() => {
  if (typeof route.query.type === 'string') typeFilter.value = route.query.type
})

watch(displayedEvents, (events) => {
  if (!selectedEvent.value && events.length > 0) selectedEvent.value = events[0]
})
</script>

<template>
  <div class="grid gap-4">
    <DashboardCard title="Log Controls" subtitle="Client-side event view filters">
      <div class="grid gap-3 lg:grid-cols-[1fr_1fr_1.4fr_auto_auto_auto_auto]">
        <select v-model="typeFilter" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm text-white">
          <option v-for="type in eventTypes" :key="type" :value="type">{{ type }}</option>
        </select>
        <select v-model="severityFilter" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm text-white">
          <option value="all">all severities</option>
          <option value="info">info</option>
          <option value="warning">warning</option>
          <option value="critical">critical</option>
        </select>
        <input v-model="search" placeholder="Search type or summary" class="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm text-white" />
        <button class="focus-ring rounded-md bg-slate-700 px-3 py-2 text-sm font-semibold text-white" @click="pauseLive">{{ paused ? 'Resume live' : 'Pause live' }}</button>
        <button class="focus-ring rounded-md bg-red-500 px-3 py-2 text-sm font-semibold text-white" @click="clearView">Clear view</button>
        <button class="focus-ring rounded-md bg-slate-700 px-3 py-2 text-sm font-semibold text-white" @click="clearFilters">Clear filters</button>
        <button class="focus-ring rounded-md bg-cyan-500 px-3 py-2 text-sm font-semibold text-slate-950" @click="exportJson">Export JSONL</button>
      </div>
      <p class="mt-3 text-xs text-slate-400">Showing {{ displayedEvents.length }} of {{ sourceEvents.length }} events</p>
      <p v-if="exportResult" class="mt-2 rounded-md border border-emerald-400/30 bg-emerald-400/10 px-3 py-2 text-sm text-emerald-100">{{ exportResult }}</p>
      <p v-if="exportError" class="mt-2 rounded-md border border-red-400/30 bg-red-400/10 px-3 py-2 text-sm text-red-100">{{ exportError }}</p>
    </DashboardCard>

    <DashboardCard title="Logs" subtitle="Recent WebSocket events">
      <div class="overflow-x-auto">
      <table class="w-full min-w-[980px] table-fixed text-left text-sm">
        <colgroup>
          <col class="w-[220px]" />
          <col class="w-[120px]" />
          <col />
          <col class="w-[110px]" />
          <col class="w-[180px]" />
        </colgroup>
        <thead class="text-xs uppercase tracking-[0.14em] text-slate-500">
          <tr><th class="py-2 pr-4">Type</th><th class="pr-4">Severity</th><th class="pr-4">Summary</th><th class="pr-4">Seq/ID</th><th>Timestamp</th></tr>
        </thead>
        <tbody>
          <tr v-for="event in displayedEvents" :key="`${event.seq}-${event.type}`" class="cursor-pointer border-t border-white/8 hover:bg-white/5" @click="selectedEvent = event">
            <td class="truncate py-2 pr-4 font-mono text-cyan-200" :title="event.type">{{ event.type }}</td>
            <td class="truncate pr-4 font-mono text-xs uppercase text-slate-500">{{ eventSeverity(event.type) }}</td>
            <td class="pr-4 text-slate-200" :title="event.summary">
              <span v-if="event.legacy_format" class="mr-2 rounded border border-amber-400/40 bg-amber-400/10 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-[0.12em] text-amber-100">LEGACY FORMAT</span>
              <span class="whitespace-normal break-words leading-snug">{{ event.summary }}</span>
            </td>
            <td class="truncate pr-4 font-mono text-slate-500">#{{ event.seq }}</td>
            <td class="truncate font-mono text-xs text-slate-500">{{ new Date(event.ts * 1000).toLocaleTimeString() }}</td>
          </tr>
        </tbody>
      </table>
      <p v-if="displayedEvents.length === 0" class="text-sm text-slate-400">
        {{ sourceEvents.length === 0 ? 'No events yet.' : 'No events match current filters.' }}
      </p>
      </div>
    </DashboardCard>

    <DashboardCard title="Event Detail" subtitle="Selected client-side envelope">
      <template v-if="selectedEvent">
        <div class="rounded-md border border-white/8 bg-black/18 p-3">
          <p class="text-sm font-semibold text-white">{{ eventSummary }}</p>
          <p class="mt-1 text-xs uppercase tracking-[0.16em] text-slate-500">source {{ selectedEvent.type.split('.')[0] }} · severity {{ selectedSeverity }}</p>
          <p v-if="selectedEvent.legacy_format" class="mt-2 inline-flex rounded border border-amber-400/40 bg-amber-400/10 px-2 py-1 text-xs font-semibold uppercase tracking-[0.14em] text-amber-100">
            {{ selectedEvent.format_warning ?? 'OLD READINESS CONTRACT' }}
          </p>
          <p class="mt-2 text-sm text-amber-100">{{ suggestedAction }}</p>
        </div>
        <details class="mt-3 rounded-md border border-white/8 bg-black/18 p-3">
          <summary class="cursor-pointer text-sm font-semibold text-slate-200">Raw JSON</summary>
          <pre class="mt-3 overflow-auto text-xs text-cyan-100">{{ JSON.stringify(selectedEvent, null, 2) }}</pre>
        </details>
      </template>
      <p v-else class="rounded-md border border-white/8 bg-black/18 px-3 py-4 text-sm text-slate-400">
        Select an event to inspect details.
      </p>
    </DashboardCard>
  </div>
</template>
