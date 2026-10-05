import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import {
  fetchReportExports,
  fetchReportsStatus,
  generateDemoPack,
  generateKtrSummary,
  generateReadinessPack,
} from '../api/reports'
import type { ReportExportRecord, ReportExportRequest, ReportsStatus } from '../types/reports'

export const useReportsStore = defineStore('reports', () => {
  const status = ref<ReportsStatus>({
    exports_count: 0,
    latest_export: null,
    root_dir: 'exports/reports',
    no_physical_command_generated: true,
  })
  const exportsList = ref<ReportExportRecord[]>([])
  const selectedExport = ref<ReportExportRecord | null>(null)
  const isGenerating = ref(false)
  const error = ref<string | null>(null)
  const latestExport = computed(() => status.value.latest_export ?? exportsList.value[0] ?? null)

  async function refresh(): Promise<void> {
    error.value = null
    try {
      const [nextStatus, nextExports] = await Promise.all([fetchReportsStatus(), fetchReportExports()])
      status.value = nextStatus
      exportsList.value = nextExports
      selectedExport.value ??= nextStatus.latest_export ?? nextExports[0] ?? null
    } catch (caught) {
      error.value = caught instanceof Error ? caught.message : 'Reports refresh failed'
    }
  }

  async function generate(kind: 'ktr' | 'demo' | 'readiness', payload: ReportExportRequest = {}): Promise<void> {
    error.value = null
    isGenerating.value = true
    try {
      const record = kind === 'ktr'
        ? await generateKtrSummary(payload)
        : kind === 'demo'
          ? await generateDemoPack(payload)
          : await generateReadinessPack(payload)
      selectedExport.value = record
      await refresh()
    } catch (caught) {
      error.value = caught instanceof Error ? caught.message : 'Report export failed'
    } finally {
      isGenerating.value = false
    }
  }

  function applyEvent(type: string, payload: unknown): void {
    if (!type.startsWith('report.')) return
    const record = payload as ReportExportRecord
    if (!record.export_id) return
    selectedExport.value = record
    const existing = exportsList.value.findIndex((item) => item.export_id === record.export_id)
    if (existing >= 0) exportsList.value.splice(existing, 1, record)
    else exportsList.value.unshift(record)
    status.value = {
      ...status.value,
      exports_count: Math.max(status.value.exports_count, exportsList.value.length),
      latest_export: record,
    }
  }

  return {
    status,
    exportsList,
    selectedExport,
    isGenerating,
    error,
    latestExport,
    refresh,
    generate,
    applyEvent,
  }
})
