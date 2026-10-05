import {
  createRouter,
  createWebHistory,
  type RouterHistory,
  type RouteRecordRaw,
} from 'vue-router'

import { useAuthStore } from '@/stores/auth'
import { useCatalogueStore } from '@/stores/catalogue'

export const routes: RouteRecordRaw[] = [
  {
    path: '/r/:token',
    name: 'public-review',
    component: () => import('@/views/PublicReviewView.vue'),
    props: true,
    meta: { publicReader: true },
  },
  { path: '/', redirect: { name: 'documents' } },
  {
    path: '/login',
    name: 'login',
    component: () => import('@/views/LoginView.vue'),
    meta: { public: true },
  },
  { path: '/documents', name: 'documents', component: () => import('@/views/DocumentsView.vue') },
  {
    path: '/documents/:id',
    name: 'document',
    component: () => import('@/views/DocumentDetailView.vue'),
    props: true,
  },
  {
    path: '/documents/:id/runs/new',
    name: 'new-run',
    component: () => import('@/views/NewRunView.vue'),
    props: true,
  },
  { path: '/runs', name: 'runs', component: () => import('@/views/RunsView.vue') },
  {
    path: '/runs/:id',
    name: 'run',
    component: () => import('@/views/RunDetailView.vue'),
    props: true,
  },
  {
    path: '/series/:id',
    name: 'series',
    component: () => import('@/views/SeriesView.vue'),
    props: true,
  },
  {
    path: '/runs/:id/review',
    name: 'review',
    component: () => import('@/views/ReviewView.vue'),
    props: true,
  },
  {
    path: '/runs/:id/feedback',
    name: 'feedback',
    component: () => import('@/views/FeedbackView.vue'),
    props: true,
  },
  {
    path: '/runs/:id/gates',
    name: 'run-gates',
    component: () => import('@/views/GatesView.vue'),
    props: true,
  },
  // The catalogue used to be its own page; bookmarks land on the pipelines tab.
  {
    path: '/gates',
    redirect: (to) => ({ name: 'pipelines', query: { ...to.query, tab: 'gates' } }),
  },
  {
    path: '/pipelines',
    name: 'pipelines',
    component: () => import('@/views/PipelinesView.vue'),
  },
  {
    path: '/pipelines/:id',
    name: 'pipeline',
    component: () => import('@/views/PipelineEditorView.vue'),
    props: true,
  },
  {
    path: '/formats/:id',
    name: 'format',
    component: () => import('@/views/FormatEditorView.vue'),
    props: true,
  },
  {
    path: '/settings',
    name: 'settings',
    component: () => import('@/views/SettingsView.vue'),
  },
  // Export used to be its own menu entry; it now lives in the settings.
  {
    path: '/exports',
    name: 'exports',
    redirect: (to) => ({ name: 'settings', query: { ...to.query, tab: 'export' } }),
  },
  {
    path: '/experiments',
    name: 'experiments',
    component: () => import('@/views/ExperimentsView.vue'),
  },
  {
    path: '/experiments/bench',
    name: 'bench',
    component: () => import('@/views/BenchView.vue'),
  },
  {
    path: '/experiments/compare',
    name: 'experiment-compare',
    component: () => import('@/views/CompareView.vue'),
  },
  {
    path: '/experiments/:key',
    name: 'experiment',
    component: () => import('@/views/ExperimentView.vue'),
    // `key` is reserved on components, so the param arrives as `experimentKey`.
    props: (route) => ({ experimentKey: String(route.params.key) }),
  },
  // The workbench used to live here; old links and bookmarks keep their query.
  { path: '/bench', redirect: (to) => ({ name: 'bench', query: to.query }) },
  { path: '/:pathMatch(.*)*', redirect: { name: 'documents' } },
]

export function createAppRouter(history: RouterHistory = createWebHistory()) {
  const router = createRouter({
    history,
    routes,
  })

  router.beforeEach(async (to) => {
    if (to.meta.publicReader) return true
    const auth = useAuthStore()
    if (!auth.checked) await auth.refresh()
    if (to.meta.public) {
      return auth.user ? { name: 'documents' } : true
    }
    if (!auth.user) return { name: 'login', query: { next: to.fullPath } }
    await useCatalogueStore().load().catch(() => undefined)
    return true
  })

  return router
}

export const router = createAppRouter()
