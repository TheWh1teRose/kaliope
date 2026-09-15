import { createRouter, createWebHistory } from 'vue-router'

import { useAuthStore } from '@/stores/auth'
import { useCatalogueStore } from '@/stores/catalogue'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
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
    { path: '/gates', name: 'gates', component: () => import('@/views/GatesView.vue') },
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
    { path: '/exports', name: 'exports', component: () => import('@/views/ExportsView.vue') },
    { path: '/bench', name: 'bench', component: () => import('@/views/BenchView.vue') },
    { path: '/:pathMatch(.*)*', redirect: { name: 'documents' } },
  ],
})

router.beforeEach(async (to) => {
  const auth = useAuthStore()
  if (!auth.checked) await auth.refresh()
  if (to.meta.public) {
    return auth.user ? { name: 'documents' } : true
  }
  if (!auth.user) return { name: 'login', query: { next: to.fullPath } }
  await useCatalogueStore().load().catch(() => undefined)
  return true
})
