import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import SharedContent from './SharedContent.vue'
import { rememberedLabel, rememberLabel, sharingApi, type FeedbackState, type SharedSnapshot } from '@/api/sharing'

const empty: FeedbackState = { label: null, stars: null, worked: null, did_not: null, marks: [] }
const snapshot: SharedSnapshot = {
  title: 'Review', created_at: '', expires_at: '',
  episodes: [{ index: 1, title: 'Episode', audio: null,
    pages: [0, 1].map(page => ({ page, url: `/page/${page}`, width: 100, height: 100 })),
    segments: [{ ordinal: 0, speaker: 'Host', text: 'Line', citations: [{ rects: [
      { page: 0, bbox: [10, 10, 20, 20] }, { page: 1, bbox: [30, 30, 40, 40] },
    ] }] }],
  }],
}
function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: Error) => void
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}
let cleanup: (() => void)[] = []
afterEach(() => { cleanup.forEach(fn => fn()); cleanup = []; vi.restoreAllMocks(); rememberLabel('') })
function reader() {
  const wrapper = mount(SharedContent, {
    props: { snapshot, selected: 1, token: 'token' },
    global: { stubs: { teleport: true } },
  })
  cleanup.push(() => wrapper.unmount())
  return wrapper
}
describe('feedback persistence', () => {
  it('keeps an explicitly cleared remembered label empty after repeated submissions', async () => {
    rememberLabel('Lena')
    vi.spyOn(sharingApi, 'feedback').mockResolvedValue(empty)
    const save = vi.spyOn(sharingApi, 'saveSheet').mockResolvedValue(empty)
    const wrapper = reader()
    await flushPromises()
    await wrapper.find('.head button').trigger('click')
    expect((wrapper.find('#share-label').element as HTMLInputElement).value).toBe('Lena')
    await wrapper.find('#share-label').setValue('')
    await wrapper.find('.panel__foot .btn--primary').trigger('click')
    await flushPromises()
    expect((wrapper.find('#share-label').element as HTMLInputElement).value).toBe('')
    expect(rememberedLabel()).toBe('')
    await wrapper.find('.panel__foot .btn--primary').trigger('click')
    await flushPromises()
    expect(save.mock.calls.map(call => call[1].label)).toEqual([null, null])
  })
  it.each(['reaction', 'slop', 'comment', 'rollback'])('preserves input-only line drafts across a pending %s save', async action => {
    const marked: FeedbackState = { ...empty, marks: [{ episode: 1, ordinal: 0,
      reaction: 'dislike', slop: false, comment: 'Saved' }] }
    vi.spyOn(sharingApi, 'feedback').mockResolvedValue(action === 'reaction' ? empty : marked)
    const pending = deferred<FeedbackState>()
    const save = vi.spyOn(sharingApi, 'saveMark').mockReturnValueOnce(pending.promise).mockResolvedValue(marked)
    const wrapper = reader()
    await flushPromises()
    if (action === 'reaction') await wrapper.find('[aria-label="Mag ich nicht"]').trigger('click')
    else if (action === 'comment') await wrapper.find('.segment textarea').setValue('Submitted')
    else await wrapper.findAll('button').find(button => button.text() === 'Klingt nach KI-Slop')!.trigger('click')
    const textarea = wrapper.find('.segment textarea')
    ;(textarea.element as HTMLTextAreaElement).value = 'Unsent draft'
    await textarea.trigger('input')
    if (action === 'rollback') pending.reject(new Error('failed'))
    else pending.resolve(marked)
    await flushPromises()
    expect((wrapper.find('.segment textarea').element as HTMLTextAreaElement).value).toBe('Unsent draft')
    expect(save).toHaveBeenCalledTimes(1)
    await wrapper.find('.segment textarea').trigger('change')
    await flushPromises()
    expect(save.mock.calls[1][1].comment).toBe('Unsent draft')
  })
  it('discards the old line draft when a reaction is explicitly cleared', async () => {
    const marked: FeedbackState = { ...empty, marks: [{ episode: 1, ordinal: 0,
      reaction: 'impressed', slop: false, comment: 'Saved' }] }
    vi.spyOn(sharingApi, 'feedback').mockResolvedValue(marked)
    const save = vi.spyOn(sharingApi, 'saveMark').mockResolvedValueOnce(empty).mockResolvedValue(marked)
    const wrapper = reader()
    await flushPromises()
    const textarea = wrapper.find('.segment textarea')
    ;(textarea.element as HTMLTextAreaElement).value = 'Old draft'
    await textarea.trigger('input')
    await wrapper.find('[aria-label="Beeindruckt"]').trigger('click')
    await wrapper.find('[aria-label="Beeindruckt"]').trigger('click')
    await flushPromises()
    expect(save.mock.calls[1][1].comment).toBe(null)
  })
  it('retains a multi-page citation while navigating forward and back', async () => {
    vi.spyOn(sharingApi, 'feedback').mockResolvedValue(empty)
    const wrapper = reader()
    await flushPromises()
    await wrapper.find('.cites button').trigger('click')
    expect(wrapper.find('rect.mark').attributes('x')).toBe('8.5')
    await wrapper.findAll('.source-bar button')[1].trigger('click')
    expect(wrapper.find('rect.mark').attributes('x')).toBe('28.5')
    await wrapper.findAll('.source-bar button')[0].trigger('click')
    expect(wrapper.find('rect.mark').attributes('x')).toBe('8.5')
  })
  it('preserves questionnaire drafts across initial load, marks and pending submission', async () => {
    const load = deferred<FeedbackState>()
    const sheet = deferred<FeedbackState>()
    vi.spyOn(sharingApi, 'feedback').mockReturnValue(load.promise)
    vi.spyOn(sharingApi, 'saveMark').mockResolvedValue({ ...empty, marks: [
      { episode: 1, ordinal: 0, reaction: 'impressed', slop: false, comment: null },
    ] })
    vi.spyOn(sharingApi, 'saveSheet').mockReturnValue(sheet.promise)
    const wrapper = reader()
    await wrapper.find('.head button').trigger('click')
    await wrapper.find('#share-label').setValue('Reviewer')
    await wrapper.find('#share-worked').setValue('Draft')
    await wrapper.find('#share-did-not').setValue('Other draft')
    await wrapper.findAll('.star')[3].trigger('click')
    load.resolve(empty)
    await flushPromises()
    expect((wrapper.find('#share-worked').element as HTMLTextAreaElement).value).toBe('Draft')
    await wrapper.find('.panel__head button').trigger('click')
    await wrapper.find('[aria-label="Beeindruckt"]').trigger('click')
    await flushPromises()
    await wrapper.find('.head button').trigger('click')
    expect((wrapper.find('#share-label').element as HTMLInputElement).value).toBe('Reviewer')
    expect((wrapper.find('#share-did-not').element as HTMLTextAreaElement).value).toBe('Other draft')
    await wrapper.find('.panel__foot .btn--primary').trigger('click')
    await flushPromises()
    expect(sharingApi.saveSheet).toHaveBeenCalledWith('token', {
      label: 'Reviewer', stars: 4, worked: 'Draft', did_not: 'Other draft',
    })
    await wrapper.find('#share-worked').setValue('New draft')
    sheet.resolve({ ...empty, label: 'Reviewer', stars: 4, worked: 'Draft', did_not: 'Other draft' })
    await flushPromises()
    expect((wrapper.find('#share-worked').element as HTMLTextAreaElement).value).toBe('New draft')
    expect(wrapper.find('[aria-label="Beeindruckt"]').attributes('aria-pressed')).toBe('true')
  })
  it.each([false, true])('orders rapid mark then clear, including failed earlier writes (%s)', async fail => {
    vi.spyOn(sharingApi, 'feedback').mockResolvedValue(empty)
    const first = deferred<FeedbackState>()
    const save = vi.spyOn(sharingApi, 'saveMark').mockReturnValueOnce(first.promise).mockResolvedValue(empty)
    const wrapper = reader()
    await flushPromises()
    const button = wrapper.find('[aria-label="Beeindruckt"]')
    await button.trigger('click')
    await button.trigger('click')
    await flushPromises()
    expect(save).toHaveBeenCalledTimes(1)
    expect(button.attributes('aria-pressed')).toBe('false')
    if (fail) first.reject(new Error('failed'))
    else first.resolve({ ...empty, marks: [{ episode: 1, ordinal: 0, reaction: 'impressed', slop: false, comment: null }] })
    await flushPromises()
    expect(save).toHaveBeenCalledTimes(2)
    expect(save.mock.calls[1][1].reaction).toBe(null)
    expect(button.attributes('aria-pressed')).toBe('false')
  })
})
