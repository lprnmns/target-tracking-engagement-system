<script setup lang="ts">
import {
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogOverlay,
  DialogPortal,
  DialogRoot,
  DialogTitle,
  TabsContent,
  TabsList,
  TabsRoot,
  TabsTrigger,
} from 'reka-ui'
import { Camera, Crosshair, FileClock, Move3d, SlidersHorizontal, X } from '@lucide/vue'

const props = defineProps<{ active: string }>()
const emit = defineEmits<{ change: [tab: string], close: [] }>()

const tabs = [
  { id: 'camera', label: 'Kamera', icon: Camera },
  { id: 'detection', label: 'Algılama', icon: Crosshair },
  { id: 'motion', label: 'Hareket', icon: Move3d },
  { id: 'calibration', label: 'Kalibrasyon', icon: SlidersHorizontal },
  { id: 'logs', label: 'Kayıtlar', icon: FileClock },
]
</script>

<template>
  <DialogRoot :open="true" :modal="false" @update:open="value => !value && emit('close')">
    <DialogPortal>
      <DialogOverlay class="drawer-overlay" />
      <DialogContent class="drawer-content">
        <header class="drawer-header">
          <div><p>MÜHENDİS ÇALIŞMA ALANI</p><DialogTitle>Mühendis Paneli</DialogTitle><DialogDescription>Donanım, algılama ve kalibrasyon ayrıntıları</DialogDescription></div>
          <DialogClose class="close-button" aria-label="Mühendis panelini kapat"><X :size="19" /></DialogClose>
        </header>

        <TabsRoot :model-value="props.active" class="drawer-tabs" @update:model-value="value => emit('change', String(value))">
          <TabsList class="tabs-list" aria-label="Mühendis teknik sekmeleri">
            <TabsTrigger v-for="tab in tabs" :key="tab.id" :value="tab.id" class="tab-trigger"><component :is="tab.icon" :size="16" /><span>{{ tab.label }}</span></TabsTrigger>
          </TabsList>
          <div class="tabs-viewport">
            <TabsContent v-for="tab in tabs" :key="tab.id" :value="tab.id" class="tab-content"><slot :name="tab.id" /></TabsContent>
          </div>
        </TabsRoot>
      </DialogContent>
    </DialogPortal>
  </DialogRoot>
</template>

<style scoped>
.drawer-content{position:fixed;z-index:61;top:10px;right:10px;bottom:10px;display:grid;grid-template-rows:auto minmax(0,1fr);width:min(580px,42vw);min-width:480px;overflow:hidden;border:1px solid rgba(83,222,246,.28);border-radius:18px;background:linear-gradient(180deg,rgba(8,24,39,.98) 0%,rgba(4,12,23,.98) 100%);color:#e8f7ff;box-shadow:-18px 0 55px rgba(0,0,0,.38);animation:drawer-in .2s cubic-bezier(.22,.8,.25,1)}.drawer-header{display:flex;align-items:flex-start;justify-content:space-between;gap:16px;padding:18px 18px 14px;border-bottom:1px solid rgba(148,163,184,.13)}.drawer-header p{margin:0;color:#5ee5fb;font-size:.61rem;font-weight:900;letter-spacing:.2em}.drawer-header :deep(h2){margin:5px 0 0;font-size:1.22rem}.drawer-header :deep(p){margin:4px 0 0;color:#90a7b8;font-size:.72rem;letter-spacing:0}.close-button{display:grid;place-items:center;width:36px;height:36px;border:1px solid rgba(148,163,184,.2);border-radius:10px;background:rgba(15,23,42,.75);color:#d8e8f2;cursor:pointer}.close-button:hover{border-color:rgba(94,234,255,.5);color:#6ee7f9}.drawer-tabs{display:grid;grid-template-rows:auto minmax(0,1fr);min-height:0}.tabs-list{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:6px;padding:10px 12px;border-bottom:1px solid rgba(148,163,184,.1)}.tab-trigger{display:flex;align-items:center;justify-content:center;gap:6px;min-width:0;border:1px solid transparent;border-radius:9px;background:transparent;color:#8097aa;padding:9px 6px;font-size:.67rem;font-weight:850;cursor:pointer}.tab-trigger:hover{background:rgba(8,47,73,.42);color:#c7f5ff}.tab-trigger[data-state='active']{border-color:rgba(94,234,255,.32);background:rgba(8,74,99,.54);color:#cffafe}.tabs-viewport{min-height:0;overflow:auto;padding:12px}.tab-content{outline:none}.tab-content[data-state='inactive']{display:none}.tab-content :deep(.operator-card),.tab-content :deep(.cockpit-card){min-height:0}@keyframes drawer-in{from{opacity:0;transform:translateX(24px)}to{opacity:1;transform:none}}@media(max-width:900px){.drawer-content{width:min(520px,calc(100vw - 20px));min-width:0}}@media(max-width:560px){.drawer-content{inset:0;width:auto;border-radius:0}.tabs-list{grid-template-columns:repeat(5,1fr)}.tab-trigger span{display:none}}
.drawer-overlay{position:fixed;z-index:60;inset:0;background:rgba(1,6,14,.42);backdrop-filter:blur(2px)}
</style>
