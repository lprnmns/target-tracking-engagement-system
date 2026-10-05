<script setup lang="ts">
import { computed, onMounted } from 'vue'
import DashboardCard from '../components/dashboard/DashboardCard.vue'
import MetricRow from '../components/dashboard/MetricRow.vue'
import StatusBadge from '../components/shared/StatusBadge.vue'
import { useDemoStore } from '../stores/demoStore'
import { useReportsStore } from '../stores/reportsStore'
import { useDataLabStore } from '../stores/dataLabStore'
import { useReleaseStore } from '../stores/releaseStore'

const demo = useDemoStore()
const reports = useReportsStore()
const dataLab = useDataLabStore()
const release = useReleaseStore()
const verdict = computed(() => demo.timeline.verdict)
const latestReport = computed(() => reports.latestExport)
const latestSession = computed(() => dataLab.dataLabSessions[0] ?? null)
const latestReplay = computed(() => dataLab.dataLabReplay)
const latestAnnotation = computed(() => dataLab.annotationCandidates[0] ?? null)
const knownLimitations = [
  'Production YOLO modeli henüz yüklenmedi.',
  'Gerçek laptop/USB kamera kanıtı henüz alınmadı.',
  'Pico telemetry doğrulaması henüz yapılmadı.',
  'Self-test current state tamamlanmadan competition readiness geçmez.',
  'Mock/surrogate evidence yalnızca release/demo kanıtıdır, yarışma/prod kanıtı değildir.',
]
const evidenceFiles = [
  'demo_timeline.md/json',
  'demo_readiness_summary.md',
  'demo_runbook.md',
  'data_lab_summary.md',
  'data_lab_sessions.json',
  'replay_summary.md',
  'annotation_review_summary.md',
  'dataset_health_summary.md',
  'safety_summary.md',
  'launcher_inspection.md',
  'interface_inventory.md',
  'ktr_4_3_interfaces.md',
]

function tone(status: string): 'good' | 'warn' | 'bad' | 'neutral' {
  if (status === 'completed') return 'good'
  if (status === 'blocked') return 'bad'
  if (status === 'warning') return 'warn'
  return 'neutral'
}

onMounted(() => {
  void demo.refresh()
  void reports.refresh()
  void dataLab.refresh()
  void release.refresh()
})
</script>

<template>
  <div class="grid gap-4">
    <div class="rounded-md border border-amber-400/30 bg-amber-400/10 px-4 py-3 text-sm text-amber-100">
      Jury Demo Center combines existing evidence only. It does not enable hardware, motion or fire. Every critical card keeps advisory / no physical command evidence visible.
    </div>

    <section class="grid gap-4 xl:grid-cols-3">
      <DashboardCard title="Jury Demo Center" subtitle="One-glance demo status">
        <div class="mb-3 flex flex-wrap gap-2">
          <StatusBadge :label="verdict.release_demo_ready ? 'RELEASE DEMO READY' : 'RELEASE DEMO NOT READY'" :tone="verdict.release_demo_ready ? 'good' : 'warn'" />
          <StatusBadge :label="verdict.competition_ready ? 'COMPETITION READY' : 'COMPETITION BLOCKED'" :tone="verdict.competition_ready ? 'good' : 'bad'" />
          <StatusBadge label="NO PHYSICAL COMMAND" tone="bad" />
        </div>
        <MetricRow label="Demo status" :value="demo.timeline.status" />
        <MetricRow label="Latest demo timeline" :value="demo.timeline.run_id" />
        <MetricRow label="Latest report export" :value="latestReport?.export_id ?? demo.timeline.report_export_id ?? 'not generated'" />
        <MetricRow label="Latest Data Lab session" :value="latestSession?.session_id ?? 'not available'" />
        <MetricRow label="Latest replay" :value="latestReplay?.replay_id ?? 'not available'" />
        <MetricRow label="Latest annotation review" :value="latestAnnotation ? `${latestAnnotation.review_status} · ${latestAnnotation.candidate_id}` : 'foundation ready'" />
        <MetricRow label="Safety invariant" value="DISARMED + NO_FIRE + dry_run=true + hardware_enabled=false + physical_command_enabled=false" />
        <MetricRow label="No physical command" :value="demo.timeline.no_physical_command_generated" />
        <button class="mt-4 rounded-md bg-cyan-500 px-3 py-2 text-sm font-semibold text-slate-950 disabled:cursor-not-allowed disabled:opacity-60" :disabled="demo.isRunning" @click="demo.run">
          {{ demo.isRunning ? 'Running full demo evidence...' : 'Run full demo evidence' }}
        </button>
      </DashboardCard>

      <DashboardCard title="Demo Verdict" subtitle="Split readiness semantics">
        <MetricRow label="release_demo_ready" :value="verdict.release_demo_ready" />
        <MetricRow label="release_demo_blockers" :value="verdict.release_demo_blockers.length" />
        <MetricRow label="release_demo_warnings" :value="verdict.release_demo_warnings.length" />
        <MetricRow label="competition_ready" :value="verdict.competition_ready" />
        <MetricRow label="competition_blockers" :value="verdict.competition_blockers.length" />
        <MetricRow label="Dataset ready for training" :value="verdict.dataset_ready_for_training" />
        <MetricRow label="dataset_blockers" :value="verdict.dataset_blockers.length" />
        <MetricRow label="no_physical_command_generated" :value="demo.timeline.no_physical_command_generated" />
      </DashboardCard>

      <DashboardCard title="Readiness Reasons" subtitle="Why competition remains blocked">
        <div class="grid gap-3">
          <div>
            <p class="mb-2 text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">Release demo warnings</p>
            <p v-for="reason in verdict.release_demo_warnings" :key="reason" class="mb-2 rounded-md border border-amber-400/25 bg-amber-400/10 p-2 text-sm text-amber-100">{{ reason }}</p>
            <p v-if="verdict.release_demo_warnings.length === 0" class="text-sm text-slate-400">none</p>
          </div>
          <div>
            <p class="mb-2 text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">Release demo blockers</p>
            <p v-for="reason in verdict.release_demo_blockers" :key="reason" class="mb-2 rounded-md border border-red-400/25 bg-red-400/10 p-2 text-sm text-red-100">{{ reason }}</p>
            <p v-if="verdict.release_demo_blockers.length === 0" class="text-sm text-slate-400">none</p>
          </div>
          <div>
            <p class="mb-2 text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">Competition blockers</p>
            <p v-for="reason in verdict.competition_blockers" :key="reason" class="mb-2 rounded-md border border-red-400/25 bg-red-400/10 p-2 text-sm text-red-100">{{ reason }}</p>
            <p v-if="verdict.competition_blockers.length === 0" class="text-sm text-slate-400">none</p>
          </div>
          <div>
            <p class="mb-2 text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">Dataset blockers</p>
            <p v-for="reason in verdict.dataset_blockers" :key="reason" class="mb-2 rounded-md border border-red-400/25 bg-red-400/10 p-2 text-sm text-red-100">{{ reason }}</p>
            <p v-if="verdict.dataset_blockers.length === 0" class="text-sm text-slate-400">none</p>
          </div>
        </div>
      </DashboardCard>

    </section>

    <section class="grid gap-4 xl:grid-cols-2">
      <DashboardCard title="Known Limitations" subtitle="Why competition/prod is still blocked">
        <div class="grid gap-2">
          <div v-for="item in knownLimitations" :key="item" class="grid gap-2 rounded-md border border-amber-400/25 bg-amber-400/10 p-3 text-sm sm:grid-cols-[minmax(0,1fr)_max-content]">
            <p class="min-w-0 whitespace-normal break-words text-amber-100">{{ item }}</p>
            <p class="self-start whitespace-nowrap rounded border border-amber-300/30 bg-black/20 px-2 py-1 text-xs font-semibold uppercase tracking-[0.12em] text-amber-100">
              competition blocker / demo limitation
            </p>
          </div>
        </div>
        <div class="mt-3 flex flex-wrap gap-2">
          <StatusBadge label="RELEASE/DEMO EVIDENCE ONLY" tone="warn" />
          <StatusBadge label="NOT COMPETITION READY" tone="bad" />
          <StatusBadge label="NO PHYSICAL COMMAND" tone="bad" />
        </div>
      </DashboardCard>

      <DashboardCard title="Evidence Index" subtitle="Latest demo package files">
        <div class="grid gap-2 sm:grid-cols-2">
          <p v-for="file in evidenceFiles" :key="file" class="rounded-md border border-white/8 bg-black/20 px-3 py-2 font-mono text-xs text-cyan-100">{{ file }}</p>
        </div>
        <MetricRow class="mt-3" label="Latest export" :value="latestReport?.export_id ?? 'not generated'" />
        <MetricRow label="no_physical_command_generated" :value="latestReport?.no_physical_command_generated ?? true" />
      </DashboardCard>
    </section>

    <DashboardCard title="Portable Release Package" subtitle="Demo/evidence ZIP, not competition-ready">
      <div class="grid gap-3 md:grid-cols-3">
        <MetricRow label="Latest package" :value="release.latestPackage?.package_id ?? 'not generated'" />
        <MetricRow label="Output dir" :value="release.latestPackage?.output_dir ?? 'not generated'" />
        <MetricRow label="ZIP path" :value="release.latestPackage?.zip_path ?? 'not generated'" />
        <MetricRow label="Source commit" :value="release.latestPackage?.source_commit ?? 'not generated'" />
        <MetricRow label="Package workflow commit" :value="release.latestPackage?.package_workflow_commit ?? 'not generated'" />
        <MetricRow label="Report/docs commit" :value="release.latestPackage?.report_commit ?? 'not generated'" />
        <MetricRow label="Files count" :value="release.latestPackage?.files_count ?? 0" />
        <MetricRow label="Checksum status" :value="release.latestPackage?.checksum_status ?? 'not generated'" />
        <MetricRow label="Release demo ready" :value="release.latestPackage?.release_demo_ready ?? verdict.release_demo_ready" />
        <MetricRow label="Competition ready" :value="release.latestPackage?.competition_ready ?? false" />
        <MetricRow label="no_physical_command_generated" :value="release.latestPackage?.no_physical_command_generated ?? true" />
      </div>
      <div class="mt-3 flex flex-wrap items-center gap-2">
        <StatusBadge label="DEMO/EVIDENCE ZIP ONLY" tone="warn" />
        <StatusBadge label="NOT COMPETITION READY" tone="bad" />
        <StatusBadge label="NO PHYSICAL COMMAND" tone="bad" />
        <button class="rounded-md bg-cyan-500 px-3 py-2 text-sm font-semibold text-slate-950 disabled:cursor-not-allowed disabled:opacity-60" :disabled="release.isBuildingPackage" @click="release.buildPackage">
          {{ release.isBuildingPackage ? 'Building package...' : 'Build portable release package' }}
        </button>
      </div>
    </DashboardCard>

    <section class="grid gap-4 xl:grid-cols-2">
      <DashboardCard title="Clean-room Verification" subtitle="ZIP extracted and smoked outside repo">
        <div class="grid gap-3 md:grid-cols-2">
          <MetricRow label="Latest clean-room run" :value="release.latestCleanroom?.run_id ?? 'not run'" />
          <MetricRow label="Extract path" :value="release.latestCleanroom?.extract_path ?? 'not available'" />
          <MetricRow label="Smoke status" :value="release.latestCleanroom?.smoke_status ?? 'not run'" />
          <MetricRow label="Endpoints passed" :value="release.latestCleanroom ? `${release.latestCleanroom.endpoints_passed}/${release.latestCleanroom.endpoints_total}` : '0/0'" />
          <MetricRow label="Release demo ready" :value="release.latestCleanroom?.release_demo_ready ?? false" />
          <MetricRow label="Competition ready" :value="release.latestCleanroom?.competition_ready ?? false" />
          <MetricRow label="no_physical_command_generated" :value="release.latestCleanroom?.no_physical_command_generated ?? true" />
        </div>
        <div class="mt-3 flex flex-wrap items-center gap-2">
          <StatusBadge label="CLEAN-ROOM EVIDENCE" tone="good" />
          <StatusBadge label="NOT COMPETITION READY" tone="bad" />
          <button class="rounded-md bg-cyan-500 px-3 py-2 text-sm font-semibold text-slate-950 disabled:cursor-not-allowed disabled:opacity-60" :disabled="release.isRunningCleanroom" @click="release.runCleanroomVerification">
            {{ release.isRunningCleanroom ? 'Running clean-room...' : 'Run clean-room verification' }}
          </button>
        </div>
      </DashboardCard>

      <DashboardCard title="Jury Rehearsal" subtitle="Presentation flow evidence package">
        <div class="grid gap-3 md:grid-cols-2">
          <MetricRow label="Latest rehearsal" :value="demo.juryRehearsal?.rehearsal_id ?? 'not run'" />
          <MetricRow label="Clean-room verified" :value="demo.juryRehearsal?.cleanroom_verified ?? false" />
          <MetricRow label="Release demo ready" :value="demo.juryRehearsal?.verdict?.release_demo_ready ?? false" />
          <MetricRow label="Competition ready" :value="demo.juryRehearsal?.verdict?.competition_ready ?? false" />
          <MetricRow label="Dataset ready" :value="demo.juryRehearsal?.verdict?.dataset_ready_for_training ?? false" />
          <MetricRow label="no_physical_command_generated" :value="demo.juryRehearsal?.no_physical_command_generated ?? true" />
        </div>
        <div class="mt-3 flex flex-wrap items-center gap-2">
          <StatusBadge label="JURY REHEARSAL ONLY" tone="warn" />
          <StatusBadge label="NO PHYSICAL COMMAND" tone="bad" />
          <button class="rounded-md bg-cyan-500 px-3 py-2 text-sm font-semibold text-slate-950 disabled:cursor-not-allowed disabled:opacity-60" :disabled="demo.isRunningJury" @click="demo.runJury">
            {{ demo.isRunningJury ? 'Running rehearsal...' : 'Run jury rehearsal' }}
          </button>
        </div>
      </DashboardCard>
    </section>

    <DashboardCard title="Demo Evidence Timeline" subtitle="Jury/operator evidence sequence">
      <div class="grid gap-3">
        <div v-for="event in demo.timeline.events" :key="event.event_id" class="rounded-md border border-white/10 bg-black/20 p-4">
          <div class="flex flex-wrap items-start justify-between gap-3">
            <div>
              <p class="text-lg font-semibold text-white">{{ event.title }}</p>
              <p class="mt-1 text-sm text-slate-400">{{ event.summary }}</p>
            </div>
            <div class="flex flex-wrap gap-2">
              <StatusBadge :label="event.status.toUpperCase()" :tone="tone(event.status)" />
              <StatusBadge :label="event.source.toUpperCase()" tone="neutral" />
              <StatusBadge label="NO PHYSICAL COMMAND" tone="bad" />
            </div>
          </div>
          <div class="mt-3 grid gap-2 text-sm md:grid-cols-3">
            <MetricRow label="Step" :value="event.step" />
            <MetricRow label="Evidence ref" :value="event.evidence_ref ?? 'runtime metadata'" />
            <MetricRow label="Timestamp" :value="new Date(event.timestamp * 1000).toLocaleString()" />
          </div>
        </div>
        <p v-if="demo.timeline.events.length === 0" class="text-sm text-slate-400">No demo timeline generated yet.</p>
      </div>
    </DashboardCard>
  </div>
</template>
