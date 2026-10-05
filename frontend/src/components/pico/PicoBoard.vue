<script setup lang="ts">
import { computed } from 'vue'
import type { PinAssignment } from '../../types/pico'

const props = defineProps<{
  pins: PinAssignment[]
  selectedPinName: string | null
  invalidPins: Set<string>
}>()

const emit = defineEmits<{
  select: [pinName: string]
}>()

const leftPins = computed(() => props.pins.slice(0, Math.ceil(props.pins.length / 2)))
const rightPins = computed(() => props.pins.slice(Math.ceil(props.pins.length / 2)))

function y(index: number): number {
  return 44 + index * 24
}

function colorFor(pin: PinAssignment): string {
  if (props.invalidPins.has(pin.pin_name)) return '#ef4444'
  if (pin.function === 'ESTOP_IN') return '#dc2626'
  if (pin.function === 'TRIGGER_SERVO_PWM') return '#f97316'
  if (['PAN_STEP', 'PAN_DIR', 'TILT_STEP', 'TILT_DIR', 'DRIVER_ENABLE'].includes(pin.function)) return '#38bdf8'
  if (pin.function === 'UNUSED') return '#64748b'
  if (pin.mode === 'PWM') return '#f59e0b'
  if (pin.mode === 'UART') return '#a78bfa'
  if (pin.direction === 'IN') return '#22c55e'
  return '#38bdf8'
}

function badgeFor(pin: PinAssignment): string | null {
  if (pin.function === 'ESTOP_IN') return 'CRIT'
  if (pin.function === 'TRIGGER_SERVO_PWM') return 'PWM'
  if (['PAN_STEP', 'PAN_DIR', 'TILT_STEP', 'TILT_DIR'].includes(pin.function)) return 'MOTOR'
  if (pin.function.startsWith('LIMIT_')) return 'LIMIT'
  return null
}

function criticalLabel(pin: PinAssignment): string {
  if (pin.function === 'ESTOP_IN') return 'Safety critical E-stop input'
  if (pin.function === 'TRIGGER_SERVO_PWM') return 'Trigger servo PWM-capable output'
  if (['PAN_STEP', 'PAN_DIR', 'TILT_STEP', 'TILT_DIR', 'DRIVER_ENABLE'].includes(pin.function)) return 'Motion control pin'
  if (pin.function.startsWith('LIMIT_')) return 'Safety limit switch input'
  return 'General GPIO assignment'
}
</script>

<template>
  <section class="rounded-md border border-white/10 bg-[#14181d] p-4">
    <div class="mb-4">
      <h3 class="text-base font-semibold text-white">Pico 2 Pinout Preview</h3>
      <p class="mt-1 text-xs text-slate-400">Click a pin to edit its preview assignment</p>
    </div>

    <svg viewBox="0 0 560 380" role="img" class="h-auto w-full max-w-3xl">
      <rect x="134" y="22" width="292" height="334" rx="8" fill="#12313b" stroke="#38bdf8" stroke-opacity="0.35" />
      <rect x="232" y="46" width="96" height="40" rx="4" fill="#0f172a" stroke="#94a3b8" stroke-opacity="0.35" />
      <text x="280" y="70" text-anchor="middle" fill="#dbeafe" font-size="14" font-weight="700">PICO 2</text>
      <rect x="242" y="148" width="76" height="84" rx="6" fill="#1f2937" stroke="#64748b" stroke-opacity="0.7" />
      <text x="280" y="194" text-anchor="middle" fill="#cbd5e1" font-size="11">RP2350</text>

      <g v-for="(pin, index) in leftPins" :key="pin.pin_name">
        <line :x1="134" :y1="y(index)" :x2="102" :y2="y(index)" stroke="#475569" />
        <circle
          :cx="134"
          :cy="y(index)"
          r="8"
          :fill="colorFor(pin)"
          :stroke="selectedPinName === pin.pin_name ? '#ffffff' : '#0f172a'"
          stroke-width="3"
          class="cursor-pointer"
          @click="emit('select', pin.pin_name)"
        />
        <text x="92" :y="y(index) + 4" text-anchor="end" fill="#cbd5e1" font-size="11">{{ pin.pin_name }}</text>
        <text x="150" :y="y(index) + 4" fill="#94a3b8" font-size="9">{{ pin.function }}</text>
        <text v-if="badgeFor(pin)" x="150" :y="y(index) - 7" fill="#fbbf24" font-size="8" font-weight="700">{{ badgeFor(pin) }}</text>
        <title>{{ pin.pin_name }} · {{ pin.function }} · {{ criticalLabel(pin) }} · PWM={{ pin.pwm_capable }} · UART={{ pin.uart_capable }}</title>
      </g>

      <g v-for="(pin, index) in rightPins" :key="pin.pin_name">
        <line :x1="426" :y1="y(index)" :x2="458" :y2="y(index)" stroke="#475569" />
        <circle
          :cx="426"
          :cy="y(index)"
          r="8"
          :fill="colorFor(pin)"
          :stroke="selectedPinName === pin.pin_name ? '#ffffff' : '#0f172a'"
          stroke-width="3"
          class="cursor-pointer"
          @click="emit('select', pin.pin_name)"
        />
        <text x="468" :y="y(index) + 4" fill="#cbd5e1" font-size="11">{{ pin.pin_name }}</text>
        <text x="410" :y="y(index) + 4" text-anchor="end" fill="#94a3b8" font-size="9">{{ pin.function }}</text>
        <text v-if="badgeFor(pin)" x="410" :y="y(index) - 7" text-anchor="end" fill="#fbbf24" font-size="8" font-weight="700">{{ badgeFor(pin) }}</text>
        <title>{{ pin.pin_name }} · {{ pin.function }} · {{ criticalLabel(pin) }} · PWM={{ pin.pwm_capable }} · UART={{ pin.uart_capable }}</title>
      </g>
    </svg>

    <div class="mt-3 flex flex-wrap gap-2">
      <span class="rounded-md border border-red-400/35 bg-red-400/10 px-2 py-1 text-xs font-semibold text-red-200">ESTOP critical</span>
      <span class="rounded-md border border-orange-400/35 bg-orange-400/10 px-2 py-1 text-xs font-semibold text-orange-200">Trigger PWM</span>
      <span class="rounded-md border border-cyan-400/35 bg-cyan-400/10 px-2 py-1 text-xs font-semibold text-cyan-200">STEP/DIR motor group</span>
      <span class="rounded-md border border-emerald-400/35 bg-emerald-400/10 px-2 py-1 text-xs font-semibold text-emerald-200">Limit/input safety</span>
    </div>
  </section>
</template>
