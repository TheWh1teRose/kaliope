/**
 * Experiment pages, by the key the backend registry uses.
 *
 * Each experiment is coded on its own: one backend module in
 * `backend/app/experiments/` and one view here. An experiment without a view
 * still appears in the collection but cannot be opened.
 */
import type { Component } from 'vue'

export const experimentViews: Record<string, () => Promise<Component>> = {
  direct_style: () => import('@/experiments/direct_style/DirectStyleView.vue'),
  verbalized_sampling: () =>
    import('@/experiments/verbalized_sampling/VerbalizedSamplingView.vue'),
}
