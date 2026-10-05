import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { ScriptOut } from '@/api/types'
import { useReviewStore } from '@/stores/review'
import { useRunsStore } from '@/stores/runs'
import { useDocumentsStore } from '@/stores/documents'
import ReviewView from './ReviewView.vue'

let cleanup: (() => void)[] = []
afterEach(() => { cleanup.forEach(fn => fn()); cleanup = []; vi.restoreAllMocks() })
async function editor() {
  const pinia = createPinia()
  setActivePinia(pinia)
  const review = useReviewStore()
  const script: ScriptOut = {
    run_id: 'run', document_id: 'doc', parse_version: 1, language: 'de', word_count: 1,
    format_spec: { id: 'format', name: 'Format', speakers: [], register: 'plain',
      target_minutes: 1, opening: null, closing: null, beats_hint: null },
    segments: [{ id: 'line', ordinal: 0, speaker: 'Host', text: 'Line', kind: 'pedagogy',
      beat_id: null, anchors: [], violations: [], accepted: false, flagged: false,
      edited: false, original_text: null, undoable: true, tags: [], comments: [],
      reaction: 'dislike', slop: false, reaction_comment: 'Saved' }],
  }
  review.script = script
  vi.spyOn(review, 'open').mockResolvedValue(script)
  vi.spyOn(useRunsStore(), 'get').mockResolvedValue({
    id: 'run', document_id: 'doc', document_title: 'Document', flow_id: 'flow',
    flow_version: '1', status: 'completed', created_at: '', started_at: null,
    finished_at: null, error: null, total_cost_usd: 0, target_minutes: null,
    verdict: null, format_spec: null, audience_spec: null, nodes: [], gates: [],
    manifest: null, pause: null,
  })
  vi.spyOn(useDocumentsStore(), 'structure').mockResolvedValue({
    document_id: 'doc', parse_version: 1, language: 'de', page_count: 0,
    page_sizes: [], sections: null, blocks: [], objectives: [], zone_catalogue: [],
  })
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/run/:id', name: 'run', component: { template: '<div />' } },
  ] })
  await router.push('/run/run')
  const wrapper = mount(ReviewView, {
    props: { id: 'run' }, global: { plugins: [pinia, router],
      stubs: { SourceRegister: { template: '<div><slot /></div>' } } },
  })
  cleanup.push(() => wrapper.unmount())
  await flushPromises()
  return { wrapper, review }
}
describe('internal reaction comment drafts', () => {
  it.each(['reaction', 'slop', 'comment'])('preserves input-only drafts during a pending %s refresh', async action => {
    const { wrapper, review } = await editor()
    let release!: () => void
    const pending = new Promise<void>(resolve => { release = resolve })
    const record = vi.spyOn(review, 'record').mockImplementation(async (_id, event) => {
      await pending
      review.script = { ...review.script!, segments: [{ ...review.script!.segments[0],
        reaction: event.text_after as 'dislike', slop: event.text_before === 'slop',
        reaction_comment: event.note ?? null }] }
    })
    if (action === 'reaction') await wrapper.find('[aria-label="Beeindruckt"]').trigger('click')
    else if (action === 'comment') await wrapper.find('.react-extra textarea').setValue('Submitted')
    else await wrapper.find('.react-extra button').trigger('click')
    const textarea = wrapper.find('.react-extra textarea')
    ;(textarea.element as HTMLTextAreaElement).value = 'Unsent draft'
    await textarea.trigger('input')
    release()
    await flushPromises()
    expect((wrapper.find('.react-extra textarea').element as HTMLTextAreaElement).value).toBe('Unsent draft')
    expect(record).toHaveBeenCalledTimes(1)
    await wrapper.find('.react-extra textarea').trigger('change')
    await flushPromises()
    expect(record.mock.calls[1][1].note).toBe('Unsent draft')
  })
  it.each([false, true])('drops cleared comments before remarking (newer draft: %s)', async newer => {
    const { wrapper, review } = await editor()
    let release!: () => void
    let wait: Promise<void> | undefined
    const record = vi.spyOn(review, 'record').mockImplementation(async (_id, event) => {
      if (wait) await wait
      review.script = { ...review.script!, segments: [{ ...review.script!.segments[0],
        reaction: (event.text_after || null) as 'dislike' | null,
        reaction_comment: event.note ?? null }] }
    })
    await wrapper.find('.react-extra textarea').setValue('Old draft')
    await flushPromises()
    wait = new Promise<void>(resolve => { release = resolve })
    await wrapper.find('[aria-label="Mag ich nicht"]').trigger('click')
    await flushPromises()
    if (newer) {
      const textarea = wrapper.find('.react-extra textarea')
      ;(textarea.element as HTMLTextAreaElement).value = 'New draft'
      await textarea.trigger('input')
    }
    release()
    wait = undefined
    await flushPromises()
    expect(wrapper.find('.react-extra textarea').exists()).toBe(false)
    await wrapper.find('[aria-label="Mag ich nicht"]').trigger('click')
    await flushPromises()
    expect(record.mock.calls[2][1].note).toBe(newer ? 'New draft' : null)
    expect((wrapper.find('.react-extra textarea').element as HTMLTextAreaElement).value).toBe(newer ? 'New draft' : '')
  })
})
