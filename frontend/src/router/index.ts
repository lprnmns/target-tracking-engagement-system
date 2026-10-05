import { createRouter, createWebHistory } from 'vue-router'
import AppShell from '../components/layout/AppShell.vue'
import LandingView from '../views/LandingView.vue'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      component: AppShell,
      children: [
        // Boot-critical: avoid a stale/missing lazy chunk leaving only the
        // navy document background visible after a one-click deployment.
        { path: '', name: 'landing', component: LandingView },
        { path: 'cockpit', name: 'cockpit', component: () => import('../views/CockpitView.vue') },
        { path: 'cockpit/world', name: 'cockpit-world', component: () => import('../views/CockpitView.vue') },
        { path: 'cockpit/model-calibration', name: 'cockpit-model-calibration', component: () => import('../views/ModelCalibrationView.vue') },
        { path: 'setup', name: 'setup-center', component: () => import('../views/SetupCenterView.vue') },
        { path: 'debug', name: 'debug-center', component: () => import('../views/DebugCenterView.vue') },
        { path: 'evidence', name: 'evidence-hub', component: () => import('../views/EvidenceHubView.vue') },
        { path: 'legacy-console', name: 'competition-console', component: () => import('../views/CompetitionConsoleView.vue') },
        { path: 'dashboard', name: 'dashboard', component: () => import('../views/DashboardView.vue') },
        { path: 'system', name: 'system', component: () => import('../views/SystemView.vue') },
        { path: 'mission-modes', name: 'mission-modes', component: () => import('../views/MissionModesView.vue') },
        { path: 'safety', name: 'safety', component: () => import('../views/SafetyView.vue') },
        { path: 'system-map', name: 'system-map', component: () => import('../views/SystemMapView.vue') },
        { path: 'self-test', name: 'self-test', component: () => import('../views/SelfTestView.vue') },
        { path: 'first-run', name: 'first-run', component: () => import('../views/FirstRunView.vue') },
        { path: 'hardware-wizard', name: 'hardware-wizard', component: () => import('../views/HardwareWizardView.vue') },
        { path: 'pico', name: 'pico', component: () => import('../views/PicoView.vue') },
        { path: 'devices', name: 'devices', component: () => import('../views/DevicesView.vue') },
        { path: 'serial', name: 'serial', component: () => import('../views/SerialView.vue') },
        { path: 'vision', name: 'vision', component: () => import('../views/VisionView.vue') },
        { path: 'models', name: 'models', component: () => import('../views/ModelsView.vue') },
        { path: 'motion', name: 'motion', component: () => import('../views/MotionView.vue') },
        { path: 'calibration', name: 'calibration', component: () => import('../views/CalibrationView.vue') },
        { path: 'ballistics-lab', name: 'ballistics-lab', component: () => import('../views/BallisticsLabView.vue') },
        { path: 'color', name: 'color', component: () => import('../views/ColorView.vue') },
        { path: 'data-lab', name: 'data-lab', component: () => import('../views/DataLabView.vue') },
        { path: 'ktr-evidence', name: 'ktr-evidence', component: () => import('../views/KtrEvidenceCenterView.vue') },
        { path: 'demo', name: 'demo', component: () => import('../views/DemoView.vue') },
        { path: 'reports', name: 'reports', component: () => import('../views/ReportsView.vue') },
        { path: 'interfaces', name: 'interfaces', component: () => import('../views/InterfacesView.vue') },
        { path: 'logs', name: 'logs', component: () => import('../views/LogsView.vue') },
      ],
    },
  ],
})
